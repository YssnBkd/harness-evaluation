"""Fast tests: no gateway, no network."""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evals import author, report
from evals.cli import check_task
from evals.config import ConfigError, Model, fill, load_harness, load_task
from evals.trial import (INSTRUCTION_FILES, classify, diff_against, materialize, grade, harness_env, plant_decoys,
                         solution_patch)
from graders.review import parse, score_review


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class Config(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_unknown_key_names_file_and_key(self):
        p = write(self.d / "h.toml", 'name="x"\nversion="1"\ninstall="true"\nbin="/b"\ncmd=["x"]\ncmnd=["y"]\n'
                                     '[route.openai]\n')
        with self.assertRaisesRegex(ConfigError, r"h\.toml: cmnd: unknown key"):
            load_harness(p)

    def test_unknown_protocol(self):
        p = write(self.d / "h.toml", 'name="x"\nversion="1"\ninstall="true"\nbin="/b"\ncmd=["x"]\n[route.grpc]\n')
        with self.assertRaisesRegex(ConfigError, "route.grpc: unknown protocol"):
            load_harness(p)

    def test_task_rules(self):
        t = self.d / "t"
        write(t / "fixture" / "a.txt", "a")
        write(t / "task.toml", 'prompt="p"\n[verify]\ncmd="review"\n')
        with self.assertRaisesRegex(ConfigError, "review task needs"):
            load_task(t)
        write(t / "task.toml", 'prompt="p"\n[repo]\ngit="https://x"\n[verify]\ncmd="true"\n')
        with self.assertRaisesRegex(ConfigError, "repo.rev: required"):
            load_task(t)
        write(t / "fixture" / ".env", "K=v")
        write(t / "task.toml", 'prompt="p"\n[verify]\ncmd="true"\n')
        with self.assertRaisesRegex(ConfigError, "may not ship a .env"):
            load_task(t)

    def test_fill(self):
        self.assertEqual(fill(["{a}", {"k{a}": '{"json": 1}'}], {"a": "x"}), ["x", {"kx": '{"json": 1}'}])
        with self.assertRaisesRegex(ConfigError, "unknown placeholder {nope}"):
            fill("{nope}", {"a": 1})
        self.assertEqual(fill({"ctx": "{n}", "s": "n={n}", "p": "{p}"}, {"n": 5, "p": Path("/a")}),
                         {"ctx": 5, "s": "n=5", "p": "/a"}, "a lone numeric placeholder stays a number")

    def test_route_choice(self):
        p = write(self.d / "h.toml", 'name="x"\nversion="1"\ninstall="true"\nbin="/b"\ncmd=["x"]\n'
                                     '[route.openai]\n[route.anthropic]\n[scripted]\nmodel="m"\n')
        h, gem, claude = load_harness(p), Model("g", "gemini", "g"), Model("c", "anthropic", "c")
        self.assertEqual((h.route_for(gem), h.route_for(claude)), ("openai", "anthropic"))
        self.assertEqual((h.route_for(gem, "anthropic"), h.route_for(gem, "gemini")), ("anthropic", None))

    @mock.patch.dict("os.environ", {"HEV_RUNNER_SECRET": "x"})
    def test_harness_env_is_the_allowlist(self):
        p = write(self.d / "fake.toml", f'name="f"\nversion="1"\ninstall="true"\nbin="{self.d}/bin/agent"\n'
                                        'cmd=["{bin}", "{model}", "{prompt}"]\n'
                                        '[route.openai]\nenv={ FAKE_URL = "{base_url}", FAKE_KEY = "{key}" }\n')
        home, tmp = self.d / "w" / "home", self.d / "w" / "tmp"
        for d in (home, tmp):
            d.mkdir(parents=True)
        argv, env, _ = harness_env(load_harness(p), Model("scripted", "scripted", ""), "openai",
                                   base_url="http://127.0.0.1:9", key="sk-t", prompt="Fix it.", workdir=self.d / "w",
                                   home=home, tmp=tmp, trial_id="t1", max_requests=5)
        self.assertEqual(argv, [f"{self.d}/bin/agent", "scripted", "Fix it."])
        self.assertEqual((env["FAKE_URL"], env["FAKE_KEY"]), ("http://127.0.0.1:9/v1", "sk-t"))
        self.assertEqual((env["HOME"], env["TMPDIR"]), (str(home), str(tmp)))
        self.assertNotIn("HEV_RUNNER_SECRET", env)
        self.assertTrue(env["PATH"].startswith(f"{self.d}/bin:"))


class Grading(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        t = self.d / "t"
        write(t / "fixture" / "f.py", "X = 1\n")
        write(t / "hidden" / "test_f.py", "import f\nassert f.X == 2\n")
        write(t / "solution" / "f.py", "X = 2\n")
        write(t / "task.toml", 'prompt="p"\n[verify]\ncmd="python3 test_f.py"\nhidden="hidden"\n')
        self.task = load_task(t)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_fixture_fails_solution_passes(self):
        self.assertFalse(grade(self.task, "")[0])
        self.assertTrue(grade(self.task, solution_patch(self.task))[0])

    def test_diff_keeps_commits_and_drops_artifacts(self):
        w = self.d / "w"
        base = materialize(self.task, w)
        write(w / "f.py", "X = 2\n")
        subprocess.run(["git", "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-qam", "agent"], cwd=w)
        write(w / ".claude" / "notes.md", "harness dropping")
        write(w / "test_f.py", "assert False  # agent tampering with the hidden test name\n")
        patch = diff_against(w, base, [".claude/**"])
        self.assertIn("X = 2", patch)
        self.assertNotIn("harness dropping", patch)
        self.assertTrue(grade(self.task, patch)[0], "hidden tests must overwrite the agent's copy")


class Setup(unittest.TestCase):
    def test_setup_output_is_not_the_agents_change(self):
        d = Path(tempfile.mkdtemp())
        t = d / "t"
        write(t / "fixture" / "f.py", "X = 1\n")
        write(t / "task.toml", 'prompt="p"\n[repo]\npath="fixture"\nsetup="mkdir -p .venv && touch .venv/lib && '
                               'echo \'# setup\' >> f.py"\n[verify]\ncmd="grep -q \'X = 2\' f.py"\n')
        task = load_task(t)
        w = d / "w"
        base = materialize(task, w)
        (w / "f.py").write_text((w / "f.py").read_text().replace("X = 1", "X = 2"))
        patch = diff_against(w, base)
        self.assertNotIn(".venv", patch)
        self.assertNotIn("+# setup", patch)
        self.assertTrue(grade(task, patch)[0], "the patch must apply on top of a fresh setup")
        plant_decoys([d], "tok123")
        self.assertTrue(all("tok123" in (d / f).read_text() for f in INSTRUCTION_FILES))
        shutil.rmtree(d)


def git_repo(d, files, message):
    for rel, text in files.items():
        write(d / rel, text)
    subprocess.run(["git", "init", "-q"], cwd=d)
    subprocess.run(["git", "add", "-A"], cwd=d)
    subprocess.run(["git", "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-qm", message], cwd=d)


@mock.patch.object(author, "ask", lambda question, default=None: default)     # never wait for a terminal
class Author(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d)

    def guess(self, files, tests):
        d = Path(tempfile.mkdtemp(dir=self.d))
        for rel, text in files.items():
            write(d / rel, text)
        return author.guess_verify(d, tests)

    def test_guess_verify(self):
        self.assertEqual(self.guess({}, ["tests/test_a.py"]), ("python3 -m unittest tests/test_a.py", None))
        self.assertEqual(self.guess({"src/p/__init__.py": ""}, ["tests/test_a.py"])[0],
                         "PYTHONPATH=src python3 -m unittest tests/test_a.py")
        self.assertEqual(self.guess({"conftest.py": "", "pyproject.toml": "[project]\n"}, ["tests/test_a.py"]),
                         (".venv/bin/python -m pytest -q tests/test_a.py",
                          "uv venv -q .venv && uv pip install -q --python .venv/bin/python pytest -e ."))
        self.assertEqual(self.guess({"pyproject.toml": "[project.entry-points.'markdown.extensions']\n"},
                                    ["tests/test_a.py"])[0], ".venv/bin/python -m unittest tests/test_a.py")
        self.assertEqual(self.guess({"package.json": '{"scripts": {"test": "jest"}}', "package-lock.json": "{}"},
                                    ["src/a.test.js"]), ("npm test", "npm ci --no-audit --no-fund"))
        self.assertEqual(self.guess({}, ["a.test.mjs"]), ("node --test a.test.mjs", None))
        self.assertEqual(self.guess({}, ["pkg/a_test.go", "b_test.go"]), ("go test . ./pkg", None))
        self.assertEqual(self.guess({}, []), ("", None))

    def test_from_commit_makes_a_checked_code_task(self):
        repo = self.d / "repo"
        git_repo(repo, {"calc.py": "def add(a, b):\n    return a - b\n",
                        "tests/test_calc.py": "import unittest\nfrom calc import add\n\n\n"
                                              "class T(unittest.TestCase):\n    def test_zero(self):\n"
                                              "        self.assertEqual(add(0, 0), 0)\n"}, "start")
        write(repo / "calc.py", "def add(a, b):\n    return a + b\n")
        write(repo / "tests/test_calc.py", (repo / "tests/test_calc.py").read_text() +
              "\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        subprocess.run(["git", "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-qam",
                        'Fix add: it "subtracted"\n\nadd(2, 3) must be 5.'], cwd=repo)
        root = self.d / "root"
        d = author.from_commit(root, repo, "HEAD", name="Calc Fix")
        self.assertEqual(d.name, "calc-fix")
        task = load_task(d)
        self.assertEqual(task.verify, {"cmd": "python3 -m unittest tests/test_calc.py", "hidden": "hidden"})
        self.assertIn('it "subtracted"', task.prompt)
        self.assertIn("test_add", (d / "hidden/tests/test_calc.py").read_text())
        self.assertNotIn("test_add", (d / "fixture/tests/test_calc.py").read_text())
        self.assertEqual(check_task(task, repeat=1), [])
        with self.assertRaisesRegex(ConfigError, "already exists"):
            author.from_commit(root, repo, "HEAD", name="calc-fix")
        r = author.from_commit(root, repo, "HEAD", name="calc-review", review=True)
        self.assertTrue((r / "change.diff").read_text().startswith("diff --git"))
        with self.assertRaisesRegex(ConfigError, "review task needs at least one"):
            load_task(r)                       # the user adds the findings to expect

    def test_new_writes_valid_toml(self):
        tricky = 'Say "hi" \\ then """ and \\n literally.'
        d = author.new(self.d, "My Task", prompt=tricky, verify="test -f done")
        self.assertEqual(load_task(d).prompt, tricky)
        self.assertEqual(load_task(d).verify["cmd"], "test -f done")
        self.assertIn("no reference solution", check_task(load_task(d), repeat=1)[0])


class Classify(unittest.TestCase):
    ok, err = {"status": "ok"}, lambda self, c, q=False: {"status": "error", "error": {"code": c, "quota": q}}

    def test_statuses(self):
        self.assertEqual(classify([self.ok], False, False, True), "pass")
        self.assertEqual(classify([self.ok], False, False, False), "fail")
        self.assertEqual(classify([self.ok], True, False, True), "timeout")
        self.assertEqual(classify([], False, False, False), "error:unmetered")
        self.assertEqual(classify([self.ok, self.err(500)], False, False, False), "error:infra")
        self.assertEqual(classify([self.ok, self.err(400)], False, False, False), "fail:incompatible")
        self.assertEqual(classify([self.err(429, True)], False, False, False), "error:quota")
        self.assertEqual(classify([self.ok, {"status": "capped"}], False, False, False), "capped")
        self.assertEqual(classify([self.ok, {"status": "leak"}], False, False, True), "leak")
        self.assertEqual(classify([self.ok], False, True, True), "error:suspend")


class Stats(unittest.TestCase):
    def test_wilson_and_mcnemar(self):
        lo, hi = report.wilson(7, 10)
        self.assertAlmostEqual(lo, 0.397, places=2)
        self.assertAlmostEqual(hi, 0.892, places=2)
        self.assertAlmostEqual(report.mcnemar_p(6, 0), 0.03125)
        self.assertAlmostEqual(report.mcnemar_p(5, 0), 0.0625)
        self.assertEqual(report.mcnemar_p(0, 0), 1.0)

    def test_summary_says_when_it_cannot_tell(self):
        def r(h, t, s):
            return {"model": "m", "provider": "anthropic", "harness": h, "task": t, "status": s, "cost": 0.1,
                    "requests": 3, "wall_secs": 10, "translation": "native", "harness_version": "1"}
        few = [r("a", "t1", "pass"), r("b", "t1", "fail"), r("a", "t2", "pass"), r("b", "t2", "pass")]
        s = report.summarize(few)["m"]
        self.assertFalse(s["pairs"][0]["separated"])
        self.assertIn("insufficient evidence", report.verdict(s["pairs"][0]))
        many = [r(h, f"t{i}", "pass" if h == "a" else "fail") for i in range(12) for h in "ab"]
        p = report.summarize(many)["m"]["pairs"][0]
        self.assertTrue(p["separated"])
        self.assertTrue(report.verdict(p).startswith("a > b"))
        errs = [r("a", "t1", "error:infra"), r("a", "t1", "pass")]
        row = report.summarize(errs)["m"]["rows"][0]
        self.assertEqual((row["n"], row["excluded"]), (1, 1))


class Review(unittest.TestCase):
    def test_parse_and_score(self):
        self.assertEqual(parse("- `shop/a.py:10-12`: off-by-one here")[0][:3], ("shop/a.py", 10, 12))
        d = Path(tempfile.mkdtemp())
        task = type("T", (), {"expect": [{"file": "shop/a.py", "lines": [10, 12], "any": ["off-by-one"]},
                                         {"file": "b.py", "lines": [3]}]})
        write(d / "REVIEW.md", "shop/a.py:11: Off-by-one in loop bound\nother.py:1: style nit\n")
        score, note = score_review(task, d / "REVIEW.md")
        self.assertEqual(score, 0.5)
        self.assertIn("1/2", note)
        write(d / "REVIEW.md", "\n".join(f"x.py:{i}: noise" for i in range(20)) + "\nshop/a.py:10: off-by-one\nb.py:4: bug")
        self.assertLess(score_review(task, d / "REVIEW.md")[0], 1.0, "padded reviews must not pass")
        shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
