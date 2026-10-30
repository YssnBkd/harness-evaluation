"""Turn results/trials/*/result.json into per-model harness comparisons that say how sure they are."""
import json, math, random, statistics
from collections import defaultdict
from pathlib import Path


def load(results):
    out = []
    for p in sorted(Path(results, "trials").glob("*/result.json")):
        try:
            out.append(json.loads(p.read_text()))
        except ValueError:
            pass
    return out


def valid(r):
    return not r["status"].startswith("error") and r["status"] != "leak"


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p, d = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def mcnemar_p(b, c):
    """Exact two-sided McNemar on discordant tasks (b: only A passed, c: only B passed)."""
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def per_task(rows):
    rates = defaultdict(list)
    for r in rows:
        rates[r["task"]].append(r["status"] == "pass")
    return {t: sum(v) / len(v) for t, v in rates.items()}


def compare(a_rows, b_rows, seed=0, n_boot=4000):
    pa, pb = per_task(a_rows), per_task(b_rows)
    tasks = sorted(set(pa) & set(pb))
    if not tasks:
        return None
    diffs = [pa[t] - pb[t] for t in tasks]
    rng = random.Random(seed)
    boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n_boot))
    lo, hi = boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]
    b = sum(pa[t] == 1 and pb[t] == 0 for t in tasks)
    c = sum(pa[t] == 0 and pb[t] == 1 for t in tasks)
    sd = statistics.pstdev(diffs) if len(diffs) > 1 else 0
    need = math.ceil((1.96 * sd / 0.10) ** 2) if sd else None
    return {"diff": statistics.fmean(diffs), "lo": lo, "hi": hi, "tasks": len(tasks), "p": mcnemar_p(b, c),
            "dropped": len(set(pa) ^ set(pb)), "tasks_needed_for_10pts": need}


def holm(ps):
    order, out, running = sorted(range(len(ps)), key=lambda i: ps[i]), [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, ps[i] * (len(ps) - rank)))
        out[i] = running
    return out


def summarize(results):
    """{model: {"rows": [harness row...], "pairs": [comparison...]}} — what tables and the SVG are drawn from."""
    rs = [r for r in results if r["provider"] != "scripted"] or results
    out = {}
    for model in sorted({r["model"] for r in rs}):
        by_h = defaultdict(list)
        for r in rs:
            if r["model"] == model:
                by_h[r["harness"]].append(r)
        rows = []
        for h, all_rows in by_h.items():
            v = [r for r in all_rows if valid(r)]
            k = sum(r["status"] == "pass" for r in v)
            lo, hi = wilson(k, len(v))
            rows.append({"harness": h, "n": len(v), "pass": k, "rate": k / len(v) if v else 0.0, "lo": lo, "hi": hi,
                         "tasks": len({r["task"] for r in v}), "excluded": len(all_rows) - len(v),
                         "cost_per_trial": statistics.fmean([r["cost"] for r in v]) if v else 0.0,
                         "requests": statistics.median([r["requests"] for r in v]) if v else 0,
                         "wall": statistics.median([r["wall_secs"] for r in v]) if v else 0,
                         "translation": sorted({r["translation"] for r in all_rows}),
                         "versions": sorted({r.get("harness_version", "?") for r in all_rows})})
        rows.sort(key=lambda x: (-x["rate"], x["cost_per_trial"]))
        pairs = []
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                c = compare([r for r in by_h[a["harness"]] if valid(r)], [r for r in by_h[b["harness"]] if valid(r)])
                if c:
                    pairs.append({"a": a["harness"], "b": b["harness"], **c})
        for p, adj in zip(pairs, holm([p["p"] for p in pairs])):
            p["p_holm"] = adj
            p["separated"] = (p["lo"] > 0 or p["hi"] < 0) and adj < 0.05
        out[model] = {"rows": rows, "pairs": pairs}
    return out


def verdict(p):
    pts = lambda x: f"{x * 100:+.0f}"
    if p["separated"]:
        a, b = (p["a"], p["b"]) if p["diff"] > 0 else (p["b"], p["a"])
        return (f"{a} > {b}: {pts(abs(p['diff']))} pts [{pts(min(abs(p['lo']), abs(p['hi'])))}, "
                f"{pts(max(abs(p['lo']), abs(p['hi'])))}], {p['tasks']} tasks")
    need = f"; ≈{p['tasks_needed_for_10pts']} tasks needed to detect 10 pts" if p["tasks_needed_for_10pts"] else ""
    return (f"{p['a']} vs {p['b']}: insufficient evidence ({pts(p['diff'])} pts [{pts(p['lo'])}, {pts(p['hi'])}], "
            f"{p['tasks']} tasks){need}")


def render(summary, md=False):
    lines = []
    for model, s in summary.items():
        lines.append(f"\n## {model}\n" if md else f"\n== {model} ==")
        head = ["harness", "pass", "rate", "95% CI", "$/trial", "requests", "secs", "route", "excluded"]
        body = [[r["harness"], f"{r['pass']}/{r['n']}", f"{r['rate']:.0%}", f"{r['lo']:.0%}–{r['hi']:.0%}",
                 f"{r['cost_per_trial']:.3f}", f"{r['requests']:g}", f"{r['wall']:.0f}", ",".join(r["translation"]),
                 str(r["excluded"])] for r in s["rows"]]
        if md:
            lines += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            lines += ["| " + " | ".join(row) + " |" for row in body]
        else:
            w = [max(len(x) for x in col) for col in zip(head, *body)]
            lines += ["  ".join(x.ljust(n) for x, n in zip(row, w)) for row in [head, *body]]
        lines.append("")
        lines += [("- " if md else "  ") + verdict(p) for p in s["pairs"]] or ["  (one harness: nothing to compare)"]
    return "\n".join(lines)
