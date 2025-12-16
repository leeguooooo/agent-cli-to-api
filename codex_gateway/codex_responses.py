from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import httpx

from .openai_compat import ChatCompletionRequest, ChatMessage

_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

_DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_DEFAULT_CODEX_VERSION = "0.21.0"
_DEFAULT_CODEX_USER_AGENT = "codex_cli_rs/0.50.0 (Mac OS 26.0.1; arm64) Apple_Terminal/464"


@dataclass(frozen=True)
class CodexAuth:
    api_key: str | None
    access_token: str | None
    refresh_token: str | None
    account_id: str | None
    last_refresh: str | None


def _auth_json_path(codex_cli_home: str | None) -> Path:
    home = Path(codex_cli_home) if codex_cli_home else Path.home()
    return home / ".codex" / "auth.json"


def load_codex_auth(*, codex_cli_home: str | None) -> CodexAuth:
    path = _auth_json_path(codex_cli_home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return CodexAuth(api_key=None, access_token=None, refresh_token=None, account_id=None, last_refresh=None)

    api_key = raw.get("OPENAI_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        api_key = None

    tokens = raw.get("tokens") or {}
    if not isinstance(tokens, dict):
        tokens = {}

    def _get_token(name: str) -> str | None:
        val = tokens.get(name)
        if isinstance(val, str) and val.strip():
            return val.strip()
        return None

    last_refresh = raw.get("last_refresh")
    if not isinstance(last_refresh, str) or not last_refresh.strip():
        last_refresh = None

    return CodexAuth(
        api_key=api_key,
        access_token=_get_token("access_token"),
        refresh_token=_get_token("refresh_token"),
        account_id=_get_token("account_id"),
        last_refresh=last_refresh,
    )


async def _refresh_access_token(
    *,
    refresh_token: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(
            _OAUTH_TOKEN_URL,
            data={
                "client_id": _OAUTH_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "openid profile email",
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def maybe_refresh_codex_auth(
    *,
    codex_cli_home: str | None,
    timeout_seconds: int,
) -> CodexAuth:
    """
    Best-effort refresh for Codex OAuth tokens. This is only used when requests
    fail with auth errors; it is not proactively refreshed by time.
    """
    auth = load_codex_auth(codex_cli_home=codex_cli_home)
    if not auth.refresh_token:
        return auth

    try:
        token_resp = await _refresh_access_token(refresh_token=auth.refresh_token, timeout_seconds=timeout_seconds)
    except Exception:
        return auth

    access = token_resp.get("access_token")
    refresh = token_resp.get("refresh_token") or auth.refresh_token
    if not isinstance(access, str) or not access.strip():
        return auth

    # Persist to auth.json for subsequent requests (do not log secrets).
    path = _auth_json_path(codex_cli_home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    tokens["access_token"] = access
    if isinstance(refresh, str) and refresh.strip():
        tokens["refresh_token"] = refresh
    raw["tokens"] = tokens
    raw["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass

    return CodexAuth(
        api_key=auth.api_key,
        access_token=access.strip(),
        refresh_token=(refresh.strip() if isinstance(refresh, str) and refresh.strip() else auth.refresh_token),
        account_id=auth.account_id,
        last_refresh=raw.get("last_refresh") if isinstance(raw.get("last_refresh"), str) else auth.last_refresh,
    )


def build_codex_headers(
    *,
    token: str,
    account_id: str | None,
    version: str = _DEFAULT_CODEX_VERSION,
    user_agent: str = _DEFAULT_CODEX_USER_AGENT,
) -> dict[str, str]:
    # Match Codex CLI headers (see CLIProxyAPI applyCodexHeaders).
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
        "Connection": "Keep-Alive",
        "Version": version,
        "Openai-Beta": "responses=experimental",
        "Session_id": str(uuid.uuid4()),
        "User-Agent": user_agent,
    }
    if account_id:
        headers["Originator"] = "codex_cli_rs"
        headers["Chatgpt-Account-Id"] = account_id
    return headers


def _prompt_dir() -> Path:
    return Path(__file__).with_name("codex_instructions")


def _load_prompt_file(filename: str) -> str:
    return (_prompt_dir() / filename).read_text(encoding="utf-8")


def codex_instructions_for_model(model_name: str) -> str:
    m = (model_name or "").lower()
    if "codex-max" in m:
        return _load_prompt_file("gpt-5.1-codex-max_prompt.md")
    if "codex" in m:
        return _load_prompt_file("gpt_5_codex_prompt.md")
    if "5.1" in m:
        return _load_prompt_file("gpt_5_1_prompt.md")
    if "5.2" in m:
        return _load_prompt_file("gpt_5_2_prompt.md")
    return _load_prompt_file("prompt.md")


def _content_parts(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        return [p for p in content if isinstance(p, dict)]
    return [{"type": "text", "text": str(content)}]


def convert_chat_completions_to_codex_responses(
    req: ChatCompletionRequest,
    *,
    model_name: str,
    force_stream: bool,
    reasoning_effort_override: str | None = None,
) -> dict[str, Any]:
    instructions = codex_instructions_for_model(model_name)

    # Codex backend requires `instructions` to match an allowlisted prompt exactly.
    out: dict[str, Any] = {
        "model": model_name,
        "stream": bool(force_stream),
        "instructions": instructions,
        "input": [],
        "store": False,
    }

    # Map reasoning.effort (OpenAI chat compat accepts `reasoning_effort`).
    effort: str | None = None
    if reasoning_effort_override in {"low", "medium", "high"}:
        effort = reasoning_effort_override
    else:
        extra = getattr(req, "model_extra", None) or {}
        if isinstance(extra, dict):
            if isinstance(extra.get("reasoning_effort"), str):
                effort = extra["reasoning_effort"].strip() or None
            reasoning = extra.get("reasoning")
            if effort is None and isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
                effort = reasoning["effort"].strip() or None
    if effort not in {"low", "medium", "high"}:
        effort = "medium"
    out["reasoning"] = {"effort": effort, "summary": "auto"}
    # For proxying chat completions / UI automation, we generally want pure text output.
    # Codex backend can otherwise attempt MCP/tool calls (which are not available here),
    # leading to noisy logs and occasional refusals. Users who need tools should use
    # `codex exec` via this gateway instead.
    out["tool_choice"] = "none"
    out["parallel_tool_calls"] = False
    out["include"] = ["reasoning.encrypted_content"]

    for message in req.messages:
        role = message.role

        if role == "tool":
            # Tool output (function_call_output). Best-effort only.
            tool_call_id = None
            if isinstance(message.content, dict) and isinstance(message.content.get("tool_call_id"), str):
                tool_call_id = message.content["tool_call_id"]
            if tool_call_id is None and hasattr(message, "tool_call_id"):
                tool_call_id = getattr(message, "tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                # If missing, keep as a user message to avoid request rejection.
                role = "user"
            else:
                out["input"].append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call_id,
                        "output": str(message.content or ""),
                    }
                )
                continue

        # Codex backend does not accept role=system; map to user.
        if role in {"system", "developer"}:
            role = "user"

        msg: dict[str, Any] = {"type": "message", "role": role, "content": []}
        parts = _content_parts(message.content)
        for part in parts:
            ptype = part.get("type")
            if ptype == "text" and isinstance(part.get("text"), str):
                msg["content"].append(
                    {
                        "type": ("output_text" if role == "assistant" else "input_text"),
                        "text": part["text"],
                    }
                )
            if ptype in {"image_url", "input_image"} and role == "user":
                image = part.get("image_url")
                url = None
                if isinstance(image, dict) and isinstance(image.get("url"), str):
                    url = image["url"]
                elif isinstance(image, str):
                    url = image
                if isinstance(url, str) and url:
                    msg["content"].append({"type": "input_image", "image_url": url})

        out["input"].append(msg)

    return out


async def iter_codex_responses_events(
    *,
    base_url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    url = base_url.rstrip("/") + "/responses"
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=headers, json=payload, timeout=timeout_seconds) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="ignore")
                msg = body.strip()
                if msg:
                    raise RuntimeError(f"codex responses failed: {resp.status_code}: {msg}")
                raise RuntimeError(f"codex responses failed: {resp.status_code}")

            async for line in resp.aiter_lines():
                if not line:
                    continue
                line = line.strip()
                if not line or line.startswith(":") or line.startswith("event:"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    if event_callback is not None:
                        try:
                            event_callback(obj)
                        except Exception:
                            pass
                    yield obj


async def collect_codex_responses_text_and_usage(
    events: AsyncIterator[dict[str, Any]],
) -> tuple[str, dict[str, int] | None]:
    chunks: list[str] = []
    usage: dict[str, int] | None = None

    async for evt in events:
        t = evt.get("type")
        if t == "response.output_text.delta" and isinstance(evt.get("delta"), str):
            chunks.append(evt["delta"])
        # Some very short responses can arrive only as a final "done" event.
        if t == "response.output_text.done" and not chunks and isinstance(evt.get("text"), str):
            chunks.append(evt["text"])
        if t == "response.completed":
            resp = evt.get("response") or {}
            u = resp.get("usage") if isinstance(resp, dict) else None
            if isinstance(u, dict):
                prompt_tokens = int(u.get("input_tokens") or 0)
                completion_tokens = int(u.get("output_tokens") or 0)
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
            break

    return "".join(chunks), usage


async def stream_codex_responses_deltas_with_keepalive(
    *,
    base_url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    keepalive_seconds: int,
) -> AsyncIterator[dict[str, Any] | None]:
    """
    Yield parsed Codex SSE `data:` JSON objects, with periodic `None` to indicate keepalive ticks.
    """
    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for evt in iter_codex_responses_events(
                base_url=base_url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
            ):
                await q.put(evt)
        except Exception as e:
            await q.put({"_error": str(e)})
        finally:
            await q.put(None)

    task = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=keepalive_seconds)
            except (asyncio.TimeoutError, TimeoutError):
                yield None
                continue
            if item is None:
                break
            yield item
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
