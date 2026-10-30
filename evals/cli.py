"""hev: run coding-agent harnesses on your tasks with a fixed model, and compare them."""
import argparse, fnmatch, hashlib, json, os, random, secrets, shutil, signal, subprocess, sys, tempfile, time
from pathlib import Path

from . import author, report
from .config import PROVIDERS, ConfigError, fill, load_harnesses, load_models, load_task, load_tasks
from .gateway import LITELLM_VERSION, Gateway, litellm_bin, load_dotenv
from .trial import INSTRUCTION_FILES, classify, grade, offline_prefix, requests_of, run_trial, solution_patch

HOOKS = Path(__file__).with_name("gateway_hooks.py")
GREEN, RED, YELLOW, DIM, END = ("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m") if sys.stdout.isatty() \
    else ("",) * 5


def pick(names, pattern, kind):
    """Comma-separated names or globs; empty = all."""
    if not pattern:
        return list(names)
    out = []
    for pat in pattern.split(","):
        hit = [n for n in names if fnmatch.fnmatch(n, pat.strip())]
        if not hit:
            raise ConfigError(f"no {kind} matches {pat!r} (have: {', '.join(names)})")
        out += [n for n in hit if n not in out]
    return out


def installed_version(h):
    """First line of the harness's version output, run with a throwaway HOME so nothing of yours is read."""
    vars = {"prefix": h.prefix, "version": h.version}
    vars["bin"] = fill(h.bin, vars)
    if not Path(vars["bin"]).exists():
        return None
    with tempfile.TemporaryDirectory() as home:
        r = subprocess.run(["bash", "-c", fill(h.version_cmd, vars)], capture_output=True, text=True, timeout=60,
                           env={"HOME": home, "PATH": f"{Path(vars['bin']).parent}:/usr/local/bin:/usr/bin:/bin",
                                "TERM": "dumb", **{k: v for k, v in os.environ.items() if k in ("USER", "LANG")}})
    return (r.stdout.strip() or r.stderr.strip()).splitlines()[0] if (r.stdout or r.stderr).strip() else "?"


def run_key(task, model, harness, version, idx, route):
    """Everything that could change a trial's outcome. Change any of it and the trial is re-run; otherwise resumed."""
    blob = json.dumps([task.hash, model.entry(), harness.path.read_text(), version, LITELLM_VERSION,
                       hashlib.sha256(HOOKS.read_bytes()).hexdigest(), task.timeout, task.max_requests,
                       task.max_cost, idx, route])
    return hashlib.sha256(blob.encode()).hexdigest()


def check_ancestors(path):
    bad = [p / f for p in [Path(path), *Path(path).parents] for f in INSTRUCTION_FILES if (p / f).exists()]
    if bad:
        raise ConfigError(f"instruction files above the work folder would leak into some harnesses: "
                          f"{', '.join(map(str, bad))} — remove them or set HEV_WORK to another folder")


def plan_run(a, root):
    """The trials still to run (finished ones are resumed), what was skipped and why, and the installed harnesses."""
    models, harnesses, tasks = load_models(root / "models.toml"), load_harnesses(root), load_tasks(root)
    ms, hs, ts = pick(models, a.models, "model"), pick(harnesses, a.harnesses, "harness"), pick(tasks, a.tasks, "task")
    versions = {h: installed_version(harnesses[h]) for h in hs}
    for h, v in versions.items():
        if v and harnesses[h].version not in v:
            print(f"{YELLOW}warning{END}: {h} reports {v!r}, pinned {harnesses[h].version}")
    skipped = [f"{h}: not installed — run ./hev install {h}" for h in hs if versions[h] is None]
    hs, rng, plan = [h for h in hs if versions[h] is not None], random.Random(a.seed), []
    for idx in range(1, a.trials + 1):
        for t in rng.sample(ts, len(ts)):
            for m in ms:
                for h in rng.sample(hs, len(hs)):          # interleave harnesses so drift hits all of them alike
                    routes = list(harnesses[h].routes) if a.route == "all" \
                        else [harnesses[h].route_for(models[m], a.route)]
                    for route in filter(None, routes):
                        key = run_key(tasks[t], models[m], harnesses[h], versions[h], idx, route)
                        out = Path(a.results) / "trials" / f"{t}__{h}__{m}__{idx}__{key[:8]}"
                        prev = json.loads((out / "result.json").read_text()) if (out / "result.json").exists() else None
                        if not prev or (a.retry_errors and prev["status"].startswith("error")):
                            plan.append((tasks[t], models[m], harnesses[h], versions[h], idx, key, out, route))
    return plan, skipped, hs


def estimate(plan, results):
    """USD: expected spend (past mean cost of the same model × harness, else a third of the cap) and the hard cap."""
    paid, past, est = [p for p in plan if p[1].provider != "scripted"], report.load(results), 0.0
    for t, m, h, *_ in paid:
        seen = [r["cost"] for r in past if r["model"] == m.name and r["harness"] == h.name and r["cost"]]
        est += sum(seen) / len(seen) if seen else t.max_cost / 3
    return est, sum(t.max_cost for t, *_ in paid)


def cmd_run(a, root):
    plan, skipped, hs = plan_run(a, root)
    for s in skipped:
        print(f"{YELLOW}skip{END} {s}")
    if not hs:
        print(f"{RED}no installed harness to run{END}: see ./hev install --help")
        return 1
    if not plan:
        print("nothing to run: every requested trial already has a result (use --retry-errors to redo errors)")
        return 0
    results, paid = Path(a.results), sum(m.provider != "scripted" for _, m, *_ in plan)
    est, cap = estimate(plan, results)
    print(f"{len(plan)} trials ({paid} paid): estimated ${est:.2f}, hard cap ${min(cap, a.budget or cap):.2f}")
    if paid and est > a.confirm_above and not a.yes:
        if not sys.stdin.isatty():
            print("refusing to spend without confirmation: pass --yes")
            return 2
        if input("proceed? [y/N] ").strip().lower() != "y":
            return 1
    nonce, canary = secrets.token_hex(4), a.canary or f"hev-canary-{secrets.token_hex(8)}"
    work = Path(os.environ.get("HEV_WORK") or tempfile.gettempdir()) / "hev" / nonce
    check_ancestors(work.parent)
    work.mkdir(parents=True)
    run_dir, used = results / "gateway" / nonce, {m.name: m for _, m, *_ in plan}
    t_start, patches = time.monotonic(), {}
    try:
        with Gateway(run_dir, used, root, nonce, work, budget=a.budget, canary=canary).start() as gw:
            for m in used.values():
                if m.provider != "scripted" and (err := gw.preflight(m)):
                    print(f"{RED}preflight failed for {m.name}{END} ({m.provider}/{m.id}): {err}")
                    return 2
            for done, (t, m, h, version, idx, key, out, route) in enumerate(plan, 1):
                label = f"[{done}/{len(plan)}] {t.name} {h.name}×{m.name}{f' ({route})' if a.route else ''} #{idx}"
                if f"hev-{m.name}" in gw.dead_models():
                    print(f"{label} {YELLOW}skipped: {m.name} out of quota{END}")
                    continue
                if m.provider == "scripted" and t.name not in patches:
                    patches[t.name] = solution_patch(t) or ""
                r = run_trial(task=t, model=m, harness=h, harness_version=version, idx=idx, trial_id=key[:16],
                              run_nonce=nonce, out=out, gateway=gw, work_root=work,
                              scripted_patch=patches.get(t.name), fault=a.fault, keep=a.keep, route=route,
                              canary=canary)
                color = GREEN if r["status"] == "pass" else RED if not r["status"].startswith("error") else YELLOW
                print(f"{label} → {color}{r['status']}{END} "
                      f"{DIM}({r['wall_secs']:.0f}s, {r['requests']} req, ${r['cost']:.3f}){END}")
    except KeyboardInterrupt:
        print("\ninterrupted: finished trials are saved; run the same command again to resume")
        return 130
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"done in {time.monotonic() - t_start:.0f}s\n")
    if a.route:                                   # a route check: the verdict is "every trial passed"
        rs = [json.loads((out / "result.json").read_text()) for *_, out, _ in plan if (out / "result.json").exists()]
        return 0 if len(rs) == len(plan) and all(r["status"] == "pass" for r in rs) else 1
    summary = report.summarize([r for r in report.load(results) if r["model"] in used and r["harness"] in hs])
    print(report.render(summary))
    return 0


def cmd_report(a, root):
    rs = report.load(a.results)
    if a.json:
        print(json.dumps(report.summarize(rs), indent=1))
    else:
        print(report.render(report.summarize(rs), md=a.md) if rs else "no results yet")
    return 0


def cmd_install(a, root):
    harnesses = load_harnesses(root)
    rc = 0
    for name in pick(harnesses, a.harnesses, "harness"):
        h = harnesses[name]
        if installed_version(h) and not a.force:
            print(f"{name}: already installed in {h.prefix}")
            continue
        h.prefix.mkdir(parents=True, exist_ok=True)
        print(f"{name}: installing {h.version} into {h.prefix}")
        r = subprocess.run(["bash", "-c", fill(h.install, {"prefix": h.prefix, "version": h.version})])
        v = installed_version(h)
        ok = r.returncode == 0 and v and h.version in v
        print(f"{name}: {GREEN + 'ok' if ok else RED + 'FAILED'}{END} ({v})")
        rc |= not ok
    if not Path(litellm_bin()).exists() or a.litellm:
        print(f"litellm: installing {LITELLM_VERSION} with uv")
        subprocess.run(["uv", "tool", "install", "--force", "--compile-bytecode", f"litellm[proxy]=={LITELLM_VERSION}"])
    return rc


def check_task(t, repeat=3):
    """Plain-language problems that would make the task useless; empty list = well-formed."""
    problems = []
    ok, _, note = grade(t, "")
    if ok:
        problems.append("the starting code already passes the check, so this task would be free — "
                        "make the check fail before the change")
    sol = solution_patch(t)
    if sol is None:
        problems.append("no reference solution (add a solution/ folder or solution.diff) — needed to prove "
                        "the task is solvable and for the $0 scripted runs")
    else:
        for i in range(repeat):
            with tempfile.NamedTemporaryFile("r", suffix=".log") as log:
                ok, _, note = grade(t, sol, log.name)
                if not ok:
                    tail = "\n      ".join(log.read().strip().splitlines()[-6:])
                    problems.append(f"the reference solution fails the check (run {i + 1}/{repeat}: {note})"
                                    + (f"; last lines of its output:\n      {tail}" if tail else ""))
                    break
    return problems


def report_check(name, problems):
    print(f"{name}: " + (f"{GREEN}ok{END}" if not problems else f"{RED}problem{END}\n  - " + "\n  - ".join(problems)))
    return bool(problems)


def cmd_check_task(a, root):
    tasks = load_tasks(root)
    rc = 0
    for name in pick(tasks, a.tasks, "task"):
        rc |= report_check(name, check_task(tasks[name], a.repeat))
    return rc


def cmd_task(a, root):
    if a.how == "from-pr":
        d = author.from_pr(root, a.url, a.name, a.review, a.verify)
    elif a.how == "from-commit":
        d = author.from_commit(root, a.repo, a.sha, a.name, a.review, a.verify)
    else:
        d = author.new(root, a.name, a.prompt, a.verify, a.start)
    print(f"created {d.relative_to(root) if d.is_relative_to(root) else d}; checking it ($0)")
    return report_check(d.name, check_task(load_task(d)))


def cmd_regrade(a, root):
    tasks, n = load_tasks(root), 0
    for p in sorted(Path(a.results, "trials").glob("*/result.json")):
        r = json.loads(p.read_text())
        if r["task"] not in tasks or tasks[r["task"]].hash != r["task_hash"]:
            continue
        passed, score, note = grade(tasks[r["task"]], (p.parent / "diff.patch").read_text(), p.parent / "verify.log")
        status = classify(requests_of(p.parent), r["timed_out"], r["status"] == "error:suspend", passed)
        if (status, score) != (r["status"], r["score"]):
            print(f"{p.parent.name}: {r['status']} → {status}")
            r.update(status=status, score=score, note=note)
            p.write_text(json.dumps(r, indent=1))
            n += 1
    print(f"regraded: {n} changed")
    return 0


def cmd_doctor(a, root):
    ok = True

    def check(good, what, fix="", blocking=True):
        nonlocal ok
        ok &= good or not blocking
        mark = GREEN + "✓" if good else RED + "✗" if blocking else YELLOW + "!"
        print(f"  {mark}{END} {what}" + (f"  {DIM}→ {fix}{END}" if fix and not good else ""))

    print("basics")
    check(sys.version_info >= (3, 11), f"python {sys.version.split()[0]}", "needs Python 3.11+")
    check(bool(shutil.which("git")), "git", "install git")
    lv = subprocess.run([litellm_bin(), "--version"], capture_output=True, text=True).stdout \
        if Path(litellm_bin()).exists() else ""
    check(LITELLM_VERSION in lv, f"litellm {LITELLM_VERSION}", "./hev install --litellm")
    check(bool(offline_prefix()), "offline verification (unshare -rn)", "verifiers will run with network access")
    try:
        check_ancestors(Path(os.environ.get("HEV_WORK") or tempfile.gettempdir()))
        check(True, "work folder has no instruction files above it")
    except ConfigError as e:
        check(False, str(e))
    try:
        models, harnesses, tasks = load_models(root / "models.toml"), load_harnesses(root), load_tasks(root)
        check(True, f"config: {len(models)} models, {len(harnesses)} harnesses, {len(tasks)} tasks")
    except ConfigError as e:
        check(False, f"config: {e}")
    if not ok:
        return 1
    env = {**os.environ, **load_dotenv(root / ".env")}
    print("keys (needed for paid models only)")        # keys and harnesses only warn: the $0 smoke test needs no keys
    for prov in sorted({m.provider for m in models.values()} - {"scripted"}):
        k = PROVIDERS[prov]["key_env"]
        check(bool(env.get(k)), f"{prov}: {k}", f"add {k}=... to {root / '.env'}", blocking=False)
    print("harnesses")
    for name, h in harnesses.items():
        v = installed_version(h)
        found = f" (found {v})" if v and h.version not in v else ""
        check(bool(v) and h.version in v, f"{name} {h.version}{found}", f"./hev install {name}", blocking=False)
    for p in ("/etc/claude-code/managed-settings.json", "/etc/gemini-cli/settings.json"):
        if Path(p).exists():
            print(f"  {YELLOW}!{END} {p} exists: machine-wide policy applies to every trial")
    if a.quick:
        return 0
    print("smoke ($0, scripted model, every route of every installed harness, decoy instruction files planted)")
    with tempfile.TemporaryDirectory(prefix="hev-doctor-") as results:
        return cmd_run(parser(root).parse_args(["run", "-m", "scripted", "--route", "all", "--yes", "--retry-errors",
                                                "--results", results, "-t", a.tasks or "00-smoke",
                                                *(["-H", a.harnesses] if a.harnesses else [])]), root)


def parser(root):
    p = argparse.ArgumentParser(prog="hev", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run tasks × models × harnesses")
    r.add_argument("-t", "--tasks", help="task names or globs, comma-separated (default: all)")
    r.add_argument("-m", "--models", required=True, help="model names from models.toml, comma-separated")
    r.add_argument("-H", "--harnesses", help="harness names (default: all installed)")
    r.add_argument("-n", "--trials", type=int, default=1, help="trials per task/model/harness (default 1)")
    r.add_argument("--budget", type=float, help="hard USD cap for the whole run, enforced by the gateway")
    r.add_argument("--yes", action="store_true", help="don't ask before spending")
    r.add_argument("--confirm-above", type=float, default=1.0, help="ask before runs estimated above this (USD)")
    r.add_argument("--retry-errors", action="store_true", help="re-run trials that ended in an infra error")
    r.add_argument("--keep", action="store_true", help="keep trial work folders (debugging)")
    r.add_argument("--seed", type=int, default=0)
    for flag in ("--fault", "--canary", "--route"):   # test hooks: a scripted-model fault, a fixed canary token, and a
        r.add_argument(flag, help=argparse.SUPPRESS)  # forced protocol route ("all": every route the harness declares)
    rep = sub.add_parser("report", help="compare harnesses per model from saved results")
    rep.add_argument("--md", action="store_true")
    rep.add_argument("--json", action="store_true")
    i = sub.add_parser("install", help="install pinned harnesses into ~/.cache/hev")
    i.add_argument("harnesses", nargs="?", help="names or globs (default: all)")
    i.add_argument("--force", action="store_true")
    i.add_argument("--litellm", action="store_true", help="(re)install the pinned LiteLLM gateway")
    d = sub.add_parser("doctor", help="check the setup, then run a $0 scripted smoke test per harness")
    d.add_argument("-H", "--harnesses")
    d.add_argument("-t", "--tasks")
    d.add_argument("--quick", action="store_true", help="skip the smoke test")
    c = sub.add_parser("check-task", help="prove tasks are well-formed: starting code fails, solution passes")
    c.add_argument("tasks", nargs="?")
    c.add_argument("--repeat", type=int, default=3)
    sub.add_parser("regrade", help="re-grade saved trials with the current verifiers ($0)")
    tk = sub.add_parser("task", help="make a task from a GitHub PR, a local commit, or three questions")
    how = tk.add_subparsers(dest="how", required=True)
    pr = how.add_parser("from-pr", help="repo before the PR, PR diff as solution, its test files as hidden tests")
    pr.add_argument("url", help="https://github.com/owner/repo/pull/123")
    cm = how.add_parser("from-commit", help="the same from a local commit (the code is copied, nothing is uploaded)")
    cm.add_argument("repo", help="path to a local git repo")
    cm.add_argument("sha", help="the commit that solves the task")
    for sp in (pr, cm):
        sp.add_argument("--name", help="task folder name (default: from the repo and PR/commit)")
        sp.add_argument("--review", action="store_true", help="make a code-review task of the change instead")
        sp.add_argument("--verify", help="verify command (default: guessed from the repo, asked on a terminal)")
    nw = how.add_parser("new", help="answer three questions, get a commented task folder")
    nw.add_argument("name")
    nw.add_argument("--prompt", help="what the agent should do")
    nw.add_argument("--verify", help="shell command that exits 0 when the task is solved")
    nw.add_argument("--start", help="folder with the starting files")
    for sp in (r, rep, d, sub.choices["regrade"]):
        sp.add_argument("--results", default=str(root / "results"), help=argparse.SUPPRESS)
    return p


def main(argv=None):
    root = Path(os.environ.get("HEV_ROOT") or Path(__file__).resolve().parent.parent)
    a = parser(root).parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)          # progress shows up live in logs and pipes too
    signal.signal(signal.SIGTERM, signal.default_int_handler)    # `kill hev` cleans up exactly like Ctrl-C
    try:
        return {"run": cmd_run, "report": cmd_report, "install": cmd_install, "doctor": cmd_doctor,
                "check-task": cmd_check_task, "regrade": cmd_regrade, "task": cmd_task}[a.cmd](a, root)
    except (ConfigError, RuntimeError) as e:
        print(f"{RED}{'config error' if isinstance(e, ConfigError) else 'error'}{END}: {e}")
        return 2
