"""`hev task`: make a task folder from history (a GitHub PR, a local commit) or from three answers.

Code task: the repo before the change, the change itself as the reference solution, and the test files it touched as
hidden tests. Review task (--review): the change committed on top as "the change under review", and the findings to
expect (from the PR's review comments) for you to prune.
"""
import contextlib, functools, json, re, shutil, subprocess, sys, tarfile, tempfile, time, urllib.error, urllib.request
from collections import Counter
from io import BytesIO
from pathlib import Path

from .config import ConfigError
from .trial import _git_source, git

TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$"
                       r"|\.(test|spec)\.[cm]?[jt]sx?$")
REVIEW_PROMPT = """
Review the change in the last commit (HEAD~1..HEAD) for bugs. Do not modify the code.
Write your findings to REVIEW.md, one per line, as `path:line: what is wrong`, using line numbers of the files as they
are now. Report only real problems, most important first.
"""
CLOSES = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b")


def slug(s):
    return re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-.") or "task"


def tq(s):
    """TOML basic string (JSON's escapes are a subset of TOML's)."""
    return json.dumps(s, ensure_ascii=False)


def tq_long(s):
    return '"""\n' + s.strip().replace("\\", "\\\\").replace('"""', '""\\"') + '\n"""'


def ask(question, default=None):
    """Interactive prompt when there is a terminal; otherwise the default (flags fill in what scripts can't answer)."""
    if not sys.stdin.isatty():
        return default
    hint = f" [{default}]" if default else ""
    return input(f"{question}{hint}: ").strip() or default


@functools.cache
def _gh_ok():
    return bool(shutil.which("gh")) and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0


def api(path):
    """GitHub REST: through `gh` when it is logged in (private repos, higher rate limit), else anonymously."""
    if _gh_ok():
        r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"gh api {path}: {r.stderr.strip()}")
        return json.loads(r.stdout)
    req = urllib.request.Request(f"https://api.github.com/{path}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "hev"})
    try:
        with urllib.request.urlopen(req, timeout=30) as f:
            return json.load(f)
    except urllib.error.HTTPError as e:
        hint = " (anonymous rate limit: log in with `gh auth login`)" if e.code == 403 else ""
        raise RuntimeError(f"GitHub API {path}: HTTP {e.code}{hint}") from None


def tree_at(src, rev, dest):
    """Files of `rev` (no .git) into `dest`."""
    tar = subprocess.run(["git", "archive", rev], cwd=src, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=BytesIO(tar)) as t:
        t.extractall(dest, filter="data")


def guess_verify(root, tests):
    """(verify cmd, repo setup or None) from the repo's layout and the test files the change touched."""
    root, files = Path(root), " ".join(tests)
    has = lambda *names: any((root / n).exists() for n in names)
    text = lambda *names: " ".join((root / n).read_text(errors="replace") for n in names if (root / n).is_file())
    if any(t.endswith("_test.go") for t in tests):
        pkgs = sorted({"./" + str(Path(t).parent) if str(Path(t).parent) != "." else "." for t in tests})
        return "go test " + " ".join(pkgs), None
    if any(re.search(r"\.[cm]?[jt]sx?$", t) for t in tests):
        if has("package.json") and '"test"' in text("package.json"):
            return "npm test", ("npm ci" if has("package-lock.json") else "npm install") + " --no-audit --no-fund"
        return f"node --test {files}", None
    if any(t.endswith(".py") for t in tests):
        conf = text("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "setup.py", "requirements-dev.txt",
                    "requirements.txt", *tests)
        pytest = has("conftest.py", "tests/conftest.py", "pytest.ini") or "pytest" in conf
        # dependencies, or entry points (plugins found by name only once the package is installed)
        deps = re.search(r"(?m)^dependencies\s*=\s*\[\s*['\"]|install_requires\s*=\s*\[\s*['\"]|entry[-_]points", conf)
        if pytest or deps:
            pip = " ".join(["pytest"] * pytest + ["-e ."] * has("pyproject.toml", "setup.py"))
            run = "-m pytest -q" if pytest else "-m unittest"
            return f".venv/bin/python {run} {files}", "uv venv -q .venv && uv pip install -q --python " \
                f".venv/bin/python {pip}"
        return ("PYTHONPATH=src " if has("src") else "") + f"python3 -m unittest {files}", None
    return "", None


def write_toml(dest, header, prompt, repo, verify, hidden=False, expect=(), tags=(), notes=None):
    lines = [f"# {header}", f"prompt = {tq_long(prompt)}"]
    lines += [f"tags = [{', '.join(map(tq, tags))}]"] if tags else []
    lines += [f"notes = {tq(notes)}"] if notes else []
    lines += ["", "[repo]"] + [f"{k} = {tq(v)}" for k, v in repo.items() if v]
    if "setup" not in repo:
        lines.append('# setup = "..."   # optional: install dependencies (runs before the agent, with network)')
    lines += ["", "[verify]"]
    if verify.get("cmd"):
        lines.append(f"cmd = {tq(verify['cmd'])}")
    else:
        lines.append('cmd = "false"   # TODO: a command that fails before the change and passes after it')
    if hidden:
        lines.append('hidden = "hidden"           # copied in only for grading: the agent never sees these files')
    for e in expect:
        lines += ["", f"# reviewer: {e['said']}" if e.get("said") else "", "[[expect]]", f"file = {tq(e['file'])}",
                  f"lines = [{e['lines'][0]}, {e['lines'][-1]}]"]
        lines += [f"any = [{', '.join(map(tq, e['any']))}]"] if e.get("any") else []
    if verify.get("cmd") == "review" and not expect:
        lines += ["", "# A review task needs the findings to expect, e.g.:", "# [[expect]]", '# file = "src/app.py"',
                  "# lines = [10, 14]", '# any = ["off-by-one", "boundary"]   # optional: words a match must mention']
    (dest / "task.toml").write_text("\n".join(l for l in lines if l is not None) + "\n")


@contextlib.contextmanager
def fresh(dest):
    """The new task folder; removed again if making it fails halfway."""
    if dest.exists():
        raise ConfigError(f"{dest}: already exists (pick another name)")
    try:
        dest.mkdir(parents=True)
        yield dest
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def build(dest, src, base, head, *, prompt, repo, header, review, verify=None, expect=(), tags=(), notes=None):
    """Fill the task folder from the change base..head of the git repo `src`."""
    diff = git(src, "diff", "--binary", base, head).stdout
    if not diff.strip():
        raise ConfigError(f"{base[:12]}..{head[:12]} changes nothing")
    if review:
        (dest / "change.diff").write_text(diff)
        (dest / "solution").mkdir()
        (dest / "solution" / "REVIEW.md").write_text(
            "".join(f"{e['file']}:{e['lines'][0]}-{e['lines'][-1]}: {e.get('said') or 'expected finding'}\n"
                    for e in expect))
        write_toml(dest, header, REVIEW_PROMPT, {**repo, "patch": "change.diff"}, {"cmd": "review"},
                   expect=expect, tags=["review", *tags], notes=notes)
        return dest
    changed = git(src, "diff", "--name-only", "--diff-filter=d", base, head).stdout.split()
    tests = [p for p in changed if TEST_FILE.search(p)]
    for p in tests:
        out = dest / "hidden" / p
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(subprocess.run(["git", "show", f"{head}:{p}"], cwd=src, capture_output=True, check=True).stdout)
    (dest / "solution.diff").write_text(diff)
    with tempfile.TemporaryDirectory() as d:
        tree_at(src, base, d)
        cmd, setup = guess_verify(d, tests)
    if not tests:
        print("note: the change touches no test files, so there are no hidden tests: write a check by hand in "
              "verify.cmd (and put any test files it needs in hidden/)")
    cmd = verify or ask("verify command (exit 0 = solved)", cmd) or ""
    write_toml(dest, header, prompt, {**repo, **({"setup": setup} if setup and not verify else {})}, {"cmd": cmd},
               hidden=bool(tests), tags=tags, notes=notes)
    return dest


def clean(text):
    return re.sub(r"<!--.*?-->", "", text or "", flags=re.S).strip()


def header(cmd, todo, name):
    return f"Made by `hev task {cmd}` on {time.strftime('%Y-%m-%d')}. {todo}, then run ./hev check-task {name}"


def _review_findings(owner, repo, n, src, head):
    """The revision the reviewers commented on (fetched if needed), and their comments on it as expected findings."""
    expect, cs = [], [c for c in api(f"repos/{owner}/{repo}/pulls/{n}/comments?per_page=100")
                      if c.get("original_line") and c.get("side", "RIGHT") == "RIGHT"]
    if cs:
        at = Counter(c["original_commit_id"] for c in cs).most_common(1)[0][0]
        if git(src, "cat-file", "-e", f"{at}^{{commit}}", check=False).returncode == 0 or \
                git(src, "fetch", "--quiet", "origin", at, check=False).returncode == 0:
            head = at
        seen = set()
        for c in cs:
            if c["original_commit_id"] != head:
                continue
            lines = [c.get("original_start_line") or c["original_line"], c["original_line"]]
            if (c["path"], *lines) not in seen:
                seen.add((c["path"], *lines))
                said = " ".join(clean(c["body"]).split())
                expect.append({"file": c["path"], "lines": lines, "said": said[:200]})
    print(f"{len(expect)} review comments on {head[:12]} became expected findings: keep only the real problems")
    return head, expect


def from_pr(root, url, name=None, review=False, verify=None):
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not m:
        raise ConfigError(f"{url}: expected a GitHub pull request URL like https://github.com/owner/repo/pull/123")
    owner, repo, n = m.groups()
    dest = Path(root, "tasks", slug(name or f"{repo}-pr{n}"))
    with fresh(dest):
        pr, info = api(f"repos/{owner}/{repo}/pulls/{n}"), api(f"repos/{owner}/{repo}")
        clone, head = pr["base"]["repo"]["clone_url"], pr["head"]["sha"]
        src = _git_source(clone, pr["base"]["sha"])
        git(src, "fetch", "--quiet", "origin", f"pull/{n}/head")
        base = git(src, "merge-base", pr["base"]["sha"], head).stdout.strip()
        licence = (info.get("license") or {}).get("spdx_id") or "unknown"
        print(f"{owner}/{repo}#{n}: {pr['title']!r}, licence {licence}, base {base[:12]}")
        head, expect = _review_findings(owner, repo, n, src, head) if review else (head, [])
        refs = CLOSES.findall(pr["body"] or "") or re.findall(rf"\b{re.escape(owner)}/{re.escape(repo)}#(\d+)\b",
                                                             pr["body"] or "", re.I)
        issue = next((api(f"repos/{owner}/{repo}/issues/{k}") for k in refs[:1]), None)
        src_text = issue or pr
        prompt = f"{src_text['title']}\n\n{clean(src_text['body'])}".strip()
        return build(dest, src, base, head, prompt=prompt, review=review, verify=verify, expect=expect,
                     repo={"git": clone, "rev": base},
                     header=header(f"from-pr {url}", "Edit the prompt, check the verify command", dest.name),
                     notes=f"{url} ({licence})", tags=["from-pr"])


def from_commit(root, path, sha, name=None, review=False, verify=None):
    src = Path(path).resolve()
    head = git(src, "rev-parse", "--verify", f"{sha}^{{commit}}").stdout.strip()
    base = git(src, "rev-parse", f"{head}^").stdout.strip()
    dest = Path(root, "tasks", slug(name or f"{src.name}-{head[:7]}"))
    message = git(src, "log", "-1", "--format=%B", head).stdout.strip()
    with fresh(dest):
        tree_at(src, base, dest / "fixture")       # a copy: private code never leaves the machine
        return build(dest, src, base, head, prompt=message, review=review, verify=verify, repo={"path": "fixture"},
                     header=header(f"from-commit {path} {sha}", "Edit the prompt (it is the commit message)",
                                   dest.name), tags=["from-commit"])


def new(root, name, prompt=None, verify=None, start=None):
    dest = Path(root, "tasks", slug(name))
    with fresh(dest):
        prompt = prompt or ask("What should the agent do? (the prompt it gets)")
        verify = verify or ask("How do we check it's done? (a shell command run in the repo; exit 0 = solved)")
        start = start or ask("Folder with the starting files (empty: start from an empty repo)", "")
        if not prompt:
            raise ConfigError("a task needs a prompt: pass --prompt or answer the question")
        if start:
            shutil.copytree(start, dest / "fixture", ignore=shutil.ignore_patterns(".git"))
        else:
            (dest / "fixture").mkdir()
            (dest / "fixture" / "README.md").write_text(f"# {name}\n")
        write_toml(dest, header("new", "Put the solved files in solution/ (laid over fixture/), tests the agent must "
                                "not see in hidden/", dest.name), prompt, {"path": "fixture"}, {"cmd": verify or ""})
        return dest
