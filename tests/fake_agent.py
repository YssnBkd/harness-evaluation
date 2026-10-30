#!/usr/bin/env python3
"""A minimal real agent loop (OpenAI chat API + one bash tool), used as the offline test harness."""
import json, os, subprocess, sys, urllib.request

if sys.argv[1:] == ["--version"]:
    sys.exit(print("fake-agent 1.0"))
model, prompt = sys.argv[1], sys.argv[2]
tools = [{"type": "function", "function": {"name": "bash", "description": "Run a shell command",
          "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]
msgs = [{"role": "user", "content": prompt}]
for _ in range(20):
    req = urllib.request.Request(os.environ["FAKE_URL"] + "/chat/completions", method="POST",
                                 data=json.dumps({"model": model, "messages": msgs, "tools": tools}).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["FAKE_KEY"],
                                          "content-type": "application/json"})
    m = json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]
    msgs.append(m)
    if not m.get("tool_calls"):
        print(m.get("content"))
        break
    for tc in m["tool_calls"]:
        r = subprocess.run(["bash", "-c", json.loads(tc["function"]["arguments"])["command"]],
                           capture_output=True, text=True)
        msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": r.stdout + r.stderr})
