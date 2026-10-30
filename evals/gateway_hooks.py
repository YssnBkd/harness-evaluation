"""Loaded inside the LiteLLM proxy (via PYTHONPATH, so all hooks share this one module).

The runner registers each trial by writing $HEV_RUN_DIR/keys/<api key>.json before launching the harness:
  {"trial": id, "dir": trial dir, "model": proxy model name, "provider": ..., "max_requests": n, "max_cost": usd,
   "script": shell command for the scripted model}
and deletes it when the trial ends, so leftover processes can't spend or pollute results.
"""
import json, os, re, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth

KEYS = Path(os.environ.get("HEV_RUN_DIR", ".")) / "keys"
spent = {}      # trial -> {"requests": n, "cost": usd}


def trial_of(api_key):
    if not re.fullmatch(r"sk-hev-[A-Za-z0-9_-]{1,120}", api_key or ""):
        return None
    try:
        return json.loads((KEYS / f"{api_key}.json").read_text())
    except (OSError, ValueError):
        return None


async def user_api_key_auth(request, api_key: str) -> UserAPIKeyAuth:
    t = trial_of(api_key)
    if t is None:
        raise HTTPException(401, "hev: unknown or closed trial key")
    return UserAPIKeyAuth(api_key=api_key, key_alias=t["trial"], metadata={"hev": {**t, "key": api_key}})


def _strip(o, key):
    if isinstance(o, dict):
        o.pop(key, None)
        for v in o.values():
            _strip(v, key)
    elif isinstance(o, list):
        for v in o:
            _strip(v, key)


def _log(t, row):
    with open(Path(t["dir"]) / "requests.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")


def _hev(kwargs):
    md = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    return (md.get("user_api_key_metadata") or {}).get("hev") or (md.get("user_api_key_auth_metadata") or {}).get("hev")


class Hooks(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        t = (user_api_key_dict.metadata or {}).get("hev")
        if t is None:
            raise HTTPException(401, "hev: no trial")
        s = spent.setdefault(t["trial"], {"requests": 0, "cost": 0.0})
        if s["requests"] >= t.get("max_requests", 10**9) or s["cost"] >= t.get("max_cost", 1e9):
            raise HTTPException(429, "hev-capped: trial request/cost cap reached")
        s["requests"] += 1
        # /v1/messages keeps litellm's metadata in "litellm_metadata"; "metadata" there belongs to Anthropic's API
        mkey = "litellm_metadata" if "litellm_metadata" in data else "metadata"
        data.setdefault(mkey, {})["hev_requested_model"] = data.get("model")
        data["model"] = t["model"]                      # route by trial, not by the name the harness sent
        if t.get("provider") == "gemini":
            _strip(data, "cache_control")              # gemini-4-argon rejects explicit caching
        if t.get("provider") == "scripted":            # the model name is the one field every adapter forwards
            data["model"] = f"{t['model']}/{t['key']}"  # (wildcard deployment) -> scripted server knows the trial
        return data

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._meter(kwargs, start_time, end_time, None)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._meter(kwargs, start_time, end_time, kwargs.get("exception"))

    def _meter(self, kwargs, start_time, end_time, exc):
        slo = kwargs.get("standard_logging_object") or {}
        md = slo.get("metadata") or {}
        t = (md.get("user_api_key_auth_metadata") or {}).get("hev") or _hev(kwargs)
        if t is None:
            return
        cost = slo.get("response_cost") or 0.0
        spent.setdefault(t["trial"], {"requests": 0, "cost": 0.0})["cost"] += cost
        lp = kwargs.get("litellm_params") or {}
        requested = ((lp.get("metadata") or {}).get("hev_requested_model")
                     or (lp.get("litellm_metadata") or {}).get("hev_requested_model"))
        _log(t, {
            "t": time.time(), "status": "error" if exc else "ok", "requested": requested,
            "served": (slo.get("model_group") or slo.get("model") or "").replace(f"/{t['key']}", "").replace(t["key"], t["model"]),
            "call": slo.get("call_type"),
            "in": slo.get("prompt_tokens"), "out": slo.get("completion_tokens"), "cost": cost,
            "secs": round((end_time - start_time).total_seconds(), 3) if exc is None else None,
            "error": None if exc is None else {"type": type(exc).__name__,
                                               "code": getattr(exc, "status_code", None), "msg": str(exc)[:500]},
        })


hooks = Hooks()


# ---- scripted model: $0 stand-in that drives the harness's own shell tool ----

SHELL_NAMES = ("bash", "shell", "terminal", "run_shell", "exec", "command")


def _pick_shell(tools):
    for tool in tools or []:
        fn = tool.get("function", tool)
        name = fn.get("name", "")
        props = (fn.get("parameters") or {}).get("properties") or {}
        arg = next((a for a in props if a.lower() in ("command", "cmd", "script", "commandline", "command_line")), None)
        if arg and any(s in name.lower() for s in SHELL_NAMES):
            return name, arg, fn.get("parameters") or {}
    return None


def _plan(body):
    """First turn: one shell tool call running the trial's script. After a tool result: a final answer."""
    t = trial_of(body.get("model", "").rpartition("/")[2]) or {}
    msgs = body.get("messages", [])                   # adapters differ in how tool results come back,
    called = any(m.get("role") in ("tool", "function") or m.get("tool_calls") for m in msgs)  # so check both
    shell = _pick_shell(body.get("tools"))
    if t.get("dir"):                                  # what the harness offered: makes adapter issues obvious
        with open(Path(t["dir"]) / "scripted.jsonl", "a") as f:
            f.write(json.dumps({"called": called, "picked": shell and shell[:2], "tools": [
                {"name": (x.get("function") or x).get("name"),
                 "args": list(((x.get("function") or x).get("parameters") or {}).get("properties", {}))}
                for x in body.get("tools") or []]}) + "\n")
    if called or shell is None:
        return t, "Done.", None
    name, arg, schema = shell
    args = {arg: t.get("script", "echo scripted > SCRIPTED_OK")}
    filler = {"string": "hev scripted step", "boolean": False, "integer": 0, "number": 0}
    for req in schema.get("required", []):            # fill other required args (description, IsDaemon, ...)
        typ = (schema.get("properties", {}).get(req) or {}).get("type")
        if req not in args and typ in filler:
            args[req] = filler[typ]
    return t, "", {"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                   "function": {"name": name, "arguments": json.dumps(args)}}


class _Scripted(BaseHTTPRequestHandler):
    """OpenAI-compatible /v1/chat/completions. Real HTTP, so faults are real too (t["fault"])."""
    seen = {}

    def log_message(self, *a):
        pass

    def _send(self, code, obj, headers=()):
        data = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (("content-type", "application/json"), *headers):
            self.send_header(k, v)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        t, text, call = _plan(body)
        fault, n = t.get("fault"), self.seen.get(t.get("trial"), 0)
        self.seen[t.get("trial")] = n + 1
        if fault == "429" or (fault == "429once" and n == 0):
            return self._send(429, {"error": {"message": "rate limited (scripted)"}}, [("retry-after", "1")])
        if fault == "quota":
            return self._send(429, {"error": {"message": "Quota exceeded for metric: requests, limit: 0"}})
        if fault in ("500", "400"):
            return self._send(int(fault), {"error": {"message": f"scripted {fault}"}})
        if fault == "hang":
            time.sleep(3600)
        usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        msg = {"role": "assistant", "content": text or None, **({"tool_calls": [call]} if call else {})}
        finish = "tool_calls" if call else "stop"
        head = {"id": "chatcmpl-hev", "created": int(time.time()), "model": "scripted"}
        if not body.get("stream"):
            return self._send(200, {**head, "object": "chat.completion", "usage": usage,
                                    "choices": [{"index": 0, "message": msg, "finish_reason": finish}]})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        delta = {**msg, "tool_calls": [{**call, "index": 0}]} if call else msg
        chunks = [{"index": 0, "delta": delta, "finish_reason": None},
                  {"index": 0, "delta": {}, "finish_reason": finish}]
        for i, c in enumerate(chunks):
            if fault == "midstream" and i == 1:
                self.wfile.write(b"data: {broken\n\n")
                return
            extra = {"usage": usage} if i == len(chunks) - 1 else {}
            self.wfile.write(f"data: {json.dumps({**head, 'object': 'chat.completion.chunk', 'choices': [c], **extra})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


if os.environ.get("HEV_SCRIPTED_PORT"):
    _srv = ThreadingHTTPServer(("127.0.0.1", int(os.environ["HEV_SCRIPTED_PORT"])), _Scripted)
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
