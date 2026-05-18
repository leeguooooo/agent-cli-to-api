#!/usr/bin/env python3
"""Generate an image via the codex-api gateway and save it into the project.

Uses your ChatGPT subscription (no OPENAI_API_KEY needed). The gateway must be
running locally (default http://127.0.0.1:8000) with CODEX_USE_CODEX_RESPONSES_API
enabled (this is the default).

Usage:
    python generate.py "a watercolor painting of a cat" -o assets/cat.png
    python generate.py "logo for a coffee shop" --size 1024x1024
    python generate.py "hero banner" -o web/hero.png --size 1536x1024
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = os.environ.get("CODEX_API_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_TOKEN = os.environ.get("CODEX_API_TOKEN", "devtoken")
DEFAULT_MODEL = os.environ.get("CODEX_API_IMAGE_MODEL", "gpt-5.5")


def _build_prompt(user_prompt: str, size: str | None, output_format: str) -> str:
    parts = [
        "Use the image_generation tool to render the following.",
        f"Request: {user_prompt}",
        f"Output format: {output_format}.",
    ]
    if size and size != "auto":
        parts.append(f"Size: {size}.")
    parts.append("Do not include explanatory text in your reply — produce only the image.")
    return " ".join(parts)


def _post(base_url: str, token: str, body: dict, timeout: int) -> dict:
    url = base_url.rstrip("/") + "/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            # Cloudflare (in front of a remote gateway) returns 403 / error 1010 to
            # the default Python-urllib UA. Send an explicit one.
            "User-Agent": "codex-api-imagegen/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")
        raise SystemExit(f"gateway returned HTTP {e.code}: {body_text}") from None
    except urllib.error.URLError as e:
        raise SystemExit(
            f"failed to reach gateway at {url}: {e.reason}\n"
            "Is the gateway running?  `cd codex-api && uv run agent-cli-to-api codex`"
        ) from None


def _extract_first_image(content: str) -> tuple[str, bytes] | None:
    m = re.search(r"data:image/(\w+);base64,([A-Za-z0-9+/=]+)", content)
    if not m:
        return None
    fmt = m.group(1).lower()
    try:
        data = base64.b64decode(m.group(2))
    except Exception:
        return None
    return fmt, data


def _default_out_path(prompt: str, fmt: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:60] or "image"
    base = Path("assets/generated")
    base.mkdir(parents=True, exist_ok=True)
    i = 1
    while True:
        candidate = base / (f"{slug}.{fmt}" if i == 1 else f"{slug}-{i}.{fmt}")
        if not candidate.exists():
            return candidate
        i += 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an image via codex-api gateway.")
    parser.add_argument("prompt", help="Description of the image you want")
    parser.add_argument("-o", "--out", help="Output file path (default: assets/generated/<slug>.png)")
    parser.add_argument("--size", default="auto",
                        help="auto | 1024x1024 | 1536x1024 | 1024x1536 | 2048x2048 | 3840x2160 | 2160x3840 ...")
    parser.add_argument("--format", default="png", choices=["png", "jpeg", "webp"],
                        help="Output image format")
    parser.add_argument("--base-url", default=DEFAULT_BASE,
                        help=f"Gateway base URL (default: {DEFAULT_BASE})")
    parser.add_argument("--token", default=DEFAULT_TOKEN,
                        help="Gateway bearer token (default: read from $CODEX_API_TOKEN)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Chat model that hosts the image_generation tool (default: {DEFAULT_MODEL})")
    parser.add_argument("--timeout", type=int, default=180, help="Request timeout in seconds")
    parser.add_argument("--quiet", action="store_true", help="Print only the output path")
    args = parser.parse_args()

    user_prompt = _build_prompt(args.prompt, args.size, args.format)
    body = {
        "model": args.model,
        "stream": False,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    if not args.quiet:
        print(f"-> POST {args.base_url}/v1/chat/completions", file=sys.stderr)
        print(f"   prompt: {args.prompt[:80]}{'...' if len(args.prompt) > 80 else ''}", file=sys.stderr)
        print(f"   size={args.size}  format={args.format}", file=sys.stderr)

    resp = _post(args.base_url, args.token, body, args.timeout)
    if "choices" not in resp:
        print(json.dumps(resp, indent=2), file=sys.stderr)
        raise SystemExit("gateway response missing choices")

    content = resp["choices"][0].get("message", {}).get("content", "")
    found = _extract_first_image(content)
    if not found:
        print("Model did not return an image. Raw text was:", file=sys.stderr)
        print(content[:1000], file=sys.stderr)
        raise SystemExit(2)

    fmt, data = found
    out_path = Path(args.out) if args.out else _default_out_path(args.prompt, fmt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)

    if args.quiet:
        print(out_path)
    else:
        print(f"-> saved: {out_path}  ({len(data):,} bytes, {fmt})", file=sys.stderr)
        print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
