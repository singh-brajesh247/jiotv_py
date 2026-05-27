"""Logging and redaction helpers for stream diagnostics."""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


log = logging.getLogger("JIO_tv")

SENSITIVE_QUERY_KEYS = {
    "__hdnea__",
    "access_token",
    "accessToken",
    "auth",
    "authToken",
    "channel",
    "hdnea",
    "refreshToken",
    "ssoToken",
    "token",
}

SENSITIVE_JSON_RE = re.compile(
    r'("(?:accessToken|authToken|refreshToken|ssoToken|ssotoken)"\s*:\s*")([^"]+)(")',
    re.IGNORECASE,
)


def token_preview(value: str, visible: int = 6) -> str:
    if not value:
        return "(empty)"
    if len(value) <= visible * 2:
        return value[0:visible] + "..."
    return f"{value[:visible]}...{value[-visible:]}"


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    redacted_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in SENSITIVE_QUERY_KEYS or "token" in key.lower():
            redacted_pairs.append((key, token_preview(value)))
        else:
            redacted_pairs.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(redacted_pairs)))


def body_preview(body: bytes, limit: int = 300) -> str:
    text = body.decode("utf-8", errors="replace").replace("\n", "\\n")
    text = SENSITIVE_JSON_RE.sub(r"\1<redacted>\3", text)
    if len(text) > limit:
        return text[:limit] + "..."
    return text
