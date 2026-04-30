from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping
from urllib.parse import urlparse

import httpx

from .openai_compat import ChatCompletionRequest, ChatMessage

_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

_DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_DEFAULT_CODEX_VERSION = "0.111.0"
_DEFAULT_CODEX_USER_AGENT = "codex_cli_rs/0.111.0 (Mac OS 26.0.1; arm64) Apple_Terminal/464"
_INSTALLATION_ID = str(uuid.uuid4())
_WEB_SEARCH_CITATION_HINT = (
    "Compatibility instruction: If you use web_search for this request, include a final "
    "Sources section with 3-5 exact clickable source URLs that you actually used. "
    "Do not invent URLs. Omit the Sources section if you did not use web_search."
)
_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+")


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


async def warmup_codex_auth(*, codex_cli_home: str | None) -> dict[str, str | None]:
    """
    Pre-warm Codex auth cache at startup.
    Returns a dict with status info for logging.
    """
    import logging
    _logger = logging.getLogger("uvicorn.error")
    
    t0 = time.time()
    auth = load_codex_auth(codex_cli_home=codex_cli_home)
    token = auth.api_key or auth.access_token
    t1 = time.time()
    
    if token:
        _logger.info("[codex-warmup] auth ready in %dms (has_token=True)", int((t1 - t0) * 1000))
        return {"status": "ready", "has_token": "true"}
    else:
        _logger.warning("[codex-warmup] no auth token found in %dms", int((t1 - t0) * 1000))
        return {"status": "no_token", "has_token": "false"}


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
    session_id: str | None = None,
    version: str = _DEFAULT_CODEX_VERSION,
    user_agent: str = _DEFAULT_CODEX_USER_AGENT,
) -> dict[str, str]:
    # Match Codex CLI headers (see CLIProxyAPI applyCodexHeaders).
    sid = session_id or str(uuid.uuid4())
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
        "Connection": "Keep-Alive",
        "version": version,
        "session_id": sid,
        "x-client-request-id": sid,
        "User-Agent": user_agent,
        "originator": "codex_cli_rs",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def extract_codex_usage_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower.startswith("x-codex-") or lower == "x-request-id":
            out[key] = value
    return out


def extract_codex_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    output = response.get("output")
    if not isinstance(output, list):
        return []

    tool_calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        func = item.get("function")
        name = None
        arguments = None
        if isinstance(func, dict):
            name = func.get("name")
            arguments = func.get("arguments")
        if item_type in {"tool_call", "function_call"} or (
            isinstance(item.get("call_id"), str) and isinstance(item.get("name"), str)
        ):
            if name is None:
                name = item.get("name")
            if arguments is None:
                arguments = item.get("arguments")
            call_id = item.get("call_id") or item.get("id")
            if not isinstance(call_id, str) or not call_id:
                call_id = f"call_{len(tool_calls) + 1}"
            if not isinstance(name, str) or not name:
                name = "tool"
            if not isinstance(arguments, str):
                try:
                    arguments = json.dumps(arguments or {}, ensure_ascii=False)
                except Exception:
                    arguments = "{}"
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
    return tool_calls


def _prompt_dir() -> Path:
    return Path(__file__).with_name("codex_instructions")


_INSTRUCTIONS_CACHE: dict[str, str] = {}


def _convert_openai_tool_for_codex(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") != "function":
        return tool
    func = tool.get("function")
    if not isinstance(func, dict):
        return tool
    name = func.get("name") or tool.get("name")
    if not isinstance(name, str) or not name:
        return tool
    out: dict[str, Any] = {"type": "function", "name": name}
    description = func.get("description")
    if isinstance(description, str) and description:
        out["description"] = description
    parameters = func.get("parameters")
    if isinstance(parameters, dict):
        out["parameters"] = parameters
    strict = func.get("strict")
    if strict is None:
        strict = tool.get("strict")
    if isinstance(strict, bool):
        out["strict"] = strict
    return out


def _convert_openai_tools_for_codex(tools: list[Any]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        converted.append(_convert_openai_tool_for_codex(tool))
    return converted


def _convert_openai_tool_choice_for_codex(choice: Any) -> Any:
    if choice in {"auto", "none"}:
        return choice
    if isinstance(choice, dict) and choice.get("type") == "function":
        func = choice.get("function")
        if isinstance(func, dict) and isinstance(func.get("name"), str):
            return {"type": "function", "name": func["name"]}
    return choice


def _extract_openai_tool_calls(message: ChatMessage) -> list[dict[str, Any]]:
    extra = getattr(message, "model_extra", None)
    if not isinstance(extra, dict):
        extra = {}
    tool_calls = extra.get("tool_calls")
    if tool_calls is None:
        function_call = extra.get("function_call")
        if isinstance(function_call, dict):
            tool_calls = [{"type": "function", "function": function_call}]
    if not isinstance(tool_calls, list):
        return []
    return [call for call in tool_calls if isinstance(call, dict)]


def _append_function_calls_from_message(out_input: list[dict[str, Any]], message: ChatMessage) -> None:
    tool_calls = _extract_openai_tool_calls(message)
    if not tool_calls:
        return
    for idx, call in enumerate(tool_calls, start=1):
        call_id = call.get("id") or call.get("call_id") or call.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_{idx}"
        func = call.get("function")
        name = None
        arguments = None
        if isinstance(func, dict):
            name = func.get("name")
            arguments = func.get("arguments")
        if name is None:
            name = call.get("name")
        if arguments is None:
            arguments = call.get("arguments")
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(arguments, str):
            try:
                arguments = json.dumps(arguments or {}, ensure_ascii=False)
            except Exception:
                arguments = "{}"
        out_input.append(
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            }
        )


def _extract_tool_call_id_from_message(message: ChatMessage) -> str | None:
    tool_call_id = None
    if isinstance(message.content, dict):
        tool_call_id = message.content.get("tool_call_id") or message.content.get("call_id")
    if tool_call_id is None:
        extra = getattr(message, "model_extra", None)
        if isinstance(extra, dict):
            tool_call_id = extra.get("tool_call_id") or extra.get("call_id")
    if tool_call_id is None and hasattr(message, "tool_call_id"):
        tool_call_id = getattr(message, "tool_call_id")
    if isinstance(tool_call_id, str) and tool_call_id:
        return tool_call_id
    return None


def _load_prompt_file(filename: str) -> str:
    if filename in _INSTRUCTIONS_CACHE:
        return _INSTRUCTIONS_CACHE[filename]
    content = (_prompt_dir() / filename).read_text(encoding="utf-8")
    _INSTRUCTIONS_CACHE[filename] = content
    return content


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


def _codex_input_file_part(part: dict[str, Any]) -> dict[str, Any] | None:
    ptype = part.get("type")
    if ptype == "file":
        source = part.get("file")
        if not isinstance(source, dict):
            return None
    elif ptype == "input_file":
        source = part
    else:
        return None

    out: dict[str, Any] = {"type": "input_file"}
    for key in ("file_id", "file_data", "filename", "file_url"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out if len(out) > 1 else None


def _web_search_citation_hint_message() -> dict[str, Any]:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": _WEB_SEARCH_CITATION_HINT}],
    }


def convert_chat_completions_to_codex_responses(
    req: ChatCompletionRequest,
    *,
    model_name: str,
    force_stream: bool,
    reasoning_effort_override: str | None = None,
    allow_tools: bool = False,
    enable_search: bool = False,
) -> dict[str, Any]:
    instructions = codex_instructions_for_model(model_name)

    # Codex backend requires `instructions` to match an allowlisted prompt exactly.
    out: dict[str, Any] = {
        "model": model_name,
        "stream": bool(force_stream),
        "instructions": instructions,
        "input": [],
        "tools": [],
        "store": False,
        "prompt_cache_key": _INSTALLATION_ID,
        "client_metadata": {"x-codex-installation-id": _INSTALLATION_ID},
        "text": {"verbosity": "low"},
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
    extra = getattr(req, "model_extra", None) or {}
    if allow_tools and isinstance(extra, dict):
        tools = extra.get("tools")
        if isinstance(tools, list) and tools:
            out["tools"] = _convert_openai_tools_for_codex(tools)
        tool_choice = extra.get("tool_choice")
        if tool_choice is not None:
            out["tool_choice"] = _convert_openai_tool_choice_for_codex(tool_choice)
        parallel_tool_calls = extra.get("parallel_tool_calls")
        if parallel_tool_calls is not None:
            out["parallel_tool_calls"] = parallel_tool_calls

    if enable_search:
        tools = out["tools"]
        if isinstance(tools, list) and not any(
            isinstance(tool, dict) and tool.get("type") == "web_search" for tool in tools
        ):
            tools.append({"type": "web_search", "external_web_access": True})

    if not allow_tools and not enable_search:
        # For proxying chat completions / UI automation, we generally want pure text output.
        # Codex backend can otherwise attempt MCP/tool calls (which are not available here),
        # leading to noisy logs and occasional refusals. Users who need tools should use
        # `codex exec` via this gateway instead.
        out["tool_choice"] = "none"
        out["parallel_tool_calls"] = False
    out["include"] = ["reasoning.encrypted_content"]

    if enable_search:
        out["input"].append(_web_search_citation_hint_message())

    for message in req.messages:
        role = message.role

        if role == "tool":
            # Tool output (function_call_output). Best-effort only.
            tool_call_id = _extract_tool_call_id_from_message(message)
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
            if ptype in {"file", "input_file"} and role == "user":
                file_part = _codex_input_file_part(part)
                if file_part is not None:
                    msg["content"].append(file_part)

        out["input"].append(msg)
        if role == "assistant":
            _append_function_calls_from_message(out["input"], message)

    return out


def _has_web_search_call(response: dict[str, Any]) -> bool:
    output = response.get("output")
    if not isinstance(output, list):
        return False
    return any(isinstance(item, dict) and item.get("type") == "web_search_call" for item in output)


def _trim_url_match(url: str) -> str:
    while url and url[-1] in ".,;:!?)]}":
        url = url[:-1]
    return url


def _url_title(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    host = parsed.netloc or url
    if host.startswith("www."):
        host = host[4:]
    return host


def add_synthetic_web_search_citations(response: dict[str, Any]) -> dict[str, Any]:
    """
    ChatGPT Codex backend currently exposes web_search calls but not url_citation
    annotations. When the model prints URLs in text, synthesize compatible
    annotations so Responses clients can still render citations.
    """
    if not _has_web_search_call(response):
        return response

    output = response.get("output")
    if not isinstance(output, list):
        return response

    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            existing = part.get("annotations")
            if isinstance(existing, list) and existing:
                continue
            text = part.get("text")
            if not isinstance(text, str) or "http" not in text:
                continue

            annotations: list[dict[str, Any]] = []
            for match in _URL_RE.finditer(text):
                raw_url = match.group(0)
                url = _trim_url_match(raw_url)
                if not url:
                    continue
                annotations.append(
                    {
                        "type": "url_citation",
                        "start_index": match.start(),
                        "end_index": match.start() + len(url),
                        "url": url,
                        "title": _url_title(url),
                    }
                )
            if annotations:
                part["annotations"] = annotations

    return response


async def iter_codex_responses_events(
    *,
    base_url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    response_headers_cb: Callable[[dict[str, str]], None] | None = None,
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
            if response_headers_cb is not None:
                try:
                    response_headers_cb(dict(resp.headers))
                except Exception:
                    pass

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
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]] | None]:
    chunks: list[str] = []
    usage: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = None

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
                    # Preserve backend-provided details when available (helps verify reasoning effort).
                    "prompt_tokens_details": u.get("input_tokens_details") if isinstance(u.get("input_tokens_details"), dict) else {},
                    "completion_tokens_details": u.get("output_tokens_details") if isinstance(u.get("output_tokens_details"), dict) else {},
                }
            if isinstance(resp, dict):
                parsed = extract_codex_tool_calls(resp)
                if parsed:
                    tool_calls = parsed
            break

    return "".join(chunks), usage, tool_calls


async def collect_codex_responses_native_response(
    events: AsyncIterator[dict[str, Any]],
) -> dict[str, Any]:
    output_items: dict[int, dict[str, Any]] = {}

    async for evt in events:
        if evt.get("type") in {"response.output_item.added", "response.output_item.done"}:
            item = evt.get("item")
            output_index = evt.get("output_index")
            if isinstance(item, dict) and isinstance(output_index, int):
                output_items[output_index] = item
        if evt.get("type") == "response.completed":
            response = evt.get("response")
            if isinstance(response, dict):
                if output_items:
                    response["output"] = [output_items[index] for index in sorted(output_items)]
                add_synthetic_web_search_citations(response)
                return response
            break

    raise RuntimeError("codex responses failed: missing response.completed payload")


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
