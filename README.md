# kauz-bridge

OpenAI-compatible gateway + autoreg for **ai-workplace.kauz.ai** (KAUZ AI Workplace).

Fully reversed HTTP API — no browser needed after registration.
Free accounts get a full agent stack: routing model (`kauz-selection`), GPT-5.6 class models, Claude Haiku 4.5, Gemini, DeepSeek + **169 server-side toolsets** (web search, browser, arxiv, wikipedia, duckduckgo, jina, tavily...) executed on their backend.

## What's inside

| File | Purpose |
|------|---------|
| `kauz_autoreg.py` | Full-cycle registration: sign-up → IMAP email verify → sign-in → session save |
| `kauz_bridge.py` | OpenAI-compatible bridge (`/v1/chat/completions`, `/v1/models`, `/v1/toolsets`) |

## Quick start

```bash
# 1. Register account (Gmail +alias trick — one mailbox, unlimited accounts)
python kauz_autoreg.py --count 1 \
  --imap-host imap.gmail.com \
  --imap-user you@gmail.com \
  --imap-pass "your-app-password"

# → writes kauz_session.json + kauz_accounts.jsonl

# 2. Run the bridge
python kauz_bridge.py --port 8310 --session kauz_session.json

# 3. Use it as any OpenAI endpoint
curl http://127.0.0.1:8310/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"kauz-selection","messages":[{"role":"user","content":"hello"}]}'
```

Works with any OpenAI-compatible client: `openai`, LiteLLM, Cherry Studio, anything. Just set `base_url=http://127.0.0.1:8310/v1`, `api_key="x"` (ignored).

## Models (verified live 2026-10-07)

| Model | Status |
|-------|--------|
| `kauz-selection` | OK — smart router (picks model per task, incl. reasoning) |
| `kauz_gpt-5.6-luna` | OK |
| `kauz_gpt-5.6-terra` | OK |
| `eu.anthropic.claude-haiku-4-5-20251001-v1:0` | OK |
| `gemini-3.5-flash-lite` | OK |
| `deepseek-v4-flash-0731` | OK |
| `glm-5.3`, `kimi-k2.7-code`, `kauz_gpt-5`, `claude-opus-5-5`, `gemini-3.6-flash` | 403 `model_disabled:chat` (present in UI bundle, gated server-side) |

## Tool use

The platform executes tools **server-side**. Two knobs in the reversed request:

- `explicitToolName` — force one tool (e.g. `jinaSearchWeb`)
- `explicitSimToolsetIds` — enable toolsets by id (169 available: `duckduckgo`, `arxiv`, `wikipedia`, `browser_use`, `github_v2`, `google_search`, ...)

Through the bridge:

```bash
# force a specific tool
curl http://127.0.0.1:8310/v1/chat/completions -d '{
  "model":"kauz-selection",
  "kauz_tool":"jinaSearchWeb",
  "messages":[{"role":"user","content":"bitcoin price?"}]}'

# or enable toolsets
curl http://127.0.0.1:8310/v1/chat/completions -d '{
  "model":"kauz-selection",
  "kauz_toolsets":["duckduckgo","wikipedia"],
  "messages":[{"role":"user","content":"research X"}]}'

# list all toolsets
curl http://127.0.0.1:8310/v1/toolsets
```

Tool calls + results are mapped to OpenAI `tool_calls` and appended `[tool_result]` content.

Note: the model may invoke web tools even without hints (server-side default toolset).

## Reversed API reference

Auth = **better-auth**. Frontend = Next.js + Vercel AI SDK v5. Edge = BunnyCDN.

```
POST /api/auth/sign-up/email
  {"name","email","password","brevoNewsletter":false,"referenceCode":"",
   "callbackURL":"/de/verify-email?status=verified&email=<email>"}
  → 200, sends verification mail. No captcha, referenceCode optional (empty works).

GET /api/auth/verify-email?token=***  → {"status":true}

POST /api/auth/sign-in/email   {"email","password","remember":true}
  → 401 until email verified; set-cookie better-auth session

POST /api/auth/get-session  → user.id, activeOrganizationId

POST /api/chat                (SSE, text/event-stream)
  {"id":"<uuid>","message":{"role":"user","parts":[{"type":"text","text":"..."}],"id":"<uuid>"},
   "locale":"de","selectedChatModel":"kauz-selection",
   "selectedImageModel":"gemini-2.5-flash-image",
   "explicitToolName":null,"explicitSimToolsetIds":[],"explicitMcpServerIds":[]}

  SSE events: start / start-step / reasoning-delta / text-delta /
  tool-input-start / tool-input-delta / tool-input-available /
  tool-output-available / finish-step / finish / [DONE]

GET /api/trpc/tools.listToolsetsForSelection?batch=1
    &input={"0":{"json":{"sessionUserId":"<uid>","activeOrganizationId":"<org>"}}}
  → 169 toolsets with allowedToolIds

GET /api/trpc/selfHosted.listModels?...  → org model list
```

Gotchas:
- Wrong field set on `/api/chat` → `400 {"code":"bad_request:api","cause":"Invalid request body"}`. The exact body above is captured from the live frontend.
- `message` (singular object with `parts`), NOT `messages[]`.
- Verify mail token: parse IMAP message as **raw bytes** — text-mode file writes corrupt MIME.
- Sign-in before email verify returns generic `401 INVALID_EMAIL_OR_PASSWORD`.

## Notes

- Registration uses Gmail `+alias` addressing: one real mailbox → unlimited accounts.
- IMAP requires an app password (Google 2FA on).
- This is a research project for interoperability. Respect the provider's ToS.
