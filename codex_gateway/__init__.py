from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI

    app: FastAPI

__all__ = ["app"]


def __getattr__(name: str):
    # Lazily import the FastAPI app so environment variables can be loaded
    # before `codex_gateway.server` (and thus `codex_gateway.config`) is imported.
    if name == "app":
        return import_module(".server", __name__).app
    raise AttributeError(name)
