#!/usr/bin/env python3
"""roast_gateway.py — прожарка Kauz-шлюза через CF tunnel (внешняя проверка)."""
import json, os, time, urllib.request, urllib.error

BASE = os.environ.get("KZ_GATEWAY", "https://benefits-diego-papers-linear.trycloudflare.com")
HDR = {"Content-Type": "application/json"}
results = []

def req(path, body=None, method=None, timeout=90):
    t0 = time.time()
    r = urllib.request.Request(BASE + path,
        data=json.dumps(body).encode() if body else None,
        headers=HDR, method=method or ("POST" if body else "GET"))
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        return resp.status, resp.read().decode(errors="replace"), round(time.time()-t0, 1)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200], round(time.time()-t0, 1)

# 1. health
s, b, t = req("/health"); results.append(("health", s, t, b[:120]))
# 2. models
s, b, t = req("/v1/models")
ids = [m["id"] for m in json.loads(b).get("data", [])] if s == 200 else []
results.append(("models", s, t, f"{len(ids)}: {ids[:4]}"))
# 3. non-stream chat
s, b, t = req("/v1/chat/completions", {
    "model": "kauz-selection", "stream": False,
    "messages": [{"role": "user", "content": "Reply with exactly: ROAST-OK"}]}, timeout=120)
txt = ""
try: txt = json.loads(b)["choices"][0]["message"]["content"][:60]
except Exception: txt = b[:80]
results.append(("chat-nonstream", s, t, txt))
# 4. stream chat
s, b, t = req("/v1/chat/completions", {
    "model": "kauz-selection", "stream": True,
    "messages": [{"role": "user", "content": "Count 1 to 5"}]}, timeout=120)
nchunk = b.count("data: "); hasdone = "data: [DONE]" in b
results.append(("chat-stream", s, t, f"chunks={nchunk} done={hasdone}"))
# 5. tool-use (extra body kauz_tool)
s, b, t = req("/v1/chat/completions", {
    "model": "kauz-selection", "stream": False,
    "kauz_tool": "jinaSearchWeb",
    "messages": [{"role": "user", "content": "Search web for: latest claude haiku release date"}]}, timeout=180)
txt = ""
try: txt = json.loads(b)["choices"][0]["message"]["content"][:80]
except Exception: txt = b[:80]
results.append(("tool-use", s, t, txt))
# 6. second model
s, b, t = req("/v1/chat/completions", {
    "model": "eu.anthropic.claude-haiku-4-5-20251001-v1:0", "stream": False,
    "messages": [{"role": "user", "content": "Say HAOK"}]}, timeout=120)
try: txt = json.loads(b)["choices"][0]["message"]["content"][:40]
except Exception: txt = b[:80]
results.append(("haiku-4.5", s, t, txt))

print("=" * 60)
for name, s, t, info in results:
    print(f"{'PASS' if s==200 else 'FAIL'} {name:16s} HTTP{s} {t:5.1f}s  {info}")
