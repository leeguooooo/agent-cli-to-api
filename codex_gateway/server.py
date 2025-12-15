from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import aclosing

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .codex_cli import collect_codex_text_and_usage_from_events, iter_codex_events
from .config import settings
from .openai_compat import ChatCompletionRequest, ErrorResponse, messages_to_prompt

app = FastAPI(title="codex-api-gateway", version="0.1.0")
logger = logging.getLogger("uvicorn.error")

if settings.cors_origins.strip():
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )


_semaphore = None


def _get_semaphore():
    global _semaphore
    if _semaphore is None:
        import asyncio

        _semaphore = asyncio.Semaphore(settings.max_concurrency)
    return _semaphore


def _extract_reasoning_effort(req: ChatCompletionRequest) -> str | None:
    extra = getattr(req, "model_extra", None) or {}
    if isinstance(extra, dict):
        direct = extra.get("reasoning_effort")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        reasoning = extra.get("reasoning")
        if isinstance(reasoning, dict):
            effort = reasoning.get("effort")
            if isinstance(effort, str) and effort.strip():
                return effort.strip()
    return None


def _check_auth(authorization: str | None) -> None:
    token = settings.bearer_token
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <token>")
    if authorization.removeprefix("Bearer ").strip() != token:
        raise HTTPException(status_code=403, detail="Invalid token")


def _openai_error(message: str, *, status_code: int = 500) -> JSONResponse:
    payload = ErrorResponse(
        error={
            "message": message,
            "type": "codex_gateway_error",
            "param": None,
            "code": None,
        }
    ).model_dump()
    return JSONResponse(status_code=status_code, content=payload)


def _maybe_strip_answer_tags(text: str) -> str:
    """
    Optional compatibility shim for clients that parse a single do(...)/finish(...)
    expression and choke on trailing XML tags like </answer>.
    """
    if not settings.strip_answer_tags:
        return text
    if not text:
        return text
    return text.replace("</answer>", "")


def _truncate_for_log(text: str) -> str:
    limit = settings.log_max_chars
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... (truncated, {len(text)} chars total)"


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/v1/models")
async def list_models(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    models = settings.advertised_models[:] if settings.advertised_models else [settings.default_model]
    if settings.model_aliases:
        models.extend(settings.model_aliases.keys())
        models.extend(settings.model_aliases.values())
    # Preserve order while deduping.
    seen: set[str] = set()
    unique_models: list[str] = []
    for m in models:
        if not m or m in seen:
            continue
        seen.add(m)
        unique_models.append(m)
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": 0, "owned_by": "local"} for m in unique_models],
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _check_auth(authorization)

    requested_model = req.model or settings.default_model
    model = settings.model_aliases.get(requested_model, requested_model)
    reasoning_effort = _extract_reasoning_effort(req) or settings.model_reasoning_effort
    prompt = messages_to_prompt(req.messages)
    if len(prompt) > settings.max_prompt_chars:
        return _openai_error(f"Prompt too large ({len(prompt)} chars)", status_code=413)

    created = int(time.time())
    resp_id = f"chatcmpl-{uuid.uuid4().hex}"

    sem = _get_semaphore()
    try:
        if settings.debug_log:
            logger.info(
                "[%s] /v1/chat/completions model=%s resolved_model=%s stream=%s effort=%s prompt_chars=%d",
                resp_id,
                requested_model,
                model,
                req.stream,
                reasoning_effort,
                len(prompt),
            )
            logger.info("[%s] prompt:\n%s", resp_id, _truncate_for_log(prompt))

        def _evt_log(evt: dict) -> None:
            if not settings.debug_log:
                return
            t = evt.get("type")
            if t == "thread.started":
                logger.info("[%s] codex %s thread_id=%s", resp_id, t, evt.get("thread_id"))
                return
            if t in {"turn.started", "turn.completed", "turn.failed"}:
                extra = ""
                if t == "turn.completed" and isinstance(evt.get("usage"), dict):
                    u = evt["usage"]
                    extra = f" usage={u}"
                logger.info("[%s] codex %s%s", resp_id, t, extra)
                return
            if t == "error":
                logger.error("[%s] codex error: %s", resp_id, _truncate_for_log(str(evt.get("message") or "")))
                return
            if t in {"item.started", "item.completed"}:
                item = evt.get("item") or {}
                itype = item.get("type")
                if itype == "command_execution":
                    cmd = item.get("command") or ""
                    status = item.get("status") or ""
                    exit_code = item.get("exit_code")
                    logger.info("[%s] codex %s command status=%s exit=%s cmd=%s", resp_id, t, status, exit_code, cmd)
                    out = item.get("aggregated_output")
                    if isinstance(out, str) and out.strip():
                        logger.info("[%s] codex command output:\n%s", resp_id, _truncate_for_log(out))
                    return
                if itype == "file_change":
                    changes = item.get("changes") or []
                    paths: list[str] = []
                    if isinstance(changes, list):
                        for ch in changes:
                            if isinstance(ch, dict) and isinstance(ch.get("path"), str):
                                kind = ch.get("kind")
                                if isinstance(kind, str) and kind:
                                    paths.append(f"{kind}:{ch['path']}")
                                else:
                                    paths.append(ch["path"])
                    logger.info("[%s] codex %s file_change %s", resp_id, t, ", ".join(paths)[: settings.log_max_chars])
                    return
                if itype == "agent_message":
                    text = _maybe_strip_answer_tags(str(item.get("text") or ""))
                    logger.info("[%s] codex %s agent_message_chars=%d", resp_id, t, len(text))
                    logger.info("[%s] codex agent_message:\n%s", resp_id, _truncate_for_log(text))
                    return
                if itype == "reasoning":
                    r = item.get("text") or ""
                    logger.info("[%s] codex %s reasoning_chars=%d", resp_id, t, len(str(r)))
                    return
                logger.info("[%s] codex %s item_type=%s", resp_id, t, itype)
                return

        def _stderr_log(line: str) -> None:
            if not settings.debug_log:
                return
            logger.warning("[%s] codex stderr: %s", resp_id, _truncate_for_log(line))

        if not req.stream:
            async with sem:
                events = iter_codex_events(
                    prompt=prompt,
                    model=model,
                    cd=settings.workspace,
                    sandbox=settings.sandbox,
                    skip_git_repo_check=settings.skip_git_repo_check,
                    model_reasoning_effort=reasoning_effort,
                    approval_policy=settings.approval_policy,
                    enable_search=settings.enable_search,
                    add_dirs=settings.add_dirs,
                    codex_cli_home=settings.codex_cli_home,
                    timeout_seconds=settings.timeout_seconds,
                    stream_limit=settings.subprocess_stream_limit,
                    event_callback=_evt_log,
                    stderr_callback=_stderr_log,
                )
                result = await collect_codex_text_and_usage_from_events(events)
            text = _maybe_strip_answer_tags(result.text)
            if settings.debug_log:
                logger.info("[%s] response_chars=%d", resp_id, len(text))
                logger.info("[%s] response:\n%s", resp_id, _truncate_for_log(text))
            return {
                "id": resp_id,
                "object": "chat.completion",
                "created": created,
                "model": requested_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": result.usage,
            }

        async def sse_gen():
            async with sem:
                # initial role chunk
                first = {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"

                events = iter_codex_events(
                    prompt=prompt,
                    model=model,
                    cd=settings.workspace,
                    sandbox=settings.sandbox,
                    skip_git_repo_check=settings.skip_git_repo_check,
                    model_reasoning_effort=reasoning_effort,
                    approval_policy=settings.approval_policy,
                    enable_search=settings.enable_search,
                    add_dirs=settings.add_dirs,
                    codex_cli_home=settings.codex_cli_home,
                    timeout_seconds=settings.timeout_seconds,
                    stream_limit=settings.subprocess_stream_limit,
                    event_callback=_evt_log,
                    stderr_callback=_stderr_log,
                )
                async with aclosing(events):
                    async for evt in events:
                        if await request.is_disconnected():
                            return
                        if evt.get("type") == "item.completed":
                            item = evt.get("item") or {}
                            if item.get("type") == "agent_message":
                                text = _maybe_strip_answer_tags(item.get("text") or "")
                                if text:
                                    chunk = {
                                        "id": resp_id,
                                        "object": "chat.completion.chunk",
                                        "created": created,
                                        "model": requested_model,
                                        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                                    }
                                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                end = {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": requested_model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(end, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(sse_gen(), media_type="text/event-stream")
    except TimeoutError:
        return _openai_error(f"codex exec timed out after {settings.timeout_seconds}s", status_code=504)
    except Exception as e:
        return _openai_error(str(e), status_code=500)
