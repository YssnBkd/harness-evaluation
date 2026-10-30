"""Load and validate models.toml, harnesses/*.toml and tasks/*/task.toml.

Every problem is reported as `file: key: problem`, and unknown keys are errors (a typo must not silently
change an experiment).
"""
import hashlib, re, tomllib
from dataclasses import dataclass, field
from pathlib import Path

CACHE = Path.home() / ".cache" / "hev"

# provider -> how the gateway reaches it. `protocol` is what the provider speaks natively.
PROVIDERS = {
    "anthropic": {"prefix": "anthropic/", "key_env": "ANTHROPIC_API_KEY", "protocol": "anthropic"},
    "gemini":    {"prefix": "gemini/",    "key_env": "GEMINI_API_KEY",    "protocol": "gemini"},
    "openai":    {"prefix": "openai/",    "key_env": "OPENAI_API_KEY",    "protocol": "openai"},
    "deepseek":  {"prefix": "deepseek/",  "key_env": "DEEPSEEK_API_KEY",  "protocol": "openai"},
    "scripted":  {"prefix": None,         "key_env": None,                "protocol": "openai"},
}
PROTOCOLS = ("anthropic", "openai", "gemini")


class ConfigError(Exception):
    pass


def _check(where, table, allowed, required=()):
    if not isinstance(table, dict):
        raise ConfigError(f"{where}: expected a table")
    for k in table:
        if k not in allowed:
            raise ConfigError(f"{where}: {k}: unknown key (allowed: {', '.join(sorted(allowed))})")
    for k in required:
        if k not in table:
            raise ConfigError(f"{where}: {k}: required")


def _load(path):
    try:
        return tomllib.loads(Path(path).read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None


def tree_hash(path):
    """Content hash of a file or directory (names + bytes), so results are keyed on what the task really is."""
    h, p = hashlib.sha256(), Path(path)
    for f in sorted([p] if p.is_file() else (x for x in p.rglob("*") if x.is_file())):
        h.update(str(f.relative_to(p.parent if p.is_file() else p)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


@dataclass
class Model:
    name: str
    provider: str
    id: str
    price: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    context: int = 200_000
    max_output: int = 32_000

    @property
    def protocol(self):
        return PROVIDERS[self.provider]["protocol"]

    def entry(self):
        return {"provider": self.provider, "id": self.id, "price": self.price, "params": self.params}


def load_models(path):
    models = {}
    for name, t in _load(path).items():
        where = f"{path}: [{name}]"
        _check(where, t, {"provider", "id", "price", "params", "context", "max_output", "notes"}, ["provider"])
        if t["provider"] not in PROVIDERS:
            raise ConfigError(f"{where}: provider: unknown {t['provider']!r} (known: {', '.join(PROVIDERS)})")
        if t["provider"] != "scripted" and "id" not in t:
            raise ConfigError(f"{where}: id: required")
        _check(f"{where}.price", t.get("price", {}), {"in", "out", "cache_read", "cache_write"})
        models[name] = Model(name, t["provider"], t.get("id", "scripted"), t.get("price", {}), t.get("params", {}),
                             t.get("context", 200_000), t.get("max_output", 32_000))
    return models


@dataclass
class Harness:
    name: str
    path: Path
    title: str
    version: str
    install: str
    bin: str
    version_cmd: str
    cmd: list
    stdin: str = "none"
    env: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    routes: dict = field(default_factory=dict)       # protocol -> {"env", "files", "args"}
    artifacts: list = field(default_factory=list)
    cleanup: list = field(default_factory=list)
    path_dirs: list = field(default_factory=list)
    scripted: dict = field(default_factory=dict)
    login: dict = field(default_factory=dict)

    @property
    def prefix(self):
        return CACHE / f"{self.name}@{self.version}"

    def route_for(self, model):
        """The protocol this harness should use for `model`: native if it can, else the first it declares."""
        if model.protocol in self.routes:
            return model.protocol
        return next(iter(self.routes), None)


def load_harness(path):
    path = Path(path)
    t, where = _load(path), str(path)
    _check(where, t, {"name", "version", "install", "bin", "version_cmd", "cmd", "stdin", "env", "files", "route",
                      "artifacts", "cleanup", "path", "scripted", "login", "notes"},
           ["name", "version", "install", "bin", "cmd", "route"])
    if not isinstance(t["cmd"], list) or not t["cmd"]:
        raise ConfigError(f"{where}: cmd: must be a non-empty list of strings")
    if t.get("stdin", "none") not in ("none", "prompt"):
        raise ConfigError(f"{where}: stdin: must be \"none\" or \"prompt\"")
    for proto, r in t["route"].items():
        if proto not in PROTOCOLS:
            raise ConfigError(f"{where}: route.{proto}: unknown protocol (known: {', '.join(PROTOCOLS)})")
        _check(f"{where}: route.{proto}", r, {"env", "files", "args"})
    _check(f"{where}: scripted", t.get("scripted", {}), {"tool", "arg"})
    _check(f"{where}: login", t.get("login", {}), {"provider", "pass_env", "usage"})
    return Harness(path.stem, path, t["name"], t["version"], t["install"], t["bin"],
                   t.get("version_cmd", "{bin} --version"), t["cmd"], t.get("stdin", "none"), t.get("env", {}),
                   t.get("files", {}), t["route"], t.get("artifacts", []), t.get("cleanup", []),
                   t.get("path", []), t.get("scripted", {}), t.get("login", {}))


def load_harnesses(root):
    return {p.stem: load_harness(p) for p in sorted(Path(root, "harnesses").glob("*.toml"))}


@dataclass
class Task:
    name: str
    dir: Path
    prompt: str
    timeout: int = 900
    max_requests: int = 150
    max_cost: float = 3.0
    repo: dict = field(default_factory=dict)
    verify: dict = field(default_factory=dict)
    expect: list = field(default_factory=list)
    hash: str = ""

    @property
    def is_review(self):
        return self.verify.get("cmd") == "review"


def load_task(d):
    d = Path(d)
    path = d / "task.toml"
    t, where = _load(path), str(path)
    _check(where, t, {"prompt", "timeout", "max_requests", "max_cost", "repo", "verify", "expect", "tags", "notes"},
           ["prompt", "verify"])
    repo = t.get("repo", {"path": "fixture"})
    _check(f"{where}: repo", repo, {"path", "git", "rev", "setup", "patch"})
    if ("path" in repo) == ("git" in repo):
        raise ConfigError(f"{where}: repo: give exactly one of path = \"fixture\" or git = \"https://...\"")
    if "git" in repo and "rev" not in repo:
        raise ConfigError(f"{where}: repo.rev: required with repo.git (pin a commit so results are reproducible)")
    if "path" in repo and not (d / repo["path"]).is_dir():
        raise ConfigError(f"{where}: repo.path: folder {repo['path']!r} not found next to task.toml")
    verify = t["verify"]
    _check(f"{where}: verify", verify, {"cmd", "hidden", "timeout", "network"}, ["cmd"])
    for i, e in enumerate(t.get("expect", [])):
        _check(f"{where}: expect[{i}]", e, {"file", "lines", "any"}, ["file"])
    if verify["cmd"] == "review" and not t.get("expect"):
        raise ConfigError(f"{where}: expect: a review task needs at least one [[expect]] finding")
    if (d / ".env").exists() or ("path" in repo and (d / repo["path"] / ".env").exists()):
        raise ConfigError(f"{where}: tasks may not ship a .env file (some harnesses load it as credentials)")
    return Task(d.name, d, t["prompt"].strip(), t.get("timeout", 900), t.get("max_requests", 150),
                t.get("max_cost", 3.0), repo, verify, t.get("expect", []), tree_hash(d))


def load_tasks(root):
    return {p.parent.name: load_task(p.parent) for p in sorted(Path(root, "tasks").glob("*/task.toml"))}


_PH = re.compile(r"\{([a-z_]+(?:\.[a-z_]+)?)\}")


def fill(value, vars, where="value"):
    """Substitute {name} placeholders in strings, recursively through lists/tables. Unknown names are errors."""
    if isinstance(value, str):
        def sub(m):
            if m.group(1) not in vars:
                raise ConfigError(f"{where}: unknown placeholder {{{m.group(1)}}} (known: {', '.join(sorted(vars))})")
            return str(vars[m.group(1)])
        return _PH.sub(sub, value)
    if isinstance(value, list):
        return [fill(v, vars, where) for v in value]
    if isinstance(value, dict):
        return {fill(k, vars, where): fill(v, vars, where) for k, v in value.items()}
    return value
