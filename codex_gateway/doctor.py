from __future__ import annotations

import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .claude_oauth import maybe_refresh_claude_oauth
from .codex_responses import load_codex_auth
from .gemini_cloudcode import load_gemini_creds


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_provider(raw: str | None) -> str:
    p = (raw or "").strip().lower()
    if not p:
        return "auto"
    if p in {"auto", "codex", "gemini", "claude", "cursor-agent"}:
        return p
    if p in {"cursor", "cursoragent", "cursor_agent"}:
        return "cursor-agent"
    return "auto"


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    required: bool
    details: str


def _fmt_status(ok: bool, *, required: bool) -> str:
    if ok:
        return "OK"
    return "FAIL" if required else "WARN"


def _which(name: str) -> str | None:
    return shutil.which(name)


def _check_binary(label: str, bin_name: str, *, required: bool) -> CheckResult:
    p = _which(bin_name)
    return CheckResult(f"{label} binary", bool(p), required, p or f"not found on PATH: {bin_name}")


def _check_codex_auth(*, required: bool) -> CheckResult:
    auth = load_codex_auth(codex_cli_home=os.environ.get("CODEX_CLI_HOME"))
    ok = bool(auth.access_token or auth.api_key)
    detail = "auth ok" if ok else "missing ~/.codex/auth.json tokens (run `codex login`)"
    return CheckResult("Codex auth", ok, required, detail)


def _check_gemini_creds(*, required: bool) -> CheckResult:
    path = Path(os.environ.get("GEMINI_OAUTH_CREDS_PATH", "~/.gemini/oauth_creds.json")).expanduser()
    if not path.exists():
        return CheckResult("Gemini OAuth cache", False, required, f"missing: {path} (run `gemini auth login`)")
    creds = load_gemini_creds(path)
    ok = bool(creds.access_token or creds.refresh_token)
    return CheckResult(
        "Gemini OAuth cache",
        ok,
        required,
        f"{path} (access={bool(creds.access_token)} refresh={bool(creds.refresh_token)})",
    )


async def _check_claude_oauth_refreshable(*, required: bool) -> CheckResult:
    path = Path(os.environ.get("CLAUDE_OAUTH_CREDS_PATH", "~/.claude/oauth_creds.json")).expanduser()
    if not path.exists():
        return CheckResult(
            "Claude OAuth cache",
            False,
            required,
            f"missing: {path} (run `uv run python -m codex_gateway.claude_oauth_login`)",
        )
    try:
        creds = await maybe_refresh_claude_oauth(str(path))
    except Exception as e:
        return CheckResult("Claude OAuth cache", False, required, f"{path} (refresh failed: {e})")
    ok = bool(creds.access_token or creds.refresh_token)
    return CheckResult(
        "Claude OAuth cache",
        ok,
        required,
        f"{path} (access={bool(creds.access_token)} refresh={bool(creds.refresh_token)})",
    )


def _check_workspace_file(*, required: bool) -> CheckResult:
    workspace = os.environ.get("CODEX_WORKSPACE")
    if not workspace:
        return CheckResult("CODEX_WORKSPACE", True, required, "not set")
    path = Path(workspace).expanduser()
    ok = path.exists() and path.is_dir()
    return CheckResult("CODEX_WORKSPACE", ok, required, str(path) if ok else f"missing or not a directory: {path}")


async def run_doctor() -> int:
    provider = _normalize_provider(os.environ.get("CODEX_PROVIDER"))
    claude_use_oauth = _env_bool("CLAUDE_USE_OAUTH_API", False)
    gemini_use_cloudcode = _env_bool("GEMINI_USE_CLOUDCODE_API", False)

    # Provider readiness heuristics:
    # - codex requires binary + auth
    # - gemini cloudcode requires binary + oauth creds; gemini-cli mode only requires binary
    # - claude oauth requires oauth creds; claude-cli mode only requires binary
    # - cursor-agent requires binary (login state isn't reliably detectable)

    codex_bin = _check_binary("codex", "codex", required=(provider == "codex"))
    codex_auth = _check_codex_auth(required=(provider == "codex"))
    codex_ready = codex_bin.ok and codex_auth.ok

    gemini_bin = _check_binary("gemini", "gemini", required=(provider == "gemini"))
    gemini_creds = _check_gemini_creds(required=(provider == "gemini" and gemini_use_cloudcode))
    gemini_ready = gemini_bin.ok and (gemini_creds.ok if gemini_use_cloudcode else True)

    claude_bin = _check_binary("claude", "claude", required=(provider == "claude" and not claude_use_oauth))
    claude_oauth = await _check_claude_oauth_refreshable(required=(provider == "claude" and claude_use_oauth))
    claude_ready = (claude_oauth.ok if claude_use_oauth else claude_bin.ok)

    cursor_bin = _check_binary("cursor-agent", "cursor-agent", required=(provider == "cursor-agent"))
    cursor_ready = cursor_bin.ok

    checks: list[CheckResult] = []

    if provider == "codex":
        checks.extend([codex_bin, codex_auth])
    elif provider == "gemini":
        checks.append(gemini_bin)
        checks.append(gemini_creds if gemini_use_cloudcode else _check_gemini_creds(required=False))
    elif provider == "claude":
        if claude_use_oauth:
            checks.append(claude_oauth)
            checks.append(_check_binary("claude", "claude", required=False))
        else:
            checks.append(claude_bin)
            checks.append(await _check_claude_oauth_refreshable(required=False))
    elif provider == "cursor-agent":
        checks.append(cursor_bin)
    else:
        checks.extend(
            [
                _check_binary("codex", "codex", required=False),
                _check_codex_auth(required=False),
                _check_binary("gemini", "gemini", required=False),
                _check_gemini_creds(required=False),
                _check_binary("claude", "claude", required=False),
                await _check_claude_oauth_refreshable(required=False),
                _check_binary("cursor-agent", "cursor-agent", required=False),
            ]
        )

    if os.environ.get("CODEX_WORKSPACE"):
        checks.append(_check_workspace_file(required=True))

    width = max(len(c.name) for c in checks) if checks else 10
    print("agent-cli-to-api doctor\n")
    for c in checks:
        print(f"- {c.name.ljust(width)} : {_fmt_status(c.ok, required=c.required)}  {c.details}")

    required_failed = any((not c.ok) and c.required for c in checks)
    warnings = any((not c.ok) and (not c.required) for c in checks)

    if provider == "auto":
        if not (codex_ready or gemini_ready or claude_ready or cursor_ready):
            required_failed = True

    if required_failed:
        result = "FAIL"
        code = 1
    elif warnings:
        result = "OK (with warnings)"
        code = 0
    else:
        result = "OK"
        code = 0

    print(f"\nResult: {result}")
    return code


def main(argv: list[str] | None = None) -> None:
    _ = argv
    raise SystemExit(asyncio.run(run_doctor()))


if __name__ == "__main__":
    main(sys.argv[1:])

