"""
Discover the current Codex model list so the gateway never needs a release when
OpenAI ships a new model.

Sources, freshest first:
1. An in-memory list fetched from the Codex backend `/models` endpoint (refreshed
   periodically by the server).
2. The Codex CLI's own `models_cache.json` (system `~/.codex` and the gateway's
   managed CLI home), whichever was fetched most recently.
3. A hardcoded fallback list.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from .codex_responses import build_codex_headers, load_codex_auth

logger = logging.getLogger("uvicorn.error")

FALLBACK_CODEX_MODELS = [
    "gpt-6.1-sol",
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-5.6-sol",
]

_FILE_CACHE_TTL_SECONDS = 60.0

_live_models: list[str] = []
_file_models: list[str] = []
_file_checked_at = 0.0


def parse_models_payload(payload: Any) -> list[str]:
    """Return visible model slugs ordered by priority (lowest first)."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("models")
    if not isinstance(raw, list):
        return []
    entries: list[tuple[float, int, str]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        if item.get("visibility", "list") != "list":
            continue
        priority = item.get("priority")
        prio = float(priority) if isinstance(priority, (int, float)) else float("inf")
        entries.append((prio, idx, slug.strip()))
    entries.sort()
    return [slug for _, _, slug in entries]


def _cache_files(codex_cli_home: str | None) -> list[Path]:
    paths = [Path.home() / ".codex" / "models_cache.json"]
    if codex_cli_home:
        paths.append(Path(codex_cli_home) / ".codex" / "models_cache.json")
    return paths


def _read_cache_files(codex_cli_home: str | None) -> list[str]:
    best: tuple[str, list[str]] | None = None
    for path in _cache_files(codex_cli_home):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        models = parse_models_payload(data)
        if not models:
            continue
        fetched_at = data.get("fetched_at") if isinstance(data.get("fetched_at"), str) else ""
        if best is None or fetched_at > best[0]:
            best = (fetched_at, models)
    return best[1] if best else []


def available_codex_models(codex_cli_home: str | None) -> list[str]:
    """Current visible Codex models, newest/highest-priority first."""
    global _file_models, _file_checked_at
    if _live_models:
        return _live_models[:]
    now = time.monotonic()
    if now - _file_checked_at > _FILE_CACHE_TTL_SECONDS:
        _file_models = _read_cache_files(codex_cli_home)
        _file_checked_at = now
    return (_file_models or FALLBACK_CODEX_MODELS)[:]


def latest_codex_model(codex_cli_home: str | None) -> str:
    return available_codex_models(codex_cli_home)[0]


async def refresh_codex_models(
    *,
    codex_cli_home: str | None,
    base_url: str,
    version: str,
    user_agent: str,
    timeout_seconds: float = 20.0,
) -> list[str]:
    """Fetch the model list from the Codex backend. Best effort; returns [] on failure."""
    global _live_models
    auth = load_codex_auth(codex_cli_home=codex_cli_home)
    token = auth.api_key or auth.access_token
    if not token:
        return []
    headers = build_codex_headers(
        token=token,
        account_id=auth.account_id,
        version=version,
        user_agent=user_agent,
    )
    headers["Accept"] = "application/json"
    url = base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.get(url, params={"client_version": version}, headers=headers)
        resp.raise_for_status()
        models = parse_models_payload(resp.json())
    except Exception as e:
        logger.warning("[codex-models] refresh failed: %s", e)
        return []
    if models:
        if models != _live_models:
            logger.info("[codex-models] latest=%s available=%s", models[0], ",".join(models))
        _live_models = models
    return models
