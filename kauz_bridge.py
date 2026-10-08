#!/usr/bin/env python3
"""kauz-bridge — OpenAI-compatible gateway for ai-workplace.kauz.ai

Reversed API (2026-10-07):
  POST /api/auth/sign-up/email      {name,email,password,brevoNewsletter,referenceCode,callbackURL}
  GET  /api/auth/verify-email?token=***   (token from email)
  POST /api/auth/sign-in/email      {email,password,remember}
  POST /api/auth/get-session
  POST /api/chat                    SSE stream (Vercel AI SDK v5 protocol)
    body: {id, message:{role,parts:[{type:'text',text}],id}, locale,
           selectedChatModel, selectedImageModel,
           explicitToolName, explicitSimToolsetIds:[], explicitMcpServerIds:[]}
  GET  /api/trpc/tools.listToolsetsForSelection?batch=1&input=...

Models (from selfHosted.listModels): kauz-selection (router),
kauz_gpt-5.6-luna, kauz_gpt-5.6-terra, eu.anthropic.claude-haiku-4-5-*,
glm-5.3, gemini-3.5-flash-lite, deepseek-v4-flash-0731, qwen3.7-plus, ...

Run: python kauz_bridge.py --port 8310 --session kauz_session.json
Env: KAUZ_COOKIE  (raw cookie header; overrides --session)
"""
import argparse
import json
import os
import re
import sys
import time
import uuid
import urllib.request
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "https://ai-workplace.kauz.ai"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

STATE = {"cookie": "", "models": [], "toolsets": [], "pool": [], "pool_idx": 0}

MODEL_IDS = [
    "kauz-selection",
    "kauz_gpt-5.6-luna",
    "kauz_gpt-5.6-terra",
    "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    "gemini-3.5-flash-lite",
    "deepseek-v4-flash-0731",
]


def http_post(path, body, timeout=300):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": UA,
            "Cookie": STATE["cookie"],
            "Origin": BASE,
            "Referer": BASE + "/de/chat",
            "Accept": "*/*",
        },
    )
    return urllib.request.urlopen(req, timeout=timeout)


def http_get(path, timeout=60):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA, "Cookie": STATE["cookie"]})
    return urllib.request.urlopen(req, timeout=timeout)


def kauz_stream(user_text, model, toolsets=None, tool=None):
    """Yield raw SSE lines from /api/chat."""
    body = {
        "id": str(uuid.uuid4()),
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": user_text}],
            "id": str(uuid.uuid4()),
        },
        "locale": "en",
        "selectedChatModel": model,
        "selectedImageModel": "gemini-2.5-flash-image",
        "explicitToolName": tool,
        "explicitSimToolsetIds": toolsets or [],
        "explicitMcpServerIds": [],
    }
    r = http_post("/api/chat", body)
    for raw in r:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line:
            yield line


def openai_events(messages, model, stream, toolsets, tool):
    """Convert kauz SSE -> OpenAI chat.completions events."""
    # flatten messages: system -> prefix, history as text blocks
    parts = []
    for m in messages:
        role = m.get("role", "user")
        c = m.get("content", "")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        if c:
            parts.append(f"[{role}] {c}")
    prompt = "\n".join(parts)

    created = int(time.time())
    rid = "chatcmpl-" + uuid.uuid4().hex[:24]

    text_parts = []
    tool_calls_buf = {}  # id -> {name, args}
    finish = "stop"

    def chunk(delta, finish_reason=None):
        return {
            "id": rid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    for line in kauz_stream(prompt, model, toolsets, tool):
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            ev = json.loads(payload)
        except json.JSONDecodeError:
            continue
        t = ev.get("type", "")
        if t == "text-delta":
            text_parts.append(ev.get("delta", ""))
            if stream:
                yield chunk({"content": ev.get("delta", "")})
        elif t == "tool-input-available":
            tcid = ev.get("toolCallId", str(uuid.uuid4()))
            tool_calls_buf[tcid] = {
                "name": ev.get("toolName", "tool"),
                "arguments": json.dumps(ev.get("input", {}), ensure_ascii=False),
            }
        elif t == "tool-output-available":
            tcid = ev.get("toolCallId")
            out = json.dumps(ev.get("output", ""), ensure_ascii=False)[:4000]
            text_parts.append(f"\n[tool_result {ev.get('toolName','')}]: {out}\n")
            if stream:
                yield chunk({"content": f"\n[tool_result]: {out[:1000]}\n"})
        elif t == "finish":
            finish = ev.get("finishReason") or "stop"
            if finish == "tool-calls":
                finish = "stop"

    if tool_calls_buf:
        finish = "tool_calls"

    if stream:
        tcs = [
            {"index": i, "id": k, "type": "function",
             "function": {"name": v["name"], "arguments": v["arguments"]}}
            for i, (k, v) in enumerate(tool_calls_buf.items())
        ]
        delta = {}
        if tcs:
            delta["tool_calls"] = tcs
        yield chunk(delta, finish)
        yield "data: [DONE]"
    else:
        content = "".join(text_parts)
        msg = {"role": "assistant", "content": content}
        if tool_calls_buf:
            msg["tool_calls"] = [
                {"id": k, "type": "function",
                 "function": {"name": v["name"], "arguments": v["arguments"]}}
                for k, v in tool_calls_buf.items()
            ]
        yield json.dumps({
            "id": rid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        sys.stderr.write("[kauz] %s\n" % (a[0] % a[1:]))

    def _send(self, code, obj, ctype="application/json"):
        data = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/v1/models", "/models"):
            ids = STATE["models"] or MODEL_IDS
            self._send(200, {"object": "list", "data": [
                {"id": m, "object": "model", "created": 1700000000, "owned_by": "kauz"}
                for m in ids]})
        elif self.path == "/v1/toolsets":
            self._send(200, {"toolsets": [
                {"id": t.get("id"), "name": t.get("name"), "tools": t.get("allowedToolIds", [])}
                for t in STATE["toolsets"]]})
        elif self.path == "/health":
            self._send(200, {"ok": bool(STATE["cookie"]), "models": len(STATE["models"] or MODEL_IDS), "pool": len(STATE["pool"])})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not (self.path.startswith("/v1/chat/completions") or self.path.startswith("/chat/completions")):
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "bad json"})

        model = req.get("model", "kauz-selection")
        stream = bool(req.get("stream"))
        messages = req.get("messages", [])
        if not messages:
            return self._send(400, {"error": "messages required"})

        # tool hints: pass via extra fields or map OpenAI tools -> kauz explicitToolName
        toolsets = req.get("kauz_toolsets") or []
        tool = req.get("kauz_tool")
        if not tool and req.get("tools"):
            fn = req["tools"][0].get("function", {}).get("name")
            if fn:
                tool = fn

        rotate_cookie(force=True)
        try:
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for ev in openai_events(messages, model, True, toolsets, tool):
                    payload = ("data: " + (ev if isinstance(ev, str) else json.dumps(ev)) + "\n\n").encode()
                    self.wfile.write(("%x\r\n" % len(payload)).encode() + payload + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            else:
                out = list(openai_events(messages, model, False, toolsets, tool))
                self._send(200, json.loads(out[0]))
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            if e.code in (401, 429):
                rotate_cookie(force=True)  # burned/rate-limited session -> next in pool
            self._send(e.code, {"error": {"message": body, "type": "upstream_error", "code": e.code}})
        except Exception as e:
            self._send(500, {"error": {"message": str(e), "type": "bridge_error"}})


def load_session(path):
    if os.environ.get("KAUZ_COOKIE"):
        STATE["cookie"] = os.environ["KAUZ_COOKIE"]
        return
    if not os.path.exists(path):
        # pool-only mode: session file optional (kauz_accounts.jsonl supplies cookies)
        return
    sess = json.load(open(path))
    if isinstance(sess, dict) and "cookies" in sess:
        ck = sess["cookies"]
        if isinstance(ck, dict):
            STATE["cookie"] = "; ".join(f"{k}={v}" for k, v in ck.items())
        else:
            STATE["cookie"] = ck
    elif isinstance(sess, dict) and "cookie" in sess:
        STATE["cookie"] = sess["cookie"]
    else:
        raise SystemExit(f"cannot parse session file {path}")


def load_pool(pool_path):
    """Load kauz_accounts.jsonl into STATE['pool'] (list of cookie strings)."""
    pool = []
    if not os.path.exists(pool_path):
        return pool
    for line in open(pool_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            ck = rec.get("cookies") or {}
            if isinstance(ck, dict) and ck:
                pool.append("; ".join(f"{k}={v}" for k, v in ck.items()))
            elif isinstance(ck, str) and ck:
                pool.append(ck)
        except Exception:
            continue
    STATE["pool"] = pool
    STATE["pool_idx"] = 0
    print(f"[kauz] session pool loaded: {len(pool)} accounts from {pool_path}")
    return pool


def rotate_cookie(force=False):
    """Round-robin across pool; fall back to single STATE cookie."""
    pool = STATE.get("pool") or []
    if not pool:
        return
    if force or not STATE.get("cookie"):
        i = STATE.get("pool_idx", 0) % len(pool)
        STATE["cookie"] = pool[i]
        STATE["pool_idx"] = i + 1


def fetch_catalog():
    uid, org = "", ""
    try:
        r = http_get("/api/auth/get-session")
        sess = json.loads(r.read().decode())
        uid = sess["user"]["id"]
        org = sess["user"].get("activeOrganizationId") or ""
        inp = urllib.parse.quote(json.dumps({"0": {"json": {"sessionUserId": uid, "activeOrganizationId": org}}}))
        r = http_get("/api/trpc/tools.listToolsetsForSelection?batch=1&input=" + inp)
        d = json.loads(r.read().decode())
        STATE["toolsets"] = d[0]["result"]["data"]["json"].get("toolsets", [])
        print(f"[kauz] toolsets loaded: {len(STATE['toolsets'])}")
    except Exception as e:
        print(f"[kauz] catalog fetch failed (non-fatal): {e}")
    if not uid:
        return
    try:
        inp = urllib.parse.quote(json.dumps({"json": {"sessionUserId": uid, "activeOrganizationId": org}}))
        r = http_get("/api/trpc/selfHosted.listModels?input=" + inp)
        d = json.loads(r.read().decode())
        ids = []
        def walk(o):
            if isinstance(o, dict):
                if "id" in o and isinstance(o["id"], str) and ("model" in o or "name" in o):
                    ids.append(o["id"])
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(d)
        if ids:
            STATE["models"] = sorted(set(ids))
            print(f"[kauz] models loaded: {len(STATE['models'])}")
    except Exception as e:
        print(f"[kauz] models fetch failed (non-fatal): {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8310)
    ap.add_argument("--session", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "kauz_session.json"))
    ap.add_argument("--pool", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "kauz_accounts.jsonl"),
                    help="jsonl account pool for cookie rotation")
    args = ap.parse_args()
    load_session(args.session)
    load_pool(args.pool)
    rotate_cookie(force=True)
    if not STATE["cookie"]:
        raise SystemExit("empty cookie")
    fetch_catalog()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[kauz] bridge listening on :{args.port} (cookie {len(STATE['cookie'])} chars)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
