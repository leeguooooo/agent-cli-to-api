import argparse
import os
from pathlib import Path

import uvicorn


def _maybe_load_dotenv(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-cli-to-api",
        description="Expose agent CLIs as an OpenAI-compatible /v1 API gateway.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("CODEX_HOST", "127.0.0.1"),
        help="Bind host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        default=int(os.environ.get("CODEX_PORT", "8000")),
        type=int,
        help="Bind port (default: 8000).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn reload (dev only).",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("CODEX_LOG_LEVEL", "info"),
        help="Uvicorn log level (default: info).",
    )
    parser.add_argument(
        "--env-file",
        default=os.environ.get("CODEX_ENV_FILE"),
        help="Optionally load environment variables from this .env file.",
    )
    parser.add_argument(
        "--no-env",
        action="store_true",
        help="Disable auto-loading .env from the current directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.env_file:
        _maybe_load_dotenv(Path(args.env_file))
    elif not args.no_env:
        _maybe_load_dotenv(Path.cwd() / ".env")

    uvicorn.run(
        "codex_gateway.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


__all__ = ["main"]

