"""JioTV API client and stream URL rewrite helpers."""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

from . import constants
from .config import cfg, parse_simple_yaml
from .http_client import form_request, request
from .models import Bitrates, Channel, ChannelsResponse, JioTVCredentials, LiveURLOutput
from .secure_url import encrypt_url
from .utils import (
    body_preview,
    generate_current_time,
    generate_date,
    get_device_id,
    log,
    redact_url,
    read_json_response,
    token_preview,
)


_custom_channels_cache: dict[str, Channel] = {}
_custom_channels_lock = threading.RLock()
_channels_cache_lock = threading.RLock()
_channels_cache: ChannelsCacheEntry | None = None


@dataclass(slots=True)
class ChannelsCacheEntry:
    response: ChannelsResponse
    updated_at: float


@dataclass(slots=True)
class EncryptedURLConfig:
    base_url: str
    match: str
    params: str
    endpoint_url: str
    channel_id: str = ""
    quality: str = ""
    hdnea: str = ""


class Television:
    """API client that carries credentials and common JioTV headers."""

    def __init__(self, credentials: JioTVCredentials | None = None) -> None:
        credentials = credentials or JioTVCredentials()
        self.access_token = credentials.access_token
        self.sso_token = credentials.sso_token
        self.crm = credentials.crm
        self.unique_id = credentials.unique_id
        self.headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "appkey": "NzNiMDhlYzQyNjJm",
            "channel_id": "",
            "crmid": credentials.crm,
            "userId": credentials.crm,
            "deviceId": get_device_id(),
            "devicetype": "phone",
            "isott": "false",
            "languageId": "6",
            "lbcookie": "1",
            "os": "android",
            "osVersion": "13",
            "subscriberId": credentials.crm,
            "uniqueId": credentials.unique_id,
            constants.USER_AGENT: constants.USER_AGENT_OKHTTP,
            "usergroup": "tvYR7NSNn7rymo3F",
            "versionCode": constants.VERSION_CODE_406,
        }

    def live(self, channel_id: str) -> LiveURLOutput:
        if channel_id.startswith("sl"):
            return get_sl_channel(channel_id)

        log.info("live api request channel=%s", channel_id)
        payload = urlencode(
            {
                "channel_id": channel_id,
                "stream_type": "Seek",
                "begin": generate_current_time(),
                "srno": generate_date(),
            }
        )
        headers = dict(self.headers)
        headers[constants.ACCESS_TOKEN] = self.access_token
        headers["channel_id"] = channel_id
        url = f"https://{constants.JIOTV_API_DOMAIN}{constants.PLAYBACK_API_PATH}"
        resp = form_request(url, payload, headers=headers)
        if resp.status != 200:
            log.warning(
                "live api failed channel=%s status=%s body=%s",
                channel_id,
                resp.status,
                body_preview(resp.body),
            )
            raise RuntimeError(
                f"live request failed with status {resp.status}: {resp.body!r}"
            )
        result = LiveURLOutput.from_api(resp.json())
        hdnea = (
            _extract_hdnea_from_url(result.bitrates.auto)
            or _extract_hdnea_from_url(result.ssai.bitrates.auto)
            or _extract_hdnea_from_url(result.ssai.playback_url)
            or _extract_hdnea_from_url(result.mpd.result)
        )
        result.hdnea = hdnea
        if hdnea:
            _append_hdnea(result, hdnea)
        log.info(
            "live api ok channel=%s code=%s drm=%s bitrates=%s mpd=%s hdnea=%s",
            channel_id,
            result.code,
            result.is_drm,
            _bitrate_summary(result),
            bool(result.mpd.result or result.mpd.bitrates.auto),
            token_preview(hdnea),
        )
        return result

    def render(
        self,
        stream_url: str,
        hdnea_token: str = "",
        *,
        ssai_session: bool = False,
    ) -> tuple[bytes, int, str]:
        headers = dict(self.headers)
        headers[constants.USER_AGENT] = constants.USER_AGENT_PLAY_TV
        token = hdnea_token or _extract_hdnea_from_url(stream_url)
        if token:
            headers["Cookie"] = f"__hdnea__={token}"
        request_url = (
            _with_ssai_session_params(stream_url) if ssai_session else stream_url
        )
        resp = request(request_url, headers=headers)
        render_log_url = request_url
        new_hdnea = _extract_cookie(resp.headers.get_all("Set-Cookie", []))
        ssai_media_url = _extract_ssai_media_url(resp.body)
        if resp.status < 400 and ssai_media_url:
            log.info(
                "render ssai session resolved url=%s media=%s",
                redact_url(request_url),
                redact_url(ssai_media_url),
            )
            media_token = hdnea_token or _extract_hdnea_from_url(ssai_media_url)
            if media_token:
                headers["Cookie"] = f"__hdnea__={media_token}"
            resp = request(ssai_media_url, headers=headers)
            render_log_url = ssai_media_url
            media_hdnea = _extract_cookie(resp.headers.get_all("Set-Cookie", []))
            new_hdnea = media_hdnea or new_hdnea
        log.info(
            "render upstream status=%s bytes=%s url=%s cookie_in=%s cookie_out=%s",
            resp.status,
            len(resp.body),
            redact_url(render_log_url),
            token_preview(token),
            token_preview(new_hdnea),
        )
        if resp.status >= 400:
            log.warning("render upstream error body=%s", body_preview(resp.body))
        return resp.body, resp.status, new_hdnea

    def get_catchup_url(
        self,
        channel_id: str,
        srno: str,
        start: str,
        end: str,
    ) -> LiveURLOutput:
        payload = urlencode(
            {
                "stream_type": "Catchup",
                "channel_id": channel_id,
                "programId": srno,
                "showtime": "000000",
                "srno": srno,
                "begin": start,
                "end": end,
            }
        )
        headers = dict(self.headers)
        headers[constants.ACCESS_TOKEN] = self.access_token
        headers["channel_id"] = channel_id
        headers["srno"] = srno
        url = f"https://{constants.JIOTV_API_DOMAIN}{constants.PLAYBACK_API_PATH}"
        log.info("catchup api request channel=%s srno=%s", channel_id, srno)
        resp = form_request(url, payload, headers=headers)
        if resp.status != 200:
            log.warning(
                "catchup api failed channel=%s status=%s body=%s",
                channel_id,
                resp.status,
                body_preview(resp.body),
            )
            raise RuntimeError(f"catchup request failed with status {resp.status}")
        result = LiveURLOutput.from_api(resp.json())
        result.hdnea = (
            _extract_hdnea_from_url(result.result)
            or _extract_hdnea_from_url(result.bitrates.auto)
            or _extract_hdnea_from_url(result.ssai.bitrates.auto)
            or _extract_hdnea_from_url(result.ssai.playback_url)
        )
        log.info(
            "catchup api ok channel=%s drm=%s target=%s hdnea=%s",
            channel_id,
            result.is_drm,
            bool(result.bitrates.auto or result.result),
            token_preview(result.hdnea),
        )
        return result


def init_custom_channels() -> None:
    if cfg.custom_channels_file:
        load_and_cache_custom_channels()


def reload_custom_channels() -> None:
    init_custom_channels()


def get_custom_channel_by_id(channel_id: str) -> tuple[Channel | None, bool]:
    with _custom_channels_lock:
        channel = _custom_channels_cache.get(channel_id)
        return channel, channel is not None


def load_and_cache_custom_channels() -> None:
    channels = load_custom_channels(cfg.custom_channels_file)
    next_cache = {channel.id: channel for channel in channels}
    with _custom_channels_lock:
        _custom_channels_cache.clear()
        _custom_channels_cache.update(next_cache)
    clear_channels_cache()
    if len(channels) > constants.MAX_RECOMMENDED_CHANNELS:
        log.warning("Loaded %s custom channels; channel lists may be slow", len(channels))


def load_custom_channels(file_path: str) -> list[Channel]:
    if not file_path:
        return []
    path = Path(file_path)
    if not path.exists():
        if is_default_custom_channels_path(path):
            return convert_custom_config_to_channels(json.loads(BUILT_IN_CUSTOM_CHANNELS_JSON))
        log.info("Custom channels file not found: %s", file_path)
        return []
    try:
        data = path.read_text(encoding="utf-8")
        parsed = detect_and_parse_format(data, path)
    except Exception as exc:
        raise ValueError(f"failed to parse custom channels file: {exc}") from exc
    channels = convert_custom_config_to_channels(parsed)
    log.info("Loaded %s custom channels from %s", len(channels), file_path)
    return channels


def detect_and_parse_format(data: str, path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(data)
    if suffix in {".yml", ".yaml"}:
        return parse_simple_yaml(data)
    trimmed = data.strip()
    if not trimmed:
        raise ValueError(constants.UNSUPPORTED_CHANNELS_FORMAT)
    if trimmed.startswith(("{", "[")):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return parse_simple_yaml(data)
    return parse_simple_yaml(data)


def convert_custom_config_to_channels(custom_config: dict[str, Any]) -> list[Channel]:
    channels = []
    for item in custom_config.get("channels", []):
        channel_id = str(item.get("id", ""))
        if not channel_id.startswith("cc_"):
            channel_id = f"cc_{channel_id}"
        channels.append(
            Channel(
                id=channel_id,
                name=str(item.get("name", "")),
                url=str(item.get("url", "")),
                logo_url=str(item.get("logo_url", item.get("logoUrl", ""))),
                category=int(item.get("category", 0) or 0),
                language=int(item.get("language", 0) or 0),
                is_hd=bool(item.get("is_hd", item.get("isHD", False))),
            )
        )
    return channels


def get_custom_channels() -> list[Channel]:
    with _custom_channels_lock:
        return list(_custom_channels_cache.values())


def is_default_custom_channels_path(path: Path) -> bool:
    return path.name.lower() in {
        "custom-channels.json",
        "custom_channels.json",
        "custom-channels.yml",
        "custom_channels.yml",
        "custom-channels.yaml",
        "custom_channels.yaml",
    }


def channels() -> ChannelsResponse:
    cached = _cached_channels_response()
    if cached is not None:
        return cached

    global _channels_cache
    with _channels_cache_lock:
        cached = _cached_channels_response_locked()
        if cached is not None:
            return cached

        log.info("channels cache miss; fetching channel list")
        response = _fetch_channels()
        if cfg.channels_cache_ttl > 0:
            _channels_cache = ChannelsCacheEntry(response=response, updated_at=time.time())
        return clone_channels_response(response)


def clear_channels_cache() -> None:
    global _channels_cache
    with _channels_cache_lock:
        _channels_cache = None


def _cached_channels_response() -> ChannelsResponse | None:
    with _channels_cache_lock:
        return _cached_channels_response_locked()


def _cached_channels_response_locked() -> ChannelsResponse | None:
    if cfg.channels_cache_ttl <= 0:
        return None
    global _channels_cache
    entry = _channels_cache
    if entry is None:
        return None
    if time.time() - entry.updated_at > cfg.channels_cache_ttl:
        _channels_cache = None
        return None
    log.debug("channels cache hit ttl=%ss", cfg.channels_cache_ttl)
    return clone_channels_response(entry.response)


def _fetch_channels() -> ChannelsResponse:
    headers = {
        constants.USER_AGENT: constants.USER_AGENT_OKHTTP,
        constants.ACCEPT: constants.ACCEPT_JSON,
        constants.DEVICE_TYPE: constants.DEVICE_TYPE_PHONE,
        constants.OS: constants.OS_ANDROID,
        "appkey": "NzNiMDhlYzQyNjJm",
        "lbcookie": "1",
        "usertype": "JIO",
    }
    data = read_json_response(constants.CHANNELS_API_URL, headers)
    response = ChannelsResponse.from_api(data)
    if cfg.custom_channels_file:
        response.result.extend(get_custom_channels())
    return response


def clone_channels_response(response: ChannelsResponse) -> ChannelsResponse:
    return ChannelsResponse(
        code=response.code,
        message=response.message,
        result=[clone_channel(channel) for channel in response.result],
    )


def clone_channel(channel: Channel) -> Channel:
    return Channel(
        id=channel.id,
        name=channel.name,
        url=channel.url,
        logo_url=channel.logo_url,
        category=channel.category,
        language=channel.language,
        is_hd=channel.is_hd,
        is_catchup_available=channel.is_catchup_available,
    )


def filter_channels(
    channel_list: list[Channel],
    language: int,
    category: int,
) -> list[Channel]:
    filtered = []
    for channel in channel_list:
        if language and category:
            include = channel.language == language and channel.category == category
        elif language:
            include = channel.language == language
        elif category:
            include = channel.category == category
        else:
            include = True
        if include:
            filtered.append(channel)
    return filtered


def filter_channels_by_defaults(
    channel_list: list[Channel],
    categories: list[int],
    languages: list[int],
) -> list[Channel]:
    if not categories and not languages:
        return channel_list
    category_set = set(categories)
    language_set = set(languages)
    return [
        channel
        for channel in channel_list
        if (not category_set or channel.category in category_set)
        and (not language_set or channel.language in language_set)
    ]


def create_encrypted_url(config: EncryptedURLConfig) -> str:
    full_url = config.base_url + config.match
    if config.params:
        sep = "&" if "?" in full_url else "?"
        full_url += sep + config.params
    encrypted = encrypt_url(full_url)
    result = f"{config.endpoint_url}?auth={encrypted}"
    if config.channel_id:
        result += f"&channel_key_id={quote_plus(config.channel_id)}"
    if config.quality:
        result += f"&q={quote_plus(config.quality)}"
    if config.hdnea:
        result += f"&hdnea={quote_plus(config.hdnea)}"
    return result


def replace_m3u8(
    base_url: str,
    match: str,
    params: str,
    channel_id: str,
    quality: str,
) -> str:
    return create_encrypted_url(
        EncryptedURLConfig(
            base_url=base_url,
            match=match,
            params=params,
            channel_id=channel_id,
            endpoint_url="/render.m3u8",
            quality=quality,
        )
    )


def replace_ts(base_url: str, match: str, params: str, channel_id: str) -> str:
    if cfg.disable_ts_handler:
        return _join_url(base_url, match, params)
    return create_encrypted_url(
        EncryptedURLConfig(
            base_url=base_url,
            match=match,
            params=params,
            channel_id=channel_id,
            endpoint_url="/render.ts",
        )
    )


def replace_aac(base_url: str, match: str, params: str, channel_id: str) -> str:
    return replace_ts(base_url, match, params, channel_id)


def replace_key(match: str, params: str, channel_id: str) -> str:
    return create_encrypted_url(
        EncryptedURLConfig(
            base_url="",
            match=match,
            params=params,
            channel_id=channel_id,
            endpoint_url="/render.key",
        )
    )


def get_sl_channel(channel_id: str) -> LiveURLOutput:
    key = constants.SONY_JIO_MAP.get(channel_id)
    if key is None:
        raise ValueError("channel not found")
    channel_url = base64.b64decode(constants.SONY_CHANNELS[key]).decode()
    resp = request(channel_url, follow_redirects=False)
    if resp.status not in {301, 302, 303, 307, 308}:
        raise RuntimeError(f"SonyLiv request failed with status {resp.status}")
    actual_url = resp.headers.get("Location", "")
    return LiveURLOutput(result=actual_url, bitrates=Bitrates(auto=actual_url))


def _join_url(base_url: str, match: str, params: str) -> str:
    if not params:
        return base_url + match
    sep = "&" if "?" in match else "?"
    return base_url + match + sep + params


def _extract_hdnea_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    for part in parsed.query.split("&"):
        if part.startswith("__hdnea__="):
            return part.removeprefix("__hdnea__=")
        if part.startswith("hdnea="):
            return part.removeprefix("hdnea=")
    return ""


def _append_hdnea(result: LiveURLOutput, hdnea: str) -> None:
    def append(url: str) -> str:
        if not url or "hdnea=" in url or "__hdnea__=" in url:
            return url
        return f"{url}{'&' if '?' in url else '?'}hdnea={hdnea}"

    def append_media_url(url: str) -> str:
        lower_url = url.lower()
        if ".m3u8" not in lower_url and ".mpd" not in lower_url:
            return url
        return append(url)

    result.bitrates.auto = append(result.bitrates.auto)
    result.bitrates.high = append(result.bitrates.high)
    result.bitrates.medium = append(result.bitrates.medium)
    result.bitrates.low = append(result.bitrates.low)
    result.result = append(result.result)
    result.ssai.playback_url = append_media_url(result.ssai.playback_url)
    result.ssai.bitrates.auto = append_media_url(result.ssai.bitrates.auto)
    result.ssai.bitrates.high = append_media_url(result.ssai.bitrates.high)
    result.ssai.bitrates.medium = append_media_url(result.ssai.bitrates.medium)
    result.ssai.bitrates.low = append_media_url(result.ssai.bitrates.low)
    result.mpd.result = append(result.mpd.result)
    result.mpd.key = append(result.mpd.key)


def _with_ssai_session_params(stream_url: str) -> str:
    if not stream_url:
        return stream_url
    parsed = urlparse(stream_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return stream_url
    lower_path = parsed.path.lower()
    if ".m3u8" in lower_path or ".mpd" in lower_path:
        return stream_url

    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    existing = {key for key, _value in pairs}
    hour = datetime.now().hour
    minute = datetime.now().minute
    defaults = {
        "cgi": "defaultCGI",
        "dt": "1",
        "md_dvb": "Jio",
        "md_dvm": "Android",
        "md_hr": str(hour),
        "md_min": str(minute),
        "md_osv": "13",
        "os": "1",
        "sh": "1920",
        "sw": "1080",
        "trq": str(int(time.time() * 1000)),
        "vr": "AN-2.4.15",
    }
    changed = False
    for key, value in defaults.items():
        if key in existing:
            continue
        pairs.append((key, value))
        changed = True
    if not changed:
        return stream_url
    return urlunparse(parsed._replace(query=urlencode(pairs)))


def _bitrate_summary(result: LiveURLOutput) -> str:
    def available(label: str, bitrates: Bitrates) -> str:
        names = [
            name
            for name, value in (
                ("auto", bitrates.auto),
                ("high", bitrates.high),
                ("medium", bitrates.medium),
                ("low", bitrates.low),
            )
            if value
        ]
        return f"{label}=[{','.join(names) or 'none'}]"

    parts = [available("hls", result.bitrates)]
    if result.ssai.playback_url or any(
        (
            result.ssai.bitrates.auto,
            result.ssai.bitrates.high,
            result.ssai.bitrates.medium,
            result.ssai.bitrates.low,
        )
    ):
        parts.append(available("ssai", result.ssai.bitrates))
        if result.ssai.playback_url:
            parts.append("ssai_playback=1")
    parts.append(available("mpd", result.mpd.bitrates))
    return " ".join(parts)


def _extract_cookie(set_cookie_headers: list[str]) -> str:
    for header in set_cookie_headers:
        for part in header.split(";"):
            trimmed = part.strip()
            if trimmed.startswith("__hdnea__="):
                return trimmed.removeprefix("__hdnea__=")
    return ""


def _extract_ssai_media_url(body: bytes) -> str:
    preview = body.lstrip()[:1]
    if preview != b"{":
        return ""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    media_url = data.get("mediaURL", data.get("mediaUrl", data.get("media_url", "")))
    return str(media_url or "")


BUILT_IN_CUSTOM_CHANNELS_JSON = """{
  "channels": [
    {
      "id": "custom_news_1",
      "name": "Sample News Channel",
      "url": "https://example.com/news/playlist.m3u8",
      "logo_url": "https://example.com/logos/news.png",
      "category": 12,
      "language": 6,
      "is_hd": true
    },
    {
      "id": "custom_entertainment_1",
      "name": "Sample Entertainment Channel",
      "url": "https://example.com/entertainment/playlist.m3u8",
      "logo_url": "https://example.com/logos/entertainment.png",
      "category": 5,
      "language": 1,
      "is_hd": false
    }
  ]
}"""
