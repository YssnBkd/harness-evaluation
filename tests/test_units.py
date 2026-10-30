"""Fast tests: no gateway, no network."""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evals import report
from evals.config import ConfigError, fill, load_harness, load_task
from evals.trial import classify, diff_against, materialize, grade, solution_patch
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
