"""Stream URL selection, HDNEA cache, and manifest rewriting helpers."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import constants
from .models import LiveURLOutput
from .utils import select_quality


HDNEA_CACHE_TTL = 60
HDNEA_REFRESH_LEAD_TIME = 20


@dataclass(slots=True)
class HDNEACacheEntry:
    token: str
    updated_at: float


_hdnea_cache: dict[str, HDNEACacheEntry] = {}
_hdnea_lock = threading.RLock()


def truncate_token(token: str) -> str:
    if not token:
        return "(empty)"
    if len(token) <= 20:
        return token
    return token[:10] + "..." + token[-10:]


def is_likely_hls_url(stream_url: str) -> bool:
    return ".m3u8" in stream_url.lower()


def is_absolute_http_url(stream_url: str) -> bool:
    parsed = urlparse(stream_url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def absolute_base_from_live_result(live_result: LiveURLOutput | None) -> str:
    if live_result is None:
        return ""
    candidates = [
        live_result.bitrates.auto,
        live_result.bitrates.high,
        live_result.bitrates.medium,
        live_result.bitrates.low,
        live_result.result,
        live_result.mpd.result,
        live_result.mpd.bitrates.auto,
        live_result.mpd.bitrates.high,
        live_result.mpd.bitrates.medium,
        live_result.mpd.bitrates.low,
    ]
    for candidate in candidates:
        if is_absolute_http_url(candidate):
            parsed = urlparse(candidate)
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def to_absolute_stream_url(
    stream_url: str,
    live_result: LiveURLOutput | None = None,
) -> str:
    if not stream_url:
        return ""
    if is_absolute_http_url(stream_url):
        return stream_url
    if stream_url.startswith("//"):
        return "https:" + stream_url
    first_part = stream_url.split("/", 1)[0]
    if "." in first_part and not stream_url.startswith("/"):
        return "https://" + stream_url
    if not stream_url.startswith("/"):
        stream_url = "/" + stream_url
    base = absolute_base_from_live_result(live_result)
    if not base:
        base = f"https://{constants.JIOTV_CDN_DOMAIN}"
    return base + stream_url


def strip_hdnea_from_url(stream_url: str) -> str:
    if not stream_url:
        return stream_url
    parsed = urlparse(stream_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"hdnea", "__hdnea__"}
    ]
    return urlunparse(parsed._replace(query=urlencode(query)))


def extract_hdnea_from_url(stream_url: str) -> str:
    if not stream_url:
        return ""
    parsed = urlparse(stream_url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in {"__hdnea__", "hdnea"} and value:
            return value
    return ""


def hdnea_remaining_lifetime(token: str) -> tuple[float, bool]:
    if not token:
        return 0, False
    for part in token.split("~"):
        if not part.startswith("exp="):
            continue
        try:
            expiration = int(part.removeprefix("exp="))
        except ValueError:
            return 0, False
        remaining = datetime.fromtimestamp(expiration, timezone.utc).timestamp()
        return remaining - time.time(), True
    return 0, False


def extract_live_result_hdnea(live_result: LiveURLOutput | None) -> str:
    if live_result is None:
        return ""
    candidates = [
        live_result.bitrates.auto,
        live_result.bitrates.high,
        live_result.bitrates.medium,
        live_result.bitrates.low,
        live_result.result,
        live_result.mpd.bitrates.auto,
        live_result.mpd.bitrates.high,
        live_result.mpd.bitrates.medium,
        live_result.mpd.bitrates.low,
        live_result.mpd.result,
    ]
    for candidate in candidates:
        token = extract_hdnea_from_url(candidate)
        if token:
            return token
    return live_result.hdnea


def live_result_needs_refresh(live_result: LiveURLOutput | None) -> bool:
    token = extract_live_result_hdnea(live_result)
    remaining, ok = hdnea_remaining_lifetime(token)
    return ok and remaining <= HDNEA_REFRESH_LEAD_TIME


def get_cached_hdnea(channel_id: str) -> str:
    if not channel_id:
        return ""
    with _hdnea_lock:
        entry = _hdnea_cache.get(channel_id)
        if entry is None:
            return ""
        if not entry.token or time.time() - entry.updated_at > HDNEA_CACHE_TTL:
            _hdnea_cache.pop(channel_id, None)
            return ""
        return entry.token


def set_cached_hdnea(channel_id: str, token: str) -> None:
    if not channel_id or not token:
        return
    with _hdnea_lock:
        _hdnea_cache[channel_id] = HDNEACacheEntry(token=token, updated_at=time.time())


def clear_cached_hdnea(channel_id: str) -> None:
    with _hdnea_lock:
        _hdnea_cache.pop(channel_id, None)


def select_best_live_hls_url(live_result: LiveURLOutput | None, quality: str) -> str:
    if live_result is None:
        return ""
    selected = select_quality(
        quality,
        live_result.bitrates.auto,
        live_result.bitrates.high,
        live_result.bitrates.medium,
        live_result.bitrates.low,
    )
    if selected:
        return selected
    for candidate in (
        live_result.bitrates.high,
        live_result.bitrates.auto,
        live_result.bitrates.medium,
        live_result.bitrates.low,
    ):
        if candidate:
            return candidate
    if is_likely_hls_url(live_result.result):
        return live_result.result
    if is_likely_hls_url(live_result.mpd.result):
        return live_result.mpd.result
    return ""


def live_hls_url_candidates(live_result: LiveURLOutput | None, quality: str) -> list[str]:
    if live_result is None:
        return []
    preferred = select_best_live_hls_url(live_result, quality)
    candidates = [
        preferred,
        live_result.bitrates.auto,
        live_result.bitrates.high,
        live_result.bitrates.medium,
        live_result.bitrates.low,
        live_result.result if is_likely_hls_url(live_result.result) else "",
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def select_best_live_mpd_url(live_result: LiveURLOutput | None, quality: str) -> str:
    if live_result is None:
        return ""
    selected = select_quality(
        quality,
        live_result.mpd.bitrates.auto,
        live_result.mpd.bitrates.high,
        live_result.mpd.bitrates.medium,
        live_result.mpd.bitrates.low,
    )
    if selected:
        return selected
    for candidate in (
        live_result.mpd.bitrates.high,
        live_result.mpd.bitrates.auto,
        live_result.mpd.bitrates.medium,
        live_result.mpd.bitrates.low,
    ):
        if candidate:
            return candidate
    return live_result.mpd.result


MEDIA_PATTERN = re.compile(r"[a-z0-9=_\-A-Z/.\:]*\.(m3u8|ts|aac)(\?[^\s\"']*)?")
KEY_PATTERN = re.compile(r"http[\S]+\.(pkey|key)")


def base_and_params(render_url: str, hdnea_token: str) -> tuple[str, str]:
    base_string_url, _, params = render_url.partition("?")
    base_url = re.sub(r"[a-z0-9=_\-A-Z.]*\.m3u8", "", base_string_url)
    pairs = [
        (key, value)
        for key, value in parse_qsl(params, keep_blank_values=True)
        if key not in {"hdnea", "__hdnea__"}
    ]
    if hdnea_token:
        pairs.append(("__hdnea__", hdnea_token))
    return base_url, urlencode(pairs)
