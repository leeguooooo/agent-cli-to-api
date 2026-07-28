# agent-cli-to-api

Expose popular **agent CLIs** as a small **OpenAI-compatible** HTTP API (`/v1/*`).

Works great as a local gateway (localhost) or behind a reverse proxy.

Think of it as **LiteLLM for agent CLIs**: you point existing OpenAI SDKs/tools at `base_url`, and choose a backend by `model`.

Supported backends:
- **OpenAI Codex** - defaults to backend `/responses` for vision and **image generation** (DALL-E / `gpt-image`-class output); falls back to `codex exec`
- **Cursor Agent** - via `cursor-agent` CLI
- **Claude Code** - via CLI or **direct API** (auto-detects `~/.claude/settings.json` config)
- **Gemini** - via CLI or CloudCode direct (set `GEMINI_USE_CLOUDCODE_API=1`)

Why this exists:
- Many tools/SDKs only speak the OpenAI API (`/v1/chat/completions`) - this lets you plug agent CLIs into that ecosystem.
- One gateway, multiple CLIs: pick a backend by `model` (with optional prefixes like `cursor:` / `claude:` / `gemini:`).
- **Expose your ChatGPT Plus / Pro subscription's image generation as an HTTP API.** No `OPENAI_API_KEY` required — the gateway reuses the OAuth token from `codex login`, lets you call `image_generation` via plain chat completions, and returns the PNG inline (data URI). See [Image generation (ChatGPT subscription)](#image-generation-chatgpt-subscription).

## Table of Contents

- [Requirements](#requirements)
- [Install](#install)
- [Run (No `.env` Needed)](#run-no-env-needed)
- [Core Configuration](#core-configuration)
- [API](#api)
- [Image generation (ChatGPT subscription)](#image-generation-chatgpt-subscription)
- [OpenAI SDK examples](#openai-sdk-examples)
- [Security notes](#security-notes)
- [Logging & Performance Diagnosis](#logging--performance-diagnosis)
- [Performance notes (important)](#performance-notes-important)
- [Advanced setup (optional)](#advanced-setup-optional)
- [Keywords (SEO)](#keywords-seo)

## Requirements

- Python 3.10+ (tested on 3.13)
- Install and authenticate the CLI(s) you want to use (`codex`, `cursor-agent`, `claude`, `gemini`)

## Install

### Option A: one-line installer

```bash
curl -fsSL https://github.com/leeguooooo/agent-cli-to-api/releases/latest/download/install.sh | sh
```

Install and start the macOS launchd service:

```bash
curl -fsSL https://github.com/leeguooooo/agent-cli-to-api/releases/latest/download/install.sh | INSTALL_LAUNCHD=1 sh
```

Pin a token and install the launchd service:

```bash
curl -fsSL https://github.com/leeguooooo/agent-cli-to-api/releases/latest/download/install.sh | CODEX_GATEWAY_TOKEN=devtoken INSTALL_LAUNCHD=1 sh
```

The installer defaults to `~/.agent-cli-to-api`, installs/updates `uv`, installs/updates Codex CLI for the Codex provider, creates `.env`, and uses the latest GitHub release. Set `AGENT_CLI_TO_API_REF=main` to install unreleased `main`.

### Option B: uv

```bash
uv sync
```

### Option C: pip

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (No `.env` Needed)

Pick a provider and start the gateway:

```bash
uv run agent-cli-to-api codex
uv run agent-cli-to-api gemini
uv run agent-cli-to-api claude
uv run agent-cli-to-api cursor-agent
uv run agent-cli-to-api doctor
```

By default `agent-cli-to-api` does NOT load `.env` implicitly.

Optional auth:

```bash
CODEX_GATEWAY_TOKEN=devtoken uv run agent-cli-to-api codex
```

Custom bind host/port:

```bash
uv run agent-cli-to-api codex --host 127.0.0.1 --port 8000
```

Log request curl commands (optional):

```bash
uv run agent-cli-to-api codex curl
# or
uv run agent-cli-to-api codex --log-curl
```

Notes:
- If `CODEX_WORKSPACE` is unset, the gateway creates an empty temp workspace under `/tmp` (so you don't need to configure a repo path).
- When you start with a fixed provider (e.g. `... gemini`), the client-sent `model` string is accepted but ignored by default (gateway uses the provider's default model).
- Each provider still requires its own local CLI login state (no API key is required for Codex / Gemini CloudCode / Claude OAuth).
- **Claude auto-detects** `~/.claude/settings.json` and uses direct API mode if `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` are configured.
- `uv run agent-cli-to-api cursor-agent` defaults to Cursor Auto routing (`CURSOR_AGENT_MODEL=auto`). If you want faster responses, run with `--preset cursor-fast`.
- When running in an interactive terminal (TTY), the gateway enables colored logs and Markdown rendering by default. To disable: `CODEX_RICH_LOGS=0` or `CODEX_LOG_RENDER_MARKDOWN=0`.

Quick smoke test (optional):

```bash
# In another terminal, run:
#   uv run agent-cli-to-api codex
# Then:
BASE_URL=http://127.0.0.1:8000/v1 ./scripts/smoke.sh
# If you enabled auth:
TOKEN=devtoken BASE_URL=http://127.0.0.1:8000/v1 ./scripts/smoke.sh
```

## Core Configuration

### Presets

```bash
export CODEX_PRESET=codex-fast
uv run agent-cli-to-api codex
```

Supported presets:
- `codex-fast`
- `autoglm-phone`
- `cursor-auto`
- `cursor-fast` (Cursor model pinned for speed)
- `gemini-cloudcode` (defaults to `gemini-3-flash-preview`)
- `claude-oauth`

### Multi-provider routing

Use `CODEX_PROVIDER=auto` and select providers per-request by prefixing `model`:
- Codex: `"gpt-5.6-sol"`
- Cursor: `"cursor:<model>"`
- Claude: `"claude:<model>"`
- Gemini: `"gemini:<model>"`

### Codex backend options

- Web search is enabled by default for the Codex backend API (`CODEX_ENABLE_SEARCH=1`).
  The gateway adds the native Responses `web_search` tool to Codex `/responses`
  requests.
- `CODEX_CODEX_ALLOW_TOOLS=0` to disable Codex backend tool calls (default: enabled).
- OpenAI `tools`/`tool_choice` are mapped for Codex backend, Claude OAuth, and Gemini CloudCode (best-effort).

### Claude direct API (recommended)

The gateway **auto-detects** your Claude CLI configuration from `~/.claude/settings.json`:

```bash
# If you have Claude CLI configured with a custom API endpoint (e.g. 小米 MiMo, 腾讯混元, etc.)
# Just run - no extra config needed:
uv run agent-cli-to-api claude
```

The gateway will automatically:
1. Read `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` from `~/.claude/settings.json`
2. Use direct HTTP API calls (fast, ~0ms gateway overhead)
3. Log timing breakdown: `auth_ms`, `prepare_ms`, `api_latency_ms`

Alternative: Claude OAuth (Anthropic official):

```bash
uv run python -m codex_gateway.claude_oauth_login
CLAUDE_USE_OAUTH_API=1 uv run agent-cli-to-api claude
```

### `uvx` (no venv)

```bash
uvx --from git+https://github.com/leeguooooo/agent-cli-to-api agent-cli-to-api codex
```

### Cloudflare Tunnel

```bash
CODEX_GATEWAY_TOKEN=devtoken uv run agent-cli-to-api codex
cloudflared tunnel --url http://127.0.0.1:8000
```

For advanced env vars, see `.env.example` and `codex_gateway/config.py`.

## API

- `GET /healthz`
- `GET /debug/config` (effective runtime config; requires auth if `CODEX_GATEWAY_TOKEN` is set)
- `GET /v1/models`
- `POST /v1/embeddings` (proxies to OpenAI embeddings; requires `OPENAI_API_KEY` or `~/.codex/auth.json` with `OPENAI_API_KEY`)
- `POST /v1/chat/completions` (supports `stream`)
- `POST /v1/messages` (Anthropic Messages-compatible; supports `stream`)
- `POST /v1/messages/count_tokens` (Anthropic-compatible; currently heuristic token counting)

Tip: any OpenAI SDK that supports `base_url` should work by pointing it at this server.
Tip: Claude Code can point `ANTHROPIC_BASE_URL` at this server and use `ANTHROPIC_AUTH_TOKEN` for gateway auth.

Auth note: include `Authorization: Bearer <token>` only when you set `CODEX_GATEWAY_TOKEN` on the gateway.

### Example (non-stream)

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer devtoken" \
  -d '{
    "model":"gpt-5.6-sol",
    "messages":[{"role":"user","content":"总结一下这个仓库结构"}],
    "reasoning": {"effort":"low"},
    "stream": false
  }'
```

### Example (embeddings)

```bash
curl -s http://127.0.0.1:8000/v1/embeddings \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer devtoken" \
  -d '{
    "model":"text-embedding-3-small",
    "input":"hello world"
  }'
```

### Example (stream)

```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer devtoken" \
  -H "X-Codex-Session-Id: 0f3d5b6f-2a3b-4d78-9f50-123456789abc" \
  -d '{
    "model":"gpt-5-codex",
    "messages":[{"role":"user","content":"用一句话解释这个项目的目的"}],
    "stream": true
  }'
```

### Example (Anthropic Messages)

```bash
curl -s http://127.0.0.1:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer devtoken" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model":"claude-sonnet-4-6",
    "max_tokens": 256,
    "messages":[
      {"role":"user","content":"用一句话解释这个项目的作用"}
    ]
  }'
```

### Example (Anthropic count_tokens)

```bash
curl -s http://127.0.0.1:8000/v1/messages/count_tokens \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer devtoken" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model":"claude-sonnet-4-6",
    "messages":[
      {"role":"user","content":"hello"}
    ]
  }'
```

### Example (vision / screenshot)

When `CODEX_LOG_MODE=full` (or `CODEX_LOG_EVENTS=1`), the gateway logs `image[0] ext=... bytes=...` and `decoded_images=N` so you can confirm images are being received/decoded.

```bash
python - <<'PY' > /tmp/payload.json
import base64, json
img_b64 = base64.b64encode(open("screenshot.png","rb").read()).decode()
print(json.dumps({
  "model": "gpt-5-codex",
  "stream": False,
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "读取图片里的文字，只输出文字本身"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64," + img_b64}},
    ],
  }],
}))
PY

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer devtoken" \
  -d @/tmp/payload.json
```

PDF input uses OpenAI-style `type: "file"` parts:

```bash
python - <<'PY' > /tmp/pdf-payload.json
import base64, json
pdf_b64 = base64.b64encode(open("label.pdf","rb").read()).decode()
print(json.dumps({
  "model": "gpt-5.6-sol",
  "stream": False,
  "messages": [{
    "role": "user",
    "content": [
      {"type": "file", "file": {"filename": "label.pdf", "file_data": pdf_b64}},
      {"type": "text", "text": "Check these rules and summarize the key constraints."},
    ],
  }],
}))
PY

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer devtoken" \
  -d @/tmp/pdf-payload.json
```

## Image generation (ChatGPT subscription)

> **TL;DR** — turn your ChatGPT Plus / Pro / Team subscription into an OpenAI-compatible image-generation HTTP API. No `OPENAI_API_KEY`, no per-image billing on top of your subscription, no separate `/v1/images/generations` upstream. Just call `/v1/chat/completions` and the gateway hands you back a PNG.

### How it works

The Codex CLI's built-in `image_gen` capability is implemented as a native **Responses API tool** (`{"type": "image_generation"}`) hosted on ChatGPT's internal `backend-api/codex` endpoint — and your `~/.codex/auth.json` OAuth token is what authorises it. This gateway:

1. Reuses that OAuth token (no API key needed).
2. Injects `{"type": "image_generation"}` into the `tools` array on every chat completion request when `CODEX_ENABLE_IMAGE_GEN=1`. **Default is OFF** so plain-text completions don't get the tool silently attached.
3. Streams the upstream Responses events, intercepts the `image_generation_call` output items, and embeds the resulting base64 PNG into the assistant message content as a markdown data URI: `![](data:image/png;base64,…)`.
4. Returns a standard OpenAI Chat Completion response — any client that understands the OpenAI SDK gets the image for free.

### Requirements

- Logged-in Codex CLI (`codex login` once — creates `~/.codex/auth.json`).
- `CODEX_USE_CODEX_RESPONSES_API=1` (this is the default).
- **`CODEX_ENABLE_IMAGE_GEN=1` (must be set explicitly — default is OFF)**. Without this the gateway does not inject the `image_generation` tool and `/v1/chat/completions` returns text only.

### Example (curl)

```bash
curl -sS http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer devtoken" \
  -d '{
    "model": "gpt-5.6-sol",
    "stream": false,
    "messages": [
      {"role": "user",
       "content": "Use the image_generation tool to draw a minimal flat-design icon of a green leaf on white, 1024x1024."}
    ]
  }' | jq -r '.choices[0].message.content' \
  | python3 -c "import sys,re,base64; m=re.search(r'data:image/(\w+);base64,([A-Za-z0-9+/=]+)', sys.stdin.read()); open(f'leaf.{m.group(1)}','wb').write(base64.b64decode(m.group(2)))"
```

The script above pipes the data URI out and writes `leaf.png`.

### Example (OpenAI SDK — Python)

```python
import base64, re
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="devtoken")
resp = client.chat.completions.create(
    model="gpt-5.6-sol",
    messages=[{"role": "user", "content": "Use the image_generation tool to render a watercolour cat."}],
)
m = re.search(r"data:image/(\w+);base64,([A-Za-z0-9+/=]+)", resp.choices[0].message.content)
open(f"cat.{m.group(1)}", "wb").write(base64.b64decode(m.group(2)))
```

### Bundled helper / agent skill

A turnkey CLI helper for any agent (Claude Code, Codex, Cursor, your own scripts) ships in this repo:

```bash
python3 skills/imagegen/scripts/generate.py \
  "Studio photo of a red ceramic teacup on a wooden table, soft morning light" \
  -o assets/hero.png \
  --size 1536x1024 \
  --quiet
# stdout = assets/hero.png  (the agent can capture and use it)
```

Drop the `skills/imagegen/` directory into any agent's skill directory (or symlink it). The accompanying [`SKILL.md`](./skills/imagegen/SKILL.md) gives agents everything they need: when to use it, sizing recipes, save-path policy, error handling, and known limits.

### Supported / unsupported parameters

| Param | Status | Notes |
| --- | --- | --- |
| `size` | ✅ honoured | `auto`, `1024x1024`, `1536x1024`, `1024x1536`, `2048x2048`, `3840x2160`, … |
| `output_format` | ✅ honoured | `png` (default), `jpeg`, `webp` |
| `quality: low/medium/auto` | ✅ honoured | model picks `medium` by default |
| `quality: high` | ⚠️ silently downgraded to `medium` | ChatGPT subscription tier cap — use `OPENAI_API_KEY` and direct `/v1/images/generations` for true high |
| `background: transparent` | ❌ not supported on subscription path | requires `gpt-image-1.5` via `OPENAI_API_KEY`; or use chroma-key + local alpha extraction |
| `model` (e.g. `gpt-image-2`) | passthrough | hosted model is whatever the subscription provides; modern subscription serves `gpt-image-2`-class output |
| Edits (`/v1/images/edits`) | ❌ not yet exposed | open issue if you need it |

### Quotas and fair use

- Calls consume your ChatGPT subscription image quota — **shared with the ChatGPT web app and Codex CLI**.
- One image typically takes **15–40 seconds** at default quality.
- This is a *thin* gateway, not a "free image API for everyone" — it's meant for personal automation, agent workflows, and dogfooding from your own developer machine. Putting it behind a public proxy violates OpenAI's ToS for your subscription. Use a token (`CODEX_GATEWAY_TOKEN`) and bind to `127.0.0.1`.

### Concurrency

The ChatGPT subscription backend handles concurrent `image_generation` requests fine — measured on a Plus account, 4 simultaneous requests all returned 200 with `total_wall ≈ slowest_single` (~27s), i.e. **fully parallel, no serialization, no 429**. You don't need a semaphore in the gateway for this on personal use.

When you *might* want to add one (`CODEX_IMAGE_GEN_CONCURRENCY` is not currently a knob — open an issue if you need it):

- **Multi-user / team-shared gateway**: a burst of slow image requests can fill the worker pool (`CODEX_MAX_CONCURRENCY=100` by default) and make text completions queue behind them.
- **High-frequency batch generation** (>10 images/min sustained): you'll eventually hit subscription rate limits.

Either way, streaming chat completions and image generation are **mutually exclusive** — `stream=true` requests get HTTP 400 if `CODEX_ENABLE_IMAGE_GEN=1`, since image bytes can't be chunked back through SSE in a way that any OpenAI SDK understands. Set `stream=false` for image gen requests.

### Just want a local CLI / agent skill (no server)?

If you don't need the HTTP gateway and just want to generate images from your terminal or from an AI agent (Claude Code / Cursor / Codex Agent / OpenClaw…), use the sister project:

➡️ **[leeguooooo/chatgpt-imagegen](https://github.com/leeguooooo/chatgpt-imagegen)** — single-file Python CLI + agent skill, zero deps, same ChatGPT-subscription backend. Install via `npx skills add leeguooooo/chatgpt-imagegen -g`.

| You want | Use |
| --- | --- |
| OpenAI-compatible HTTP API, multi-app, team-shared | **this repo** (`agent-cli-to-api`) |
| Local CLI only, agent-driven, no server | [**chatgpt-imagegen**](https://github.com/leeguooooo/chatgpt-imagegen) |

Background reading on the subscription-as-image-API approach (covers both projects):

- [技术拆解：把 ChatGPT 订阅转成生图 API](https://blog.misonote.com/zh/posts/chatgpt-subscription-image-api/) — OAuth + Responses API + SSE walkthrough (zh).
- [可视化速览：一图看懂](https://blog.misonote.com/zh/posts/chatgpt-imagegen-visual-guide/) — capability matrix and flow diagram (zh).

## OpenAI SDK examples

Python:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="devtoken")
resp = client.chat.completions.create(
    model="gpt-5.6-sol",
    messages=[{"role": "user", "content": "Hi"}],
)
print(resp.choices[0].message.content)
```

TypeScript:

```ts
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8000/v1",
  apiKey: process.env.CODEX_GATEWAY_TOKEN ?? "devtoken",
});

const resp = await client.chat.completions.create({
  model: "gpt-5.6-sol",
  messages: [{ role: "user", content: "Hi" }],
});

console.log(resp.choices[0].message.content);
```

## Security notes

You are exposing an agent that can read files and run commands depending on `CODEX_SANDBOX`.
Keep it private by default, use a token, and run in an isolated environment when deploying.

## Logging & Performance Diagnosis

The gateway provides detailed timing logs to help diagnose latency:

```
INFO  claude-oauth request: url=https://api.example.com/v1/messages model=xxx auth_ms=0 prepare_ms=0
INFO  claude-oauth response: status=200 api_latency_ms=2886 parse_ms=0 total_ms=2887
```

| Metric | Description |
|--------|-------------|
| `auth_ms` | Time to load/refresh credentials |
| `prepare_ms` | Time to build request payload |
| `api_latency_ms` | **Upstream API response time** (main bottleneck) |
| `parse_ms` | Time to parse response |
| `total_ms` | Total gateway processing time |

If `api_latency_ms` ≈ `total_ms`, the latency is entirely from the upstream API (not the gateway).

### Log modes

```bash
CODEX_LOG_MODE=summary  # one line per request (default)
CODEX_LOG_MODE=qa       # show Q (question) and A (answer)
CODEX_LOG_MODE=full     # full prompt + response
```

## Performance notes (important)

If your normal `~/.codex/config.toml` has many `mcp_servers.*` entries, **Codex will start them for every `codex exec` call**
and include their tool schemas in the prompt. This can add **seconds of startup time** and **10k+ prompt tokens** per request.

For an HTTP gateway, it's usually best to run Codex with a minimal config (no MCP servers).

By default the gateway uses your system `~/.codex` (so auth stays in sync).
If you want a minimal, isolated config (no MCP servers), set `CODEX_CLI_HOME` to a gateway-local directory.
On first run it will try to copy `~/.codex/auth.json` into that directory (so you don't have to).

If you want to set it up manually or customize it:

```bash
export CODEX_CLI_HOME=$PWD/.codex-gateway-home
mkdir -p "$CODEX_CLI_HOME/.codex"
cp ~/.codex/auth.json "$CODEX_CLI_HOME/.codex/auth.json"   # or set CODEX_API_KEY instead
cat > "$CODEX_CLI_HOME/.codex/config.toml" <<'EOF'
model = "gpt-5.6-sol"
model_reasoning_effort = "low"

[projects."/path/to/your/workspace"]
trust_level = "trusted"
EOF
```

## Advanced setup (optional)

### Use `.env`

```bash
cp .env.example .env
uv run agent-cli-to-api codex --env-file .env
```

Tip: you can also opt-in to loading `.env` from the current directory with `--auto-env`.

### Auto-start on macOS (launchd)

This installs a **user** LaunchAgent and keeps the gateway running after reboot.

```bash
chmod +x scripts/install_launchd.sh
scripts/install_launchd.sh --provider codex --host 127.0.0.1 --port 8000
```

Optional env/token:

```bash
scripts/install_launchd.sh --env-file "$PWD/.env" --token devtoken
```

Uninstall:

```bash
scripts/install_launchd.sh --uninstall
```

Logs:
- `~/Library/Logs/com.codex-api.gateway.out.log`
- `~/Library/Logs/com.codex-api.gateway.err.log`

Note: `uv` must be on your PATH (e.g. `/opt/homebrew/bin/uv`).

### Prettier terminal logs (optional)

Enable colored logs (Rich handler):

```bash
export CODEX_RICH_LOGS=1
uv run agent-cli-to-api codex
```

Render assistant output as Markdown in the terminal (best-effort; prints a separate block to stderr):

```bash
export CODEX_LOG_RENDER_MARKDOWN=1
uv run agent-cli-to-api codex
```

Log request curl commands (useful for replay/debug):

```bash
export CODEX_LOG_REQUEST_CURL=1
uv run agent-cli-to-api codex
```

## Keywords (SEO)

OpenAI-compatible API, chat completions, SSE streaming, agent gateway, CLI to API proxy, Codex CLI, Cursor Agent, Claude Code, Gemini CLI.

**Image generation specifically:**
ChatGPT subscription image generation API, ChatGPT Plus image API, ChatGPT Pro image API, use ChatGPT image generation without OPENAI_API_KEY, expose ChatGPT image generation as HTTP API, gpt-image-1 / gpt-image-2 via ChatGPT subscription, Codex CLI image_gen as API, DALL-E via ChatGPT Plus subscription, no-API-key image generation proxy, OAuth-backed OpenAI image generation, /v1/chat/completions image_generation tool, Responses API image_generation tool, image_generation_call SSE events, ChatGPT subscription as image API gateway, free-tier-friendly image generation gateway, agent skill for image generation, save generated image to project directory.

**中文搜索词：** 用 ChatGPT 订阅生成图片接口、ChatGPT Plus 生图 API、不用 API key 生成图片、把 ChatGPT 订阅做成 OpenAI 兼容生图接口、ChatGPT 订阅生图代理、Codex CLI 生图能力接口化、gpt-image-2 用订阅调用、ChatGPT Plus 生图转 API、image_generation 工具网关、给 agent 用的生图 skill、生图保存到项目目录。
