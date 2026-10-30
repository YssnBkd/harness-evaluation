"""Grade a code review (REVIEW.md) against a task's [[expect]] findings.

A finding is a line `path:line: text` (or `path:start-end: text`, list bullets allowed). An expected finding is
matched when a reported finding names the same file, overlaps its `lines` range (±3 lines of slack), and, if `any`
is given, mentions one of those words. Score = share of expected findings matched. Passing also requires that the
review is not padded: at most max(3, 2 × expected) findings that match nothing.
"""
import re
from pathlib import Path

FINDING = re.compile(r"^[\s>*+-]*`?([\w./-]+\.\w+)`?:(\d+)(?:-(\d+))?`?\W+(.+)$")
SLACK = 3


def parse(text):
    found = []
    for line in text.splitlines():
        m = FINDING.match(line)
        if m:
            lo = int(m.group(2))
            found.append((m.group(1), lo, int(m.group(3) or lo), m.group(4).lower()))
    return found


def matches(f, e):
    path, lo, hi, text = f
    if not (path == e["file"] or path.endswith("/" + e["file"]) or e["file"].endswith("/" + path)):
        return False
    if "lines" in e:
        elo, ehi = e["lines"][0], e["lines"][-1]
        if hi < elo - SLACK or lo > ehi + SLACK:
            return False
    return not e.get("any") or any(w.lower() in text for w in e["any"])


def score_review(task, review_path):
    p = Path(review_path)
    if not p.exists():
        return 0.0, "no REVIEW.md was written"
    found = parse(p.read_text(errors="replace"))
    hit = [e for e in task.expect if any(matches(f, e) for f in found)]
    stray = [f for f in found if not any(matches(f, e) for e in task.expect)]
    score = len(hit) / len(task.expect)
    padded = len(stray) > max(3, 2 * len(task.expect))
    note = f"found {len(hit)}/{len(task.expect)} expected issues, {len(stray)} other findings"
    if padded:
        note += " (too many unrelated findings: not a pass)"
        score = min(score, 0.99)
    return score, note
