"""The local LiteLLM gateway every harness talks to: one per `hev run`, on 127.0.0.1, stopped when the run ends."""
import json, os, secrets, shutil, signal, socket, subprocess, time, urllib.error, urllib.request
from pathlib import Path

from .config import PROVIDERS

LITELLM_VERSION = "1.106.6"


def litellm_bin():
    return shutil.which("litellm") or str(Path.home() / ".local/bin/litellm")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_dotenv(path):
    """KEY=VALUE lines; values never reach harnesses, only the gateway."""
    env = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.split(" #", 1)[0].strip().strip("'\"")
    return env


def deployment(model, scripted_port):
    if model.provider == "scripted":
        return {"model_name": f"hev-{model.name}/*",
                "litellm_params": {"model": "openai/*", "api_base": f"http://127.0.0.1:{scripted_port}/v1",
                                   "api_key": "unused"},
                "model_info": {"input_cost_per_token": 0, "output_cost_per_token": 0}}
    p = PROVIDERS[model.provider]
    info = {k2: model.price[k1] for k1, k2 in (("in", "input_cost_per_token"), ("out", "output_cost_per_token"),
            ("cache_read", "cache_read_input_token_cost"), ("cache_write", "cache_creation_input_token_cost"))
            if k1 in model.price}
    return {"model_name": f"hev-{model.name}",
            "litellm_params": {"model": p["prefix"] + model.id, "api_key": f"os.environ/{p['key_env']}",
                               **model.params},
            "model_info": info}


class Gateway:
    def __init__(self, run_dir, models, root, nonce, work, budget=None, canary=None):
        self.dir = Path(run_dir)
        self.models, self.root, self.nonce, self.budget, self.canary = models, Path(root), nonce, budget, canary
        self.work = work
        self.port, self.scripted_port = free_port(), free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = None

    def start(self, log=print):
        (self.dir / "keys").mkdir(parents=True, exist_ok=True)
        cfg = {"model_list": [deployment(m, self.scripted_port) for m in self.models.values()],
               "litellm_settings": {"drop_params": True, "modify_params": True, "num_retries": 2,
                                    "request_timeout": 1500,
                                    "use_chat_completions_url_for_anthropic_messages": True,
                                    "callbacks": "gateway_hooks.hooks"},
               "general_settings": {"custom_auth": "gateway_hooks.user_api_key_auth"}}
        # JSON is valid YAML; the config lives apart from the hooks so litellm imports them once (shared state)
        (self.dir / "config.yaml").write_text(json.dumps(cfg, indent=1))
        env = {**os.environ, **load_dotenv(self.root / ".env")}
        env.pop("DATABASE_URL", None)
        env.update(HEV_RUN_DIR=str(self.dir), HEV_SCRIPTED_PORT=str(self.scripted_port), HEV_PARENT_PID=str(os.getpid()),
                   HEV_RUN_NONCE=self.nonce, HEV_WORK_DIR=str(self.work),
                   HEV_BUDGET=str(self.budget or ""), HEV_CANARY=self.canary or "",
                   PYTHONPATH=str(Path(__file__).parent), LITELLM_MASTER_KEY="sk-admin-" + secrets.token_hex(16),
                   LITELLM_LOCAL_MODEL_COST_MAP="True", LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS="True",
                   LITELLM_LOCAL_BLOG_POSTS="True")
        need = {PROVIDERS[m.provider]["key_env"] for m in self.models.values() if PROVIDERS[m.provider]["key_env"]}
        if missing := sorted(k for k in need if not env.get(k)):
            raise RuntimeError(f"missing API key(s): {', '.join(missing)} — put them in {self.root / '.env'}")
        logf = open(self.dir / "gateway.log", "ab")
        self.proc = subprocess.Popen([litellm_bin(), "--config", str(self.dir / "config.yaml"), "--host", "127.0.0.1",
                                      "--port", str(self.port)], env=env, stdin=subprocess.DEVNULL, stdout=logf,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        (self.dir / "gateway.pid").write_text(str(self.proc.pid))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 180:
            if self.proc.poll() is not None:
                raise RuntimeError(f"gateway exited at startup; see {self.dir / 'gateway.log'}")
            try:
                urllib.request.urlopen(f"{self.url}/health/liveliness", timeout=1)
                log(f"gateway up on {self.url} ({time.monotonic() - t0:.0f}s)")
                return self
            except OSError:
                time.sleep(0.5)
        self.stop()
        raise RuntimeError(f"gateway did not start within 180s; see {self.dir / 'gateway.log'}")

    def register(self, trial_id, dir, model, max_requests, max_cost, script=None, fault=None):
        key = f"sk-hev-{trial_id}-{secrets.token_hex(4)}"
        t = {"trial": trial_id, "dir": str(dir), "model": f"hev-{model.name}", "provider": model.provider,
             "max_requests": max_requests, "max_cost": max_cost, "script": script, "fault": fault}
        tmp = self.dir / "keys" / f".{key}.tmp"
        tmp.write_text(json.dumps(t))
        tmp.rename(self.dir / "keys" / f"{key}.json")
        return key

    def close(self, key):
        (self.dir / "keys" / f"{key}.json").unlink(missing_ok=True)

    def dead_models(self):
        d = self.dir / "dead"
        return {p.name: p.read_text() for p in d.iterdir()} if d.exists() else {}

    def preflight(self, model):
        """One tiny request per real model: a 401/429/quota fails here, before any trial spends anything."""
        key = self.register(f"preflight-{model.name}", self.dir, model, 3, 0.05)
        req = urllib.request.Request(f"{self.url}/v1/chat/completions", method="POST",
                                     headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                                     data=json.dumps({"model": model.id, "max_tokens": 16,
                                                      "messages": [{"role": "user", "content": "Reply OK."}]}).encode())
        try:
            urllib.request.urlopen(req, timeout=120).read()
            return None
        except urllib.error.HTTPError as e:
            return f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}"
        except OSError as e:
            return str(e)
        finally:
            self.close(key)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        (self.dir / "gateway.pid").unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
