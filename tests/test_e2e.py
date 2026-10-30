"""Offline end-to-end: real gateway + scripted model + a real (tiny) agent loop. $0, no API keys. ~1-2 minutes."""
import json, os, shutil, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evals.gateway import litellm_bin

HARNESS = f"""name = "Fake agent (tests)"
version = "1.0"
install = "true"
bin = "{ROOT / 'tests' / 'fake_agent.py'}"
cmd = ["{{bin}}", "{{model}}", "{{prompt}}"]

[route.openai]
env = {{ FAKE_URL = "{{base_url}}", FAKE_KEY = "{{key}}" }}
"""


def run_procs():
    """Processes still carrying a hev trial or gateway marker."""
    n = 0
    for p in Path("/proc").iterdir():
        try:
            env = (p / "environ").read_bytes() if p.name.isdigit() else b""
        except OSError:
            continue
        n += b"\0HEV_RUN=" in env or b"\0HEV_RUN_NONCE=" in env or env.startswith((b"HEV_RUN=", b"HEV_RUN_NONCE="))
    return n


@unittest.skipUnless(Path(litellm_bin()).exists(), "litellm not installed (./hev install --litellm)")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="hev-e2e-"))
        (cls.root / "harnesses").mkdir()
        (cls.root / "harnesses" / "fake.toml").write_text(HARNESS)
        # a harness that quotes the instruction file above its repo: the planted canary must catch it
        (cls.root / "harnesses" / "peek.toml").write_text(HARNESS.replace(
            'cmd = ["{bin}", "{model}", "{prompt}"]',
            'cmd = ["bash", "-c", "exec {bin} {model} \\"$(cat ../AGENTS.md) {prompt}\\""]'))
        (cls.root / "models.toml").write_text('[scripted]\nprovider = "scripted"\n')
        for t in ("00-smoke", "01-fix-slugify", "03-rename-refactor"):
            shutil.copytree(ROOT / "tasks" / t, cls.root / "tasks" / t)
        hang = cls.root / "tasks" / "zz-hang"
        shutil.copytree(ROOT / "tasks" / "00-smoke", hang)
        (hang / "task.toml").write_text('prompt = "Create hello.txt."\ntimeout = 6\n[verify]\ncmd = "test -f hello.txt"\n')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def hev(self, *args, results="results", wait=True):
        cmd = [sys.executable, str(ROOT / "hev"), *args]
        if args[0] == "run":
            cmd += ["--results", str(self.root / results)]
        env = {**os.environ, "HEV_ROOT": str(self.root)}
        if not wait:
            return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)

    def results(self, name):
        return {json.loads(p.read_text())["task"]: json.loads(p.read_text())
                for p in (self.root / name / "trials").glob("*/result.json")}

    def test_1_all_tasks_pass_then_resume(self):
        r = self.hev("run", "-m", "scripted", "-H", "fake", "-t", "0*", "--route", "openai")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        res = self.results("results")
        self.assertEqual({t: x["status"] for t, x in res.items()},
                         {"00-smoke": "pass", "01-fix-slugify": "pass", "03-rename-refactor": "pass"})
        for x in res.values():
            self.assertEqual(x["served_models"], ["hev-scripted"])
            self.assertEqual(x["escaped_processes"], 0)
        again = self.hev("run", "-m", "scripted", "-H", "fake", "-t", "0*", "--route", "openai")
        self.assertIn("nothing to run", again.stdout)

    def test_5_canary_catches_reading_above_the_repo(self):
        r = self.hev("run", "-m", "scripted", "-H", "peek", "-t", "00-smoke", results="results-peek")
        self.assertEqual(self.results("results-peek")["00-smoke"]["status"], "leak", r.stdout)

    def test_2_faults_are_classified(self):
        for fault, want in (("429once", "pass"), ("500", "error:infra"), ("400", "fail:incompatible"),
                            ("quota", "error:quota")):
            r = self.hev("run", "-m", "scripted", "-H", "fake", "-t", "00-smoke", "--fault", fault,
                         results=f"results-{fault}")
            self.assertEqual(self.results(f"results-{fault}")["00-smoke"]["status"], want, fault + r.stdout)

    def test_3_hang_times_out_and_cleans_up(self):
        self.hev("run", "-m", "scripted", "-H", "fake", "-t", "zz-hang", "--fault", "hang", results="results-hang")
        self.assertEqual(self.results("results-hang")["zz-hang"]["status"], "timeout")
        self.assertEqual(run_procs(), 0)

    def test_4_kill_9_leaves_nothing_behind(self):
        p = self.hev("run", "-m", "scripted", "-H", "fake", "-t", "00-smoke", "--fault", "hang",
                     results="results-kill", wait=False)
        for _ in range(60):                   # wait until the harness is running
            time.sleep(0.5)
            if run_procs() >= 2:
                break
        self.assertGreaterEqual(run_procs(), 2, "harness never started")
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
        p.stdout.close()
        for _ in range(20):
            time.sleep(0.5)
            if run_procs() == 0:
                break
        self.assertEqual(run_procs(), 0, "gateway or harness survived kill -9 of hev")
        self.assertFalse(list((self.root / "results-kill" / "trials").glob("*/result.json")),
                         "an interrupted trial must not leave a result (it is re-run on resume)")


if __name__ == "__main__":
    unittest.main()
