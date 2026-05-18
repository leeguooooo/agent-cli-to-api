---
name: "codex-api-imagegen"
description: "Generate raster images (PNG/JPEG/WebP) via the local codex-api gateway, powered by the user's ChatGPT subscription — no OPENAI_API_KEY needed. Use when an agent needs to create a brand-new bitmap asset for the current project (photos, illustrations, icons, hero banners, mockups, sprites, concept art) and the output should be a bitmap file saved into the workspace. Do not use when the task is better solved by editing existing SVG/vector assets, writing code-native graphics (HTML/CSS/canvas), or extending an established repo icon system."
---

# codex-api Image Generation Skill

Generates images by calling the local **codex-api** gateway's
`/v1/chat/completions` endpoint with `image_generation` tool enabled. The
gateway forwards the request to ChatGPT's Responses API and returns the PNG
embedded as a data URI in the assistant message. This helper extracts that
PNG and writes it to a path inside the current project.

## Prerequisites

- The codex-api gateway is running locally (default: `http://127.0.0.1:8000`).
  - Start it with: `cd ~/github.com/codex-api && uv run agent-cli-to-api codex`
  - Or rely on the user's launchd auto-start (`com.codex-api.gateway`).
- The user has a logged-in ChatGPT subscription (`~/.codex/auth.json` exists,
  populated by `codex login` in the Codex CLI). No `OPENAI_API_KEY` required.
- Gateway settings (defaults are already correct on a fresh install):
  - `CODEX_USE_CODEX_RESPONSES_API=1` (default)
  - `CODEX_ENABLE_IMAGE_GEN=1` (default)

If the gateway is unreachable, point the agent at the `codex-api` repo and ask
the user to start it — do not silently fall back.

## When to use

- The user asks for a new photo, illustration, icon, hero banner, sprite,
  cover image, infographic-style asset, product mockup, concept art, or any
  other bitmap deliverable for the current project.
- The asset is intended to be checked into the repo (or used as a build input)
  rather than ephemeral preview.

## When not to use

- The user wants an SVG icon that matches an existing in-repo vector set.
- The task is better solved with code (HTML/CSS, canvas, Mermaid, PlantUML).
- The user is editing an image they already have on disk — for that, use the
  Codex CLI's bundled `imagegen` skill or call `/v1/images/edits` directly
  with their `OPENAI_API_KEY` (this skill is generate-only at present).

## Credentials — DO NOT pass tokens on the command line

Agents running this skill **must not** put `CODEX_API_TOKEN=...` or `--token ...`
on the command line, because tool calls are echoed into transcripts and chat
logs. The token would leak into anyone who can see the conversation.

The script reads the gateway URL and token from three sources, in priority
order:

1. CLI flag (`--base-url`, `--token`) — **only when the user explicitly types
   them**; never construct these flags from a token your agent has memorised.
2. Environment variable (`CODEX_API_BASE_URL`, `CODEX_API_TOKEN`) — works if
   the user exports them in their shell config.
3. Plain file (`~/.config/codex-api/base_url` and `~/.config/codex-api/token`,
   one line each, recommended mode `600`) — preferred for agent workflows.

**One-time setup (user runs this, once):**

```bash
mkdir -p ~/.config/codex-api
chmod 700 ~/.config/codex-api
printf 'https://your-gateway.example.com' > ~/.config/codex-api/base_url
printf 'your-gateway-token'              > ~/.config/codex-api/token
chmod 600 ~/.config/codex-api/{base_url,token}
```

After that, the agent just runs `python3 scripts/generate.py "<prompt>"` with
**no env, no token flag** — credentials come from the file silently.

If the file is missing and no env vars are set, the script falls back to
`http://127.0.0.1:8000` + `devtoken` (the default for a locally-running
gateway with no auth configured).

## How to invoke

```bash
python3 scripts/generate.py "<prompt>" [options]
```

Common options:

| Flag | Default | Notes |
| --- | --- | --- |
| `-o`, `--out PATH` | `assets/generated/<slug>.png` | Where to write the file. Parent dirs are created. |
| `--size` | `auto` | `1024x1024`, `1536x1024`, `1024x1536`, `2048x2048`, `3840x2160`, etc. The subscription path honours the size. |
| `--format` | `png` | `png` \| `jpeg` \| `webp` |
| `--model` | `gpt-5.5` | The chat model that hosts `image_generation` |
| `--base-url` | from `$CODEX_API_BASE_URL` → `~/.config/codex-api/base_url` → `http://127.0.0.1:8000` | Override gateway URL. **Don't pass this from an agent — set the file once.** |
| `--token` | from `$CODEX_API_TOKEN` → `~/.config/codex-api/token` → `devtoken` | Bearer token for the gateway. **Don't pass this from an agent — set the file once.** |
| `--quiet` | off | Print only the output path on stdout |

The script prints **just the saved file path on stdout** (and progress info on
stderr), so the agent can capture it directly:

```bash
OUT=$(python3 scripts/generate.py "..." --quiet)
echo "saved to: $OUT"
```

## Save-path policy

1. **Always save into the workspace**, never to `/tmp` or `$HOME`.
2. If the user names a destination, pass it via `-o`.
3. If the asset is for the project, save into a sensible repo subdirectory
   (e.g. `assets/`, `public/`, `static/`, `docs/img/`, `web/img/`).
4. Never overwrite an existing file unless the user explicitly asked for it
   (`-o` will silently overwrite — the script does not autoshield). The
   default path with `--out` omitted auto-numbers (`name.png`, `name-2.png`).
5. After saving, **always echo back the path** to the user.

## Workflow for the agent

1. **Clarify** the request enough to write a 1-3 sentence visual prompt:
   subject, style, composition, mood, constraints.
2. **Pick the size/format** based on intended use:
   - icon → `1024x1024 png`
   - landing hero → `1536x1024 png` (landscape)
   - mobile splash → `1024x1536 png` (portrait)
   - photo → `2048x1152 png` or `auto`
   - transparent cutout → not currently supported by this skill on the
     subscription path; tell the user it requires an `OPENAI_API_KEY` and
     the Codex CLI's bundled imagegen skill.
3. **Pick the output path** under the workspace.
4. **Call the script**, capture stdout (= path), report it back.
5. **Inspect the result if necessary.** If the image is clearly wrong, iterate
   with a single targeted change to the prompt — do not chain many calls
   blindly (each costs subscription quota).

## Quality and limits

- Quality is auto-selected by the model. Asking for `quality: high` via the
  tool params is silently downgraded to `medium` on the subscription path —
  this is a tier cap, not a bug. Photorealistic 1536x1024 medium-quality
  output is excellent.
- A single image typically takes 15-40 seconds to generate.
- Each call consumes ChatGPT subscription quota — the rate limit is shared
  with the user's interactive ChatGPT/Codex usage. Don't loop over many
  variants without permission.

## Example

```bash
python3 scripts/generate.py \
  "Minimal flat-design illustration of a green leaf, white background, centered, no text" \
  -o assets/brand/leaf-icon.png \
  --size 1024x1024 \
  --quiet
```

Output (stdout): `assets/brand/leaf-icon.png`

## Error handling

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `failed to reach gateway` | gateway not running | start it (see Prerequisites) |
| HTTP 401 `Missing Authorization` | wrong/missing token | set `CODEX_API_TOKEN` or `--token` |
| HTTP 403 `error code: 1010` | Cloudflare in front of the gateway is blocking the request | the script already sets a User-Agent; if you removed it, restore it. Also confirm the gateway hostname is in your CF allowlist. |
| HTTP 400 `requires a newer version of Codex` | gateway is sending `version: 0.111.0` because it can't detect the local codex CLI version | **server-side fix**: pull the latest codex-api (commit `fbf9316` or newer reads `~/.codex/version.json`), or ensure `codex --version` succeeds in the gateway's environment (launchd PATH must reach `node` for the codex shim). Bumping the local `codex` CLI alone does not help if detection still fails. |
| HTTP 500 `env: node: No such file` | gateway falling back to `codex exec` subprocess | confirm `CODEX_USE_CODEX_RESPONSES_API=1` (default in current codex-api) |
| `Model did not return an image` | model returned text only | rephrase prompt to explicitly say "use the image_generation tool" |
| HTTP 429 / quota errors | subscription rate-limited | wait, or switch to API-key path (`gpt-image-2` direct) |

## Internals (for future maintainers)

- Gateway request body: standard chat completions, the model is instructed to
  call the `image_generation` Responses-API tool.
- Gateway injects `tools: [{"type": "image_generation"}]` into the upstream
  `/responses` call automatically when `CODEX_ENABLE_IMAGE_GEN=1` (default).
- Gateway collects `response.output_item.done` events whose `item.type ==
  "image_generation_call"` and embeds the base64 PNG as a markdown
  `![](data:image/png;base64,…)` part of the assistant message content.
- The script extracts the first such data URI and writes it to disk.
