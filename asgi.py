"""ASGI entrypoint for running JIO_tv with Uvicorn."""

from __future__ import annotations

import os

from . import server


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


server.initialize(
    os.environ.get("JIOTV_CONFIG", ""),
    log_stdout=_truthy_env("JIOTV_LOG_TO_STDOUT"),
    debug_log=_truthy_env("JIOTV_DEBUG"),
    proxy=os.environ.get("JIOTV_PROXY", ""),
)

app = server.asgi_app
