"""Runnable Uvicorn/ASGI server for the Python conversion."""

from __future__ import annotations

import asyncio
import io
import json
import mimetypes
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse, urlunparse

from . import constants, epg, secure_url, store, television, templates, tokens
from .config import cfg
from .http_client import StreamingHTTPResponse, request, stream_request
from .models import LiveURLOutput
from .scheduler import scheduler
from .streaming import (
    KEY_PATTERN,
    MEDIA_PATTERN,
    base_and_params,
    clear_cached_hdnea,
    extract_hdnea_from_url,
    extract_live_result_hdnea,
    get_cached_hdnea,
    live_hls_url_candidates,
    live_result_needs_refresh,
    select_best_live_hls_url,
    select_best_live_mpd_url,
    set_cached_hdnea,
    strip_hdnea_from_url,
    to_absolute_stream_url,
)
from .television import Television
from .utils import (
    body_preview,
    build_hls_play_url,
    check_logged_in,
    get_credentials,
    get_device_id,
    get_path_prefix,
    init_logger,
    log,
    login_send_otp,
    login_verify_otp,
    logout,
    redact_url,
    select_quality,
    token_preview,
)


LIVE_RESULT_CACHE_TTL = 30


@dataclass(slots=True)
class LiveResultCacheEntry:
    result: LiveURLOutput
    updated_at: float


@dataclass(slots=True)
class AppState:
    tv: Television | None = None
    title: str = "JIO_tv"
    disable_ts_handler: bool = False
    logout_disabled: bool = False
    enable_drm: bool = True
    token_refresh_locks: dict[str, threading.Lock] = field(default_factory=dict)
    token_refresh_lock: threading.RLock = field(default_factory=threading.RLock)
    live_result_cache: dict[str, LiveResultCacheEntry] = field(default_factory=dict)
    live_result_cache_lock: threading.RLock = field(default_factory=threading.RLock)
    live_warmup_inflight: set[str] = field(default_factory=set)


state = AppState()


def initialize(
    config_path: str = "",
    *,
    log_stdout: bool = False,
    debug_log: bool = False,
) -> None:
    cfg.load(config_path)
    if log_stdout:
        cfg.log_to_stdout = True
    if debug_log:
        cfg.debug = True
    init_logger()
    store.init()
    secure_url.init()
    get_device_id()
    init_handlers()
    log.info("log file: %s", get_path_prefix() / "JIO_tv.log")
    log.info(
        "config loaded debug=%s epg=%s drm=%s ts_handler=%s url_encryption=%s",
        cfg.debug,
        cfg.epg,
        state.enable_drm,
        not cfg.disable_ts_handler,
        not cfg.disable_url_encryption,
    )
    if cfg.epg or (get_path_prefix() / "epg.xml.gz").exists():
        threading.Thread(target=epg.init, daemon=True).start()


def init_handlers() -> None:
    state.title = cfg.title or "JIO_tv"
    state.disable_ts_handler = cfg.disable_ts_handler
    state.logout_disabled = cfg.disable_logout
    state.enable_drm = cfg.drm
    state.tv = Television(get_credentials())
    television.init_custom_channels()
    log.info(
        "handler state title=%r logged_in=%s custom_channels=%s",
        state.title,
        check_logged_in(),
        bool(cfg.custom_channels_file),
    )


def serve(host: str = "localhost", port: int = 5001) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Uvicorn is not installed. Install dependencies with "
            "`python3 -m pip install -r jio_py/requirements-python.txt`."
        ) from exc
    log.info("JIO_tv uvicorn server listening on http://%s:%s", host, port)
    try:
        uvicorn.run(
            asgi_app,
            host=host,
            port=port,
            log_level="debug" if cfg.debug else "info",
            access_log=False,
        )
    finally:
        scheduler.stop()


ASGIScope = dict[str, Any]
ASGIReceive = Callable[[], Any]
ASGISend = Callable[[dict[str, Any]], Any]
ResponseQueueItem = bytes | BaseException | None
RetryHeaderUpdates = dict[str, str | None] | None


class ASGIResponseWriter:
    def __init__(
        self,
        output_queue: queue.Queue[ResponseQueueItem],
    ) -> None:
        self._output_queue = output_queue

    def write(self, data: bytes) -> int:
        if data:
            self._output_queue.put(bytes(data))
        return len(data)

    def flush(self) -> None:
        return


class JioTVASGIApp:
    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        if scope["type"] != "http":
            return
        body = await _read_asgi_body(receive)
        output_queue: queue.Queue[ResponseQueueItem] = queue.Queue()
        thread = threading.Thread(
            target=_run_asgi_handler,
            args=(scope, body, output_queue),
            daemon=True,
        )
        thread.start()
        await _send_asgi_response(output_queue, send)
        thread.join(timeout=1)


asgi_app = JioTVASGIApp()


class JioTVRequestHandler:
    server_version = "JIO_tv"

    def __init__(
        self,
        method: str = "",
        path: str = "",
        headers: Message | None = None,
        body: bytes = b"",
        client_address: tuple[str, int] = ("0.0.0.0", 0),
        output_queue: queue.Queue[ResponseQueueItem] | None = None,
    ) -> None:
        self.command = method.upper()
        self.path = path
        self.headers = headers or Message()
        self.client_address = client_address
        self.rfile = io.BytesIO(body)
        self.wfile = ASGIResponseWriter(output_queue or queue.Queue())
        self._response_status = HTTPStatus.OK
        self._response_headers: list[tuple[str, str]] = []
        if self.command and self.path:
            self._dispatch(self.command)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def address_string(self) -> str:
        return self.client_address[0]

    def send_response(self, status: int | HTTPStatus) -> None:
        self._response_status = _http_status(int(status))
        self._response_headers = []

    def send_header(self, key: str, value: str) -> None:
        self._response_headers.append((key, value))

    def end_headers(self) -> None:
        reason = self._response_status.phrase.encode("latin-1", errors="replace")
        response = bytearray(
            b"HTTP/1.1 "
            + str(int(self._response_status)).encode("ascii")
            + b" "
            + reason
            + b"\r\n"
        )
        for key, value in self._response_headers:
            response.extend(key.encode("latin-1", errors="replace"))
            response.extend(b": ")
            response.extend(str(value).encode("latin-1", errors="replace"))
            response.extend(b"\r\n")
        response.extend(b"\r\n")
        self.wfile.write(bytes(response))

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)
        log.debug("request start method=%s path=%s query_keys=%s", method, path, sorted(query))
        try:
            if path.startswith("/static/"):
                self._serve_static(path.removeprefix("/static/"))
            elif path.startswith("/out/"):
                self._sony_liv_proxy(path, parsed.query)
            elif method == "GET" and path == "/":
                self._index(query)
            elif method == "POST" and path == "/login/sendOTP":
                self._login_send_otp()
            elif method == "POST" and path == "/login/verifyOTP":
                self._login_verify_otp()
            elif method == "GET" and path == "/logout":
                self._logout()
            elif method == "GET" and path.startswith("/live/"):
                self._live(path)
            elif method == "GET" and path.startswith("/warmup/"):
                self._warmup(path, query)
            elif method == "GET" and path == "/render.m3u8":
                self._render_m3u8(query)
            elif method == "GET" and path == "/render.ts":
                self._render_ts(query)
            elif method == "GET" and path == "/render.key":
                self._render_key(query)
            elif method == "GET" and path == "/channels":
                self._channels(query)
            elif method == "GET" and path == "/playlist.m3u":
                self._playlist(query)
            elif method == "GET" and path.startswith("/play/"):
                self._play(path, query)
            elif method == "GET" and path.startswith("/player/"):
                self._player(path, query)
            elif method == "GET" and path.startswith("/catchup/"):
                self._catchup_routes(path, query)
            elif method == "GET" and path == "/favicon.ico":
                self._redirect("/static/favicon.ico", HTTPStatus.MOVED_PERMANENTLY)
            elif method == "GET" and path.startswith("/jtvimage/"):
                self._image(path)
            elif method == "GET" and path == "/epg.xml.gz":
                self._epg_file()
            elif method == "GET" and path.startswith("/epg/"):
                self._web_epg(path)
            elif method == "GET" and path.startswith("/jtvposter/"):
                self._poster(path)
            elif method == "GET" and path.startswith("/mpd/"):
                self._live_mpd(path, query)
            elif method == "POST" and path == "/drm":
                self._drm_key(query)
            elif method == "GET" and path == "/dashtime":
                self._send_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
            elif method == "GET" and path == "/render.mpd":
                self._render_mpd(query)
            elif method == "GET" and path.startswith("/render.dash"):
                self._render_dash(path, parsed.query)
            else:
                self._json_error(HTTPStatus.NOT_FOUND, "not found")
        except Exception as exc:  # noqa: BLE001
            log.exception("request failed")
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _body_data(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8"))
        return {key: values[0] for key, values in parse_qs(raw.decode()).items()}

    def _query(self, query: dict[str, list[str]], key: str, default: str = "") -> str:
        values = query.get(key) or query.get(f"amp;{key}")
        return values[0] if values else default

    def _client_upstream_headers(self) -> dict[str, str]:
        forwarded = {}
        for header_name in ("Range", "If-Range", "If-None-Match", "If-Modified-Since"):
            value = self.headers.get(header_name)
            if value:
                forwarded[header_name] = value
        return forwarded

    def _send_bytes(
        self,
        body: bytes,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str = "application/octet-stream",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_text(
        self,
        text: str,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str = "text/plain; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._send_bytes(text.encode("utf-8"), status, content_type, headers)

    def _send_html(self, html: str) -> None:
        self._send_text(html, content_type="text/html; charset=utf-8")

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(data, separators=(",", ":")).encode("utf-8"),
            status,
            "application/json",
        )

    def _json_error(self, status: HTTPStatus, message: Any) -> None:
        self._send_json({"message": message}, status)

    def _redirect(self, location: str, status: HTTPStatus = HTTPStatus.FOUND) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_no_content(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_static(self, relative_path: str) -> None:
        path = (constants.STATIC_ROOT / unquote(relative_path)).resolve()
        if not path.is_file() or constants.STATIC_ROOT.resolve() not in path.parents:
            self._json_error(HTTPStatus.NOT_FOUND, "static file not found")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send_bytes(path.read_bytes(), content_type=content_type)

    def _proxy(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        retry_statuses: set[int] | None = None,
        on_retry: Callable[[int, bytes], RetryHeaderUpdates] | None = None,
    ) -> int:
        attempt_headers = dict(headers or {})
        for attempt in range(2):
            log.info("proxy request url=%s", redact_url(url))
            with stream_request(url, headers=attempt_headers) as resp:
                if retry_statuses and resp.status in retry_statuses and attempt == 0:
                    body = resp.stream.read()
                    log.warning(
                        "proxy retryable status=%s body=%s url=%s",
                        resp.status,
                        body_preview(body),
                        redact_url(url),
                    )
                    updates = on_retry(resp.status, body) if on_retry else None
                    if updates:
                        for header_name, header_value in updates.items():
                            if header_value is None:
                                attempt_headers.pop(header_name, None)
                            else:
                                attempt_headers[header_name] = header_value
                    continue
                self._send_proxy_response(resp, url)
                return resp.status
        return 0

    def _send_proxy_response(self, resp: StreamingHTTPResponse, url: str) -> None:
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        if resp.status >= 400:
            body = resp.stream.read()
            log.warning("proxy error body=%s", body_preview(body))
            self._send_bytes(
                body,
                _http_status(resp.status),
                content_type,
                _proxy_passthrough_headers(resp.headers, include_content_length=False),
            )
            return

        self.send_response(_http_status(resp.status))
        self.send_header("Content-Type", content_type)
        for key, value in _proxy_passthrough_headers(resp.headers).items():
            self.send_header(key, value)
        self.end_headers()

        copied = 0
        try:
            while True:
                chunk = resp.stream.read(64 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("client disconnected while proxying url=%s bytes=%s", redact_url(url), copied)
            return
        log.info(
            "proxy response status=%s bytes=%s content_type=%s url=%s",
            resp.status,
            copied,
            content_type,
            redact_url(url),
        )

    def _index(self, query: dict[str, list[str]]) -> None:
        api_response = television.channels()
        host_url = self._host_url()
        for channel in api_response.result:
            if not channel.logo_url.startswith(("http://", "https://")):
                channel.logo_url = f"{host_url}/jtvimage/{channel.logo_url}"

        language = self._query(query, "language")
        category = self._query(query, "category")
        channels = api_response.result
        if language or category:
            channels = television.filter_channels(
                channels,
                int(language or 0),
                int(category or 0),
            )
        elif cfg.default_categories or cfg.default_languages:
            channels = television.filter_channels_by_defaults(
                channels,
                cfg.default_categories,
                cfg.default_languages,
            )
        self._send_html(
            templates.render_index(
                state.title,
                channels,
                not check_logged_in(),
                login_required=self._query(query, "login") == "required",
            )
        )

    def _login_send_otp(self) -> None:
        body = self._body_data()
        number = str(body.get("number", ""))
        if not number:
            self._json_error(HTTPStatus.BAD_REQUEST, "Mobile Number not provided")
            return
        self._send_json({"status": login_send_otp(number)})

    def _login_verify_otp(self) -> None:
        body = self._body_data()
        number = str(body.get("number", ""))
        otp = str(body.get("otp", ""))
        if not number or not otp:
            self._json_error(HTTPStatus.BAD_REQUEST, "Mobile Number or OTP not provided")
            return
        result = login_verify_otp(number, otp)
        init_handlers()
        self._send_json(result)

    def _logout(self) -> None:
        if not state.logout_disabled:
            logout()
            init_handlers()
        self._redirect("/")

    def _live(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) == 2:
            quality = "auto"
            channel_id = parts[1]
        elif len(parts) == 3:
            quality = parts[1]
            channel_id = parts[2]
        else:
            self._json_error(HTTPStatus.NOT_FOUND, "invalid live route")
            return
        channel_id = channel_id.replace(".m3u8", "", 1)
        log.info("live route channel=%s quality=%s", channel_id, quality)
        if self._login_required_for_channel(channel_id):
            self._json_error(HTTPStatus.UNAUTHORIZED, "login required")
            return
        if self._redirect_custom_channel(channel_id):
            return
        self._ensure_tokens()
        assert state.tv is not None
        live_result = self._get_live_result(channel_id)
        if channel_id in {"1349", "1322"}:
            quality = "auto"
        live_url = select_best_live_hls_url(live_result, quality)
        if not live_url:
            log.warning(
                "live route no hls url channel=%s quality=%s api_message=%s",
                channel_id,
                quality,
                live_result.message,
            )
            self._json_error(HTTPStatus.NOT_FOUND, f"No stream found for channel id: {channel_id}")
            return
        live_url = to_absolute_stream_url(live_url, live_result)
        if live_result.hdnea:
            set_cached_hdnea(channel_id, live_result.hdnea)
        log.info(
            "live route selected channel=%s quality=%s url=%s hdnea=%s",
            channel_id,
            quality,
            redact_url(live_url),
            token_preview(live_result.hdnea),
        )
        encrypted = secure_url.encrypt_url(live_url)
        redirect_url = f"/render.m3u8?auth={encrypted}&channel_key_id={channel_id}"
        if quality != "auto":
            redirect_url += f"&q={quote_plus(quality)}"
        self._redirect(redirect_url)

    def _warmup(self, path: str, query: dict[str, list[str]]) -> None:
        channel_id = path.removeprefix("/warmup/").replace(".m3u8", "", 1)
        if not channel_id:
            self._json_error(HTTPStatus.BAD_REQUEST, "channel id required")
            return
        if self._login_required_for_channel(channel_id):
            self._json_error(HTTPStatus.UNAUTHORIZED, "login required")
            return
        if self._is_custom_channel(channel_id):
            self._send_no_content()
            return
        self._start_live_warmup(
            channel_id,
            self._query(query, "q", "auto"),
            self._query(query, "pm", "auto"),
        )
        self._send_no_content()

    def _render_m3u8(self, query: dict[str, list[str]]) -> None:
        auth = self._query(query, "auth")
        channel_id = self._query(query, "channel_key_id")
        quality = self._query(query, "q")
        if not auth or not channel_id:
            self._json_error(HTTPStatus.BAD_REQUEST, "auth and channel_key_id are required")
            return
        decoded_auth = self._decrypt_auth_or_recover(
            auth,
            channel_id,
            quality,
            route_name="render m3u8",
            recover_to_live=True,
        )
        if decoded_auth is None:
            return
        decoded_url = to_absolute_stream_url(decoded_auth)
        cached_hdnea = get_cached_hdnea(channel_id)
        url_token = extract_hdnea_from_url(decoded_url)
        render_url = strip_hdnea_from_url(decoded_url) if cached_hdnea else decoded_url
        cached_hdnea = cached_hdnea or url_token
        log.info(
            "render m3u8 start channel=%s quality=%s url=%s cache_token=%s url_token=%s",
            channel_id,
            quality or "auto",
            redact_url(render_url),
            token_preview(get_cached_hdnea(channel_id)),
            token_preview(url_token),
        )

        assert state.tv is not None
        body, status, new_hdnea = state.tv.render(render_url, cached_hdnea)
        if new_hdnea:
            set_cached_hdnea(channel_id, new_hdnea)
            cached_hdnea = new_hdnea

        if status in {401, 403, 404} and channel_id:
            log.warning(
                "render m3u8 upstream status=%s channel=%s; refreshing stream token",
                status,
                channel_id,
            )
            if status != 404:
                clear_cached_hdnea(channel_id)
            refreshed = self._refresh_channel_token(channel_id, force=True)
            if refreshed:
                fresh_token = extract_live_result_hdnea(refreshed)
                if fresh_token:
                    set_cached_hdnea(channel_id, fresh_token)
                    cached_hdnea = fresh_token
                body, status, new_hdnea = state.tv.render(
                    strip_hdnea_from_url(decoded_url),
                    cached_hdnea,
                )
                log.info(
                    "render m3u8 retry original channel=%s status=%s token=%s",
                    channel_id,
                    status,
                    token_preview(cached_hdnea),
                )
                if status == 404:
                    current_url = strip_hdnea_from_url(decoded_url)
                    for candidate in live_hls_url_candidates(refreshed, quality or "auto"):
                        candidate_url = to_absolute_stream_url(candidate, refreshed)
                        candidate_token = extract_hdnea_from_url(candidate_url)
                        candidate_render_url = (
                            strip_hdnea_from_url(candidate_url)
                            if cached_hdnea or candidate_token
                            else candidate_url
                        )
                        if strip_hdnea_from_url(candidate_render_url) == current_url:
                            continue
                        log.info(
                            "render m3u8 retry fresh candidate channel=%s url=%s",
                            channel_id,
                            redact_url(candidate_render_url),
                        )
                        body, status, new_hdnea = state.tv.render(
                            candidate_render_url,
                            cached_hdnea or candidate_token,
                        )
                        if new_hdnea:
                            set_cached_hdnea(channel_id, new_hdnea)
                            cached_hdnea = new_hdnea
                        if status < 400:
                            render_url = candidate_render_url
                            break
                if new_hdnea:
                    set_cached_hdnea(channel_id, new_hdnea)
                    cached_hdnea = new_hdnea

        rewritten = self._rewrite_manifest(
            body.decode("utf-8", errors="replace"),
            render_url,
            cached_hdnea,
            channel_id,
            quality,
        )
        log.info(
            "render m3u8 done channel=%s status=%s upstream_bytes=%s output_bytes=%s",
            channel_id,
            status,
            len(body),
            len(rewritten.encode("utf-8")),
        )
        self._send_text(
            rewritten,
            HTTPStatus(status),
            "application/vnd.apple.mpegurl",
            {"Cache-Control": "public, must-revalidate, max-age=3"},
        )

    def _rewrite_manifest(
        self,
        manifest: str,
        render_url: str,
        hdnea_token: str,
        channel_id: str,
        quality: str,
    ) -> str:
        base_url, params = base_and_params(render_url, hdnea_token)
        media_count = 0
        key_count = 0

        def media_replacer(match: re.Match[str]) -> str:
            nonlocal media_count
            media_count += 1
            target = match.group(0)
            target_path, _, target_params = target.partition("?")
            merged_params = _merge_params(target_params, params)
            endpoint = "/render.m3u8" if target_path.endswith(".m3u8") else "/render.ts"
            encrypted = television.create_encrypted_url(
                television.EncryptedURLConfig(
                    base_url="" if target_path.startswith("http") else base_url,
                    match=target_path,
                    params=merged_params,
                    channel_id=channel_id,
                    endpoint_url=endpoint,
                    quality=quality if endpoint == "/render.m3u8" else "",
                )
            )
            if endpoint == "/render.ts" and cfg.disable_ts_handler:
                return target
            return encrypted

        def key_replacer(match: re.Match[str]) -> str:
            nonlocal key_count
            key_count += 1
            return television.replace_key(match.group(0), params, channel_id)

        rewritten = KEY_PATTERN.sub(key_replacer, MEDIA_PATTERN.sub(media_replacer, manifest))
        log.info(
            "manifest rewrite channel=%s media_refs=%s key_refs=%s base=%s",
            channel_id,
            media_count,
            key_count,
            redact_url(base_url),
        )
        return rewritten

    def _render_ts(self, query: dict[str, list[str]]) -> None:
        auth = self._query(query, "auth")
        channel_id = self._query(query, "channel_key_id")
        if not auth or not channel_id:
            self._json_error(HTTPStatus.BAD_REQUEST, "auth and channel_key_id are required")
            return
        self._ensure_tokens()
        decoded_url = self._decrypt_auth_or_recover(
            auth,
            channel_id,
            "",
            route_name="render segment",
            recover_to_live=False,
        )
        if decoded_url is None:
            return
        cached_hdnea = get_cached_hdnea(channel_id)
        headers = {constants.USER_AGENT: constants.USER_AGENT_PLAY_TV}
        headers.update(self._client_upstream_headers())
        if cached_hdnea:
            headers["Cookie"] = f"__hdnea__={cached_hdnea}"
            decoded_url = strip_hdnea_from_url(decoded_url)
        log.info(
            "render segment channel=%s url=%s cached_token=%s",
            channel_id,
            redact_url(decoded_url),
            token_preview(cached_hdnea),
        )

        def retry_segment(status: int, _body: bytes) -> RetryHeaderUpdates:
            if status in {401, 403}:
                log.warning(
                    "render segment status=%s channel=%s; refreshing stream token",
                    status,
                    channel_id,
                )
                clear_cached_hdnea(channel_id)
                refreshed = self._refresh_channel_token(channel_id, force=True)
                fresh_token = extract_live_result_hdnea(refreshed)
                if fresh_token:
                    set_cached_hdnea(channel_id, fresh_token)
                    return {"Cookie": f"__hdnea__={fresh_token}"}
                return {"Cookie": None}
            log.warning("render segment transient status=%s channel=%s; retrying", status, channel_id)
            return None

        self._proxy(
            decoded_url,
            headers,
            retry_statuses={401, 403, 502, 503, 504},
            on_retry=retry_segment,
        )

    def _render_key(self, query: dict[str, list[str]]) -> None:
        auth = self._query(query, "auth")
        channel_id = self._query(query, "channel_key_id")
        if not auth:
            self._json_error(HTTPStatus.BAD_REQUEST, "auth not provided")
            return
        decoded_url = self._decrypt_auth_or_recover(
            auth,
            channel_id,
            "",
            route_name="render key",
            recover_to_live=False,
        )
        if decoded_url is None:
            return
        log.info("render key channel=%s url=%s", channel_id, redact_url(decoded_url))
        headers = dict(state.tv.headers)
        headers.update(
            {
                "srno": "230203144000",
                "ssotoken": state.tv.sso_token,
                "channelId": channel_id,
                constants.USER_AGENT: constants.USER_AGENT_PLAY_TV,
            }
        )
        self._proxy(decoded_url, headers)

    def _channels(self, query: dict[str, list[str]]) -> None:
        quality = self._query(query, "q")
        split_category = self._query(query, "c")
        languages = [item for item in self._query(query, "l").split(",") if item]
        skip_genres = [item for item in self._query(query, "sg").split(",") if item]
        response = television.channels()
        host_url = self._host_url().lower()
        if self._query(query, "type") == "m3u":
            self._send_playlist(
                response.result,
                host_url,
                quality,
                split_category,
                languages,
                skip_genres,
            )
            return
        for channel in response.result:
            channel.url = f"{host_url}/live/{channel.id}"
        self._send_json(response.to_api())

    def _send_playlist(
        self,
        channels: list[television.Channel],
        host_url: str,
        quality: str,
        split_category: str,
        languages: list[str],
        skip_genres: list[str],
    ) -> None:
        lines = [f'#EXTM3U x-tvg-url="{host_url}/epg.xml.gz"']
        logo_url = f"{host_url}/jtvimage"
        for channel in channels:
            language = constants.LANGUAGE_MAP.get(channel.language, "")
            category = constants.CATEGORY_MAP.get(channel.category, "")
            if languages and language not in languages:
                continue
            if skip_genres and category in skip_genres:
                continue
            channel_url = (
                f"{host_url}/live/{quality}/{channel.id}.m3u8"
                if quality
                else f"{host_url}/live/{channel.id}.m3u8"
            )
            channel_logo = (
                channel.logo_url
                if channel.logo_url.startswith(("http://", "https://"))
                else f"{logo_url}/{channel.logo_url}"
            )
            group_title = {
                "split": f"{category} - {language}",
                "language": language,
            }.get(split_category, category)
            lines.append(
                f'#EXTINF:-1 tvg-id="{channel.id}" tvg-name="{channel.name}" '
                f'tvg-logo="{channel_logo}" tvg-language="{language}" '
                f'tvg-type="{category}" group-title="{group_title}", {channel.name}'
            )
            lines.append(channel_url)
        self._send_text(
            "\n".join(lines) + "\n",
            content_type="application/vnd.apple.mpegurl",
            headers={"Content-Disposition": "attachment; filename=jiotv_playlist.m3u"},
        )

    def _playlist(self, query: dict[str, list[str]]) -> None:
        redirect = "/channels?type=m3u&" + urlencode(
            {
                "q": self._query(query, "q"),
                "c": self._query(query, "c"),
                "l": self._query(query, "l"),
                "sg": self._query(query, "sg"),
            }
        )
        self._redirect(redirect, HTTPStatus.MOVED_PERMANENTLY)

    def _play(self, path: str, query: dict[str, list[str]]) -> None:
        channel_id = path.removeprefix("/play/")
        quality = self._query(query, "q", "auto")
        player_mode = self._query(query, "pm", "auto")
        if self._login_required_for_channel(channel_id):
            self._redirect("/?login=required")
            return
        self._ensure_tokens()
        use_drm = self._should_use_mpd_player(channel_id, player_mode)
        if use_drm:
            player_url = f"/mpd/{channel_id}?q={quote_plus(quality)}"
        else:
            player_url = f"/player/{channel_id}?q={quote_plus(quality)}"
        self._start_live_warmup(
            channel_id,
            quality,
            "hd" if use_drm else "hls",
            ensure_tokens=False,
        )
        log.info(
            "play page channel=%s quality=%s mode=%s player=%s",
            channel_id,
            quality,
            "hd" if use_drm else "hls",
            player_url,
        )
        self._send_html(
            templates.render_play(
                state.title,
                player_url,
                channel_id,
                quality=quality,
                mode="hd" if use_drm else "hls",
                drm_enabled=state.enable_drm and not self._is_custom_channel(channel_id),
            )
        )

    def _player(self, path: str, query: dict[str, list[str]]) -> None:
        channel_id = path.removeprefix("/player/")
        if self._login_required_for_channel(channel_id):
            self._redirect("/?login=required")
            return
        quality = self._query(query, "q")
        if self._query(query, "af") != "1" and self._should_use_mpd_player(channel_id, "auto"):
            self._redirect(f"/mpd/{channel_id}?q={quote_plus(quality or 'auto')}")
            return
        self._start_live_warmup(channel_id, quality or "auto", "hls")
        play_url = build_hls_play_url(quality, channel_id)
        log.info("hls player channel=%s play_url=%s", channel_id, play_url)
        self._send_html(templates.render_hls_player(play_url))

    def _catchup_routes(self, path: str, query: dict[str, list[str]]) -> None:
        if path.startswith("/catchup/play/"):
            channel_id = path.removeprefix("/catchup/play/")
            if self._login_required_for_channel(channel_id):
                self._redirect("/?login=required")
                return
            start = self._query(query, "start")
            end = self._query(query, "end")
            srno = self._query(query, "srno")
            player_url = f"/catchup/render/{channel_id}?start={start}&end={end}&srno={srno}&v=6"
            self._send_html(
                templates.render_catchup_player(
                    state.title,
                    player_url,
                    self._query(query, "showname", "Catchup Show"),
                    self._query(query, "description", "No description available"),
                    self._query(query, "poster"),
                )
            )
        elif path.startswith("/catchup/render/"):
            self._catchup_render_player(path.removeprefix("/catchup/render/"), query)
        elif path.startswith("/catchup/stream/"):
            self._catchup_stream(path.removeprefix("/catchup/stream/"), query)
        else:
            self._catchup(path.removeprefix("/catchup/"), query)

    def _catchup(self, channel_id: str, query: dict[str, list[str]]) -> None:
        if self._login_required_for_channel(channel_id):
            self._redirect("/?login=required")
            return
        offset = int(self._query(query, "offset", "0") or 0)
        try:
            data = get_catchup_epg(channel_id, offset)
        except Exception as exc:  # noqa: BLE001
            self._send_html(
                templates.render_catchup(
                    state.title,
                    channel_id,
                    error=f"Could not fetch catchup data: {exc}",
                )
            )
            return
        self._send_html(templates.render_catchup(state.title, channel_id, data=data))

    def _catchup_stream(self, channel_id: str, query: dict[str, list[str]]) -> None:
        if self._login_required_for_channel(channel_id):
            self._json_error(HTTPStatus.UNAUTHORIZED, "login required")
            return
        start = self._query(query, "start")
        end = self._query(query, "end")
        srno = self._query(query, "srno")
        if not start or not end:
            self._json_error(HTTPStatus.BAD_REQUEST, "Missing start or end time")
            return
        self._ensure_tokens()
        start_fmt, end_fmt = _format_catchup_times(start, end)
        log.info(
            "catchup stream channel=%s srno=%s start=%s end=%s",
            channel_id,
            srno,
            start_fmt,
            end_fmt,
        )
        result = state.tv.get_catchup_url(channel_id, srno, start_fmt, end_fmt)
        target = result.bitrates.auto or result.result
        if not target:
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, "failed to get catchup URL")
            return
        encrypted = secure_url.encrypt_url(target)
        redirect = f"/render.m3u8?auth={encrypted}&channel_key_id={channel_id}"
        if result.hdnea and "hdnea=" not in target:
            redirect += f"&hdnea={quote_plus(result.hdnea)}"
        log.info(
            "catchup stream redirect channel=%s target=%s hdnea=%s",
            channel_id,
            redact_url(target),
            token_preview(result.hdnea),
        )
        self._redirect(redirect)

    def _catchup_render_player(self, channel_id: str, query: dict[str, list[str]]) -> None:
        if self._login_required_for_channel(channel_id):
            self._redirect("/?login=required")
            return
        start = self._query(query, "start")
        end = self._query(query, "end")
        srno = self._query(query, "srno")
        play_url = f"/catchup/stream/{channel_id}?start={start}&end={end}&srno={srno}"
        quality = self._query(query, "q")
        if quality:
            play_url += f"&q={quote_plus(quality)}"
        self._send_html(templates.render_hls_player(play_url, is_catchup=True))

    def _image(self, path: str) -> None:
        file_name = path.removeprefix("/jtvimage/")
        url = f"https://jiotv.catchup.cdn.jio.com/dare_images/images/{file_name}"
        self._proxy(url, {constants.USER_AGENT: constants.USER_AGENT_OKHTTP})

    def _epg_file(self) -> None:
        epg_path = get_path_prefix() / "epg.xml.gz"
        if not epg_path.exists():
            self._json_error(HTTPStatus.NOT_FOUND, "EPG not found")
            return
        self._send_bytes(epg_path.read_bytes(), content_type="application/gzip")

    def _web_epg(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 3:
            self._json_error(HTTPStatus.BAD_REQUEST, "invalid EPG route")
            return
        channel_id = parts[1].removeprefix("sl")
        offset = int(parts[2])
        url = constants.EPG_URL % (offset, int(channel_id))
        self._proxy(url, {constants.USER_AGENT: constants.USER_AGENT_OKHTTP})

    def _poster(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 3:
            self._json_error(HTTPStatus.NOT_FOUND, "poster not found")
            return
        url = f"{constants.EPG_POSTER_URL_SLASH}{parts[1]}/{parts[2]}"
        self._proxy(url)

    def _sony_liv_proxy(self, path: str, query: str) -> None:
        url = "https://lin-gd-001-cf.slivcdn.com" + path
        if query:
            url += "?" + query
        self._proxy(url, {constants.USER_AGENT: constants.USER_AGENT_PLAY_TV})

    def _live_mpd(self, path: str, query: dict[str, list[str]]) -> None:
        channel_id = path.removeprefix("/mpd/")
        quality = self._query(query, "q", "auto")
        player_mode = self._query(query, "pm", "auto")
        log.info("mpd page channel=%s quality=%s player_mode=%s", channel_id, quality, player_mode)
        if self._login_required_for_channel(channel_id):
            self._redirect("/?login=required")
            return
        if self._is_custom_channel(channel_id):
            channel, _ = television.get_custom_channel_by_id(channel_id)
            self._send_html(templates.render_hls_player(channel.url if channel else ""))
            return
        self._ensure_tokens()
        try:
            drm_output = self._get_drm_mpd(channel_id, quality)
        except Exception as exc:  # noqa: BLE001
            log.info("DRM MPD failed, falling back to HLS: %s", exc)
            drm_output = {}
        if not drm_output.get("play_url"):
            log.warning("mpd page fallback to hls channel=%s quality=%s", channel_id, quality)
            self._send_html(templates.render_hls_player(build_hls_play_url(quality, channel_id)))
            return
        log.info(
            "mpd page drm output channel=%s play_url=%s license=%s",
            channel_id,
            redact_url(drm_output["play_url"]),
            bool(drm_output.get("license_url")),
        )
        self._send_html(
            templates.render_drm_player(
                drm_output["play_url"],
                drm_output.get("license_url", ""),
                drm_output.get("tv_url_host", ""),
                drm_output.get("tv_url_path", ""),
                build_hls_play_url(quality, channel_id),
                f"/player/{channel_id}?q={quality}&af=1",
                player_mode if player_mode in {"hd", "auto"} else "auto",
            )
        )

    def _get_drm_mpd(self, channel_id: str, quality: str) -> dict[str, str]:
        live_result = self._get_live_result(channel_id)
        if live_result_needs_refresh(live_result):
            log.info("mpd token near expiry channel=%s; refreshing live result", channel_id)
            live_result = self._refresh_channel_token(channel_id, force=True) or live_result
        tv_url = select_best_live_mpd_url(live_result, quality) or live_result.mpd.result
        if not tv_url:
            log.warning("mpd url unavailable channel=%s quality=%s", channel_id, quality)
            return {"play_url": ""}
        log.info(
            "mpd selected channel=%s quality=%s url=%s drm=%s algo=%s",
            channel_id,
            quality,
            redact_url(tv_url),
            live_result.is_drm,
            live_result.algo_name,
        )
        channel_enc_url = secure_url.encrypt_url(tv_url)
        license_url = ""
        if live_result.mpd.key:
            enc_key = secure_url.encrypt_url(live_result.mpd.key)
            license_url = f"/drm?auth={enc_key}&channel_id={channel_id}&channel={channel_enc_url}"
        if live_result.algo_name == "timesplay":
            return {"play_url": tv_url, "license_url": license_url}
        parsed = urlparse(tv_url)
        path_parts = parsed.path.split("/")
        base_path = "/".join(path_parts[:-1]) + "/"
        return {
            "play_url": f"/render.mpd?auth={channel_enc_url}&channel_id={channel_id}&q={quality}",
            "license_url": license_url,
            "tv_url_host": secure_url.encrypt_url(parsed.netloc),
            "tv_url_path": secure_url.encrypt_url(base_path),
        }

    def _drm_key(self, query: dict[str, list[str]]) -> None:
        auth = self._query(query, "auth")
        channel = self._query(query, "channel")
        channel_id = self._query(query, "channel_id")
        cookie_header = ""
        if channel:
            decoded_channel = self._decrypt_auth_or_recover(
                channel,
                channel_id,
                "",
                route_name="drm channel",
                recover_to_live=False,
            )
            if decoded_channel is None:
                return
            head_resp = request(decoded_channel, method="HEAD", follow_redirects=False)
            cookie_header = _cookie_header_from_set_cookie(head_resp.headers)
        decoded_url = self._decrypt_auth_or_recover(
            auth,
            channel_id,
            "",
            route_name="drm key",
            recover_to_live=False,
        )
        if decoded_url is None:
            return
        log.info("drm key channel=%s url=%s", channel_id, redact_url(decoded_url))
        headers = {
            "accesstoken": state.tv.access_token,
            "Connection": "keep-alive",
            "os": "android",
            "appName": "RJIL_JioTV",
            "subscriberId": state.tv.crm,
            constants.USER_AGENT: constants.USER_AGENT_PLAY_TV,
            "ssotoken": state.tv.sso_token,
            "x-platform": "android",
            "srno": _generate_datetime(),
            "crmid": state.tv.crm,
            "channelid": channel_id,
            "uniqueId": state.tv.unique_id,
            "versionCode": constants.VERSION_CODE_389,
            "usergroup": "tvYR7NSNn7rymo3F",
            "devicetype": "phone",
            "Accept-Encoding": "gzip, deflate",
            "osVersion": "13",
            "deviceId": get_device_id(),
            "Content-Type": "application/octet-stream",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        resp = request(decoded_url, method="POST", headers=headers, body=body)
        if resp.status >= 400:
            log.warning("drm key upstream error body=%s", body_preview(resp.body))
        self._send_bytes(
            resp.body,
            _http_status(resp.status),
            resp.headers.get("Content-Type", "application/octet-stream"),
        )

    def _render_mpd(self, query: dict[str, list[str]]) -> None:
        tokens.ensure_fresh_credentials()
        self._refresh_tv()
        auth = self._query(query, "auth")
        if not auth:
            self._json_error(HTTPStatus.BAD_REQUEST, "auth query param is required")
            return
        channel_id = self._query(query, "channel_id")
        quality = self._query(query, "q")
        decrypted = self._decrypt_auth_or_recover(
            auth,
            channel_id,
            quality,
            route_name="render mpd",
            recover_to_live=False,
        )
        if decrypted is None:
            return
        log.info(
            "render mpd start channel=%s quality=%s url=%s",
            channel_id,
            quality or "auto",
            redact_url(decrypted),
        )
        parsed = urlparse(decrypted)
        proxy_host = parsed.netloc
        base_path = "/".join(parsed.path.split("/")[:-1]) + "/"
        dash_base_url = _dash_base_url(proxy_host, base_path)
        headers = {constants.USER_AGENT: constants.USER_AGENT_PLAY_TV}
        resp = request(decrypted, headers=headers)
        if resp.status in {401, 403}:
            log.warning(
                "render mpd status=%s channel=%s; refreshing credentials and stream url",
                resp.status,
                channel_id,
            )
            tokens.force_refresh_credentials()
            self._refresh_tv()
            retry_url = self._fresh_mpd_url(channel_id, quality) or strip_hdnea_from_url(decrypted)
            if retry_url != decrypted:
                decrypted = retry_url
                parsed = urlparse(decrypted)
                proxy_host = parsed.netloc
                base_path = "/".join(parsed.path.split("/")[:-1]) + "/"
                dash_base_url = _dash_base_url(proxy_host, base_path)
            resp = request(retry_url, headers=headers)
        if resp.status >= 400:
            log.warning("render mpd upstream error body=%s", body_preview(resp.body))
            self._send_bytes(
                resp.body,
                _http_status(resp.status),
                resp.headers.get("Content-Type", "application/dash+xml"),
            )
            return

        hdnea_token = _extract_set_cookie(resp.headers, "__hdnea__") or extract_hdnea_from_url(decrypted)
        if hdnea_token:
            dash_base_url = _dash_base_url(proxy_host, base_path, hdnea_token)
        body = _rewrite_mpd_base_url(resp.body, dash_base_url)
        self._send_bytes(
            body,
            _http_status(resp.status),
            resp.headers.get("Content-Type", "application/dash+xml"),
        )

    def _render_dash(self, path: str, query_string: str) -> None:
        parsed_query = parse_qs(query_string, keep_blank_values=True)
        proxy_host = self._query(parsed_query, "host")
        proxy_path = self._query(parsed_query, "path")
        request_path = path
        hdnea_token = ""
        if not proxy_host or not proxy_path:
            prefix = "/render.dash/host/"
            if path.startswith(prefix):
                trimmed = path.removeprefix(prefix)
                host_part, _, rest = trimmed.partition("/path/")
                proxy_host = host_part
                if "/hdnea/" in rest:
                    proxy_path, _, hdnea_rest = rest.partition("/hdnea/")
                    encrypted_hdnea, _, request_path = hdnea_rest.partition("/")
                    decoded_hdnea = self._decrypt_auth_or_recover(
                        encrypted_hdnea,
                        "",
                        "",
                        route_name="render dash hdnea",
                        recover_to_live=False,
                    )
                    if decoded_hdnea and decoded_hdnea.startswith("__hdnea__="):
                        hdnea_token = decoded_hdnea.removeprefix("__hdnea__=")
                else:
                    proxy_path, _, request_path = rest.partition("/")
                request_path = "/" + request_path if request_path else "/"
        if not proxy_host or not proxy_path:
            self._json_error(HTTPStatus.BAD_REQUEST, "host and path query params are required")
            return
        proxy_host = self._decrypt_auth_or_recover(
            proxy_host,
            "",
            "",
            route_name="render dash host",
            recover_to_live=False,
        )
        proxy_path = self._decrypt_auth_or_recover(
            proxy_path,
            "",
            "",
            route_name="render dash path",
            recover_to_live=False,
        )
        if proxy_host is None or proxy_path is None:
            return
        proxy_path = proxy_path.rstrip("/")
        if request_path.startswith("/render.dash"):
            request_path = request_path.removeprefix("/render.dash") or "/"
        query = _dash_passthrough_query(parsed_query)
        proxy_url = f"https://{proxy_host}{proxy_path}{request_path}{query}"
        log.info("render dash proxy url=%s", redact_url(proxy_url))
        tokens.ensure_fresh_credentials()
        headers = {constants.USER_AGENT: constants.USER_AGENT_PLAY_TV}
        headers.update(self._client_upstream_headers())
        if hdnea_token:
            headers["Cookie"] = f"__hdnea__={hdnea_token}"

        def retry_dash(status: int, _body: bytes) -> RetryHeaderUpdates:
            if status in {401, 403}:
                log.warning("render dash status=%s; refreshing credentials and retrying", status)
                tokens.force_refresh_credentials()
                self._refresh_tv()
                return {"Cookie": None}
            log.warning("render dash transient status=%s; retrying", status)
            return None

        self._proxy(
            proxy_url,
            headers,
            retry_statuses={401, 403, 502, 503, 504},
            on_retry=retry_dash,
        )

    def _host_url(self) -> str:
        protocol = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
        return f"{protocol}://{self.headers.get('Host', 'localhost')}"

    def _should_use_mpd_player(self, channel_id: str, player_mode: str) -> bool:
        return (
            state.enable_drm
            and not self._is_custom_channel(channel_id)
            and (player_mode in {"auto", "hd"} or channel_id in constants.SONY_LIST)
        )

    def _cached_live_result(self, channel_id: str) -> LiveURLOutput | None:
        if not channel_id:
            return None
        with state.live_result_cache_lock:
            entry = state.live_result_cache.get(channel_id)
            if entry is None:
                return None
            if time.time() - entry.updated_at > LIVE_RESULT_CACHE_TTL:
                state.live_result_cache.pop(channel_id, None)
                return None
            if live_result_needs_refresh(entry.result):
                state.live_result_cache.pop(channel_id, None)
                return None
            return entry.result

    def _cache_live_result(self, channel_id: str, live_result: LiveURLOutput) -> None:
        if not channel_id:
            return
        with state.live_result_cache_lock:
            state.live_result_cache[channel_id] = LiveResultCacheEntry(
                result=live_result,
                updated_at=time.time(),
            )

    def _get_live_result(self, channel_id: str) -> LiveURLOutput:
        cached = self._cached_live_result(channel_id)
        if cached is not None:
            log.info("live result cache hit channel=%s", channel_id)
            return cached
        return self._refresh_channel_token(channel_id)

    def _start_live_warmup(
        self,
        channel_id: str,
        quality: str,
        player_mode: str,
        *,
        ensure_tokens: bool = True,
    ) -> None:
        if not channel_id or self._is_custom_channel(channel_id):
            return
        if self._cached_live_result(channel_id) is not None:
            return
        with state.live_result_cache_lock:
            if channel_id in state.live_warmup_inflight:
                return
            state.live_warmup_inflight.add(channel_id)

        def warmup() -> None:
            try:
                if ensure_tokens:
                    self._ensure_tokens()
                self._get_live_result(channel_id)
                log.info(
                    "live warmup complete channel=%s quality=%s mode=%s",
                    channel_id,
                    quality,
                    player_mode,
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("live warmup failed channel=%s error=%s", channel_id, exc)
            finally:
                with state.live_result_cache_lock:
                    state.live_warmup_inflight.discard(channel_id)

        threading.Thread(
            target=warmup,
            name=f"jio-live-warmup-{channel_id}",
            daemon=True,
        ).start()

    def _decrypt_auth_or_recover(
        self,
        auth: str,
        channel_id: str,
        quality: str,
        *,
        route_name: str,
        recover_to_live: bool,
    ) -> str | None:
        try:
            return secure_url.decrypt_url(auth)
        except ValueError as exc:
            log.warning(
                "%s stale or invalid auth channel=%s quality=%s error=%s",
                route_name,
                channel_id,
                quality or "auto",
                exc,
            )
            if recover_to_live and channel_id:
                live_path = (
                    f"/live/{quality}/{channel_id}.m3u8"
                    if quality
                    else f"/live/auto/{channel_id}.m3u8"
                )
                log.info(
                    "%s recovering stale auth by redirecting to %s",
                    route_name,
                    live_path,
                )
                self._redirect(live_path)
            else:
                self._json_error(
                    HTTPStatus.FORBIDDEN,
                    "stale or invalid stream auth; reload the channel",
                )
            return None

    def _is_custom_channel(self, channel_id: str) -> bool:
        if not cfg.custom_channels_file:
            return False
        _, exists = television.get_custom_channel_by_id(channel_id)
        return exists

    def _login_required_for_channel(self, channel_id: str) -> bool:
        return not check_logged_in() and not self._is_custom_channel(channel_id)

    def _redirect_custom_channel(self, channel_id: str) -> bool:
        if not self._is_custom_channel(channel_id):
            return False
        channel, exists = television.get_custom_channel_by_id(channel_id)
        if not exists or channel is None:
            self._json_error(HTTPStatus.NOT_FOUND, f"Custom channel {channel_id} not found")
            return True
        self._redirect(channel.url)
        return True

    def _ensure_tokens(self) -> None:
        try:
            if tokens.ensure_fresh_tokens():
                self._refresh_tv()
        except Exception as exc:  # noqa: BLE001
            log.debug("token refresh skipped/failed: %s", exc)

    def _refresh_tv(self) -> None:
        state.tv = Television(get_credentials())
        with state.live_result_cache_lock:
            state.live_result_cache.clear()

    def _refresh_channel_token(self, channel_id: str, *, force: bool = False) -> LiveURLOutput:
        with state.token_refresh_lock:
            lock = state.token_refresh_locks.setdefault(channel_id, threading.Lock())
        with lock:
            if not force:
                cached = self._cached_live_result(channel_id)
                if cached is not None:
                    log.info("live result cache hit after lock channel=%s", channel_id)
                    return cached
            log.info("refreshing live result channel=%s", channel_id)
            assert state.tv is not None
            live_result = state.tv.live(channel_id)
            self._cache_live_result(channel_id, live_result)
            return live_result

    def _fresh_mpd_url(self, channel_id: str, quality: str) -> str:
        if not channel_id:
            return ""
        try:
            live_result = self._refresh_channel_token(channel_id, force=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("fresh MPD URL lookup failed: %s", exc)
            return ""
        return select_best_live_mpd_url(live_result, quality) or live_result.mpd.result


async def _read_asgi_body(receive: ASGIReceive) -> bytes:
    body_parts: list[bytes] = []
    while True:
        event = await receive()
        if event["type"] == "http.disconnect":
            break
        body_parts.append(event.get("body", b""))
        if not event.get("more_body", False):
            break
    return b"".join(body_parts)


def _run_asgi_handler(
    scope: ASGIScope,
    body: bytes,
    output_queue: queue.Queue[ResponseQueueItem],
) -> None:
    try:
        client_address = scope.get("client") or ("0.0.0.0", 0)
        JioTVRequestHandler(
            str(scope["method"]),
            _asgi_request_target(scope),
            _asgi_headers(scope, len(body)),
            body,
            (str(client_address[0]), int(client_address[1] or 0)),
            output_queue,
        )
    except BaseException as exc:  # noqa: BLE001
        log.exception("asgi handler failed")
        output_queue.put(exc)
    finally:
        output_queue.put(None)


def _asgi_request_target(scope: ASGIScope) -> str:
    raw_path = scope.get("raw_path") or str(scope.get("path", "/")).encode(
        "utf-8",
        errors="surrogatepass",
    )
    query_string = scope.get("query_string", b"")
    target = raw_path.decode("latin-1")
    if query_string:
        target += "?" + query_string.decode("latin-1")
    return target


def _asgi_headers(scope: ASGIScope, body_length: int = 0) -> Message:
    headers = Message()
    header_items = list(scope.get("headers", []))
    for name, value in header_items:
        headers.add_header(
            name.decode("latin-1"),
            value.decode("latin-1"),
        )
    lower_header_names = {name.lower() for name, _value in header_items}
    if b"host" not in lower_header_names:
        server_host, server_port = scope.get("server") or ("localhost", 5001)
        host = str(server_host)
        if server_port:
            host = f"{host}:{server_port}"
        headers.add_header("host", host)
    if body_length and b"content-length" not in lower_header_names:
        headers.add_header("content-length", str(body_length))
    return headers


async def _send_asgi_response(
    output_queue: queue.Queue[ResponseQueueItem],
    send: ASGISend,
) -> None:
    pending = b""
    response_started = False
    while True:
        item = await asyncio.to_thread(output_queue.get)
        if item is None:
            break
        if isinstance(item, BaseException):
            if not response_started:
                await _send_asgi_error(send)
            else:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        if not response_started:
            pending += item
            header_end = pending.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            status, headers = _parse_raw_http_response_head(pending[:header_end])
            await send({"type": "http.response.start", "status": status, "headers": headers})
            response_started = True
            body = pending[header_end + 4 :]
            pending = b""
            if body:
                await send({"type": "http.response.body", "body": body, "more_body": True})
            continue
        await send({"type": "http.response.body", "body": item, "more_body": True})

    if not response_started:
        await _send_asgi_error(send)
        return
    await send({"type": "http.response.body", "body": b"", "more_body": False})


def _parse_raw_http_response_head(head: bytes) -> tuple[int, list[tuple[bytes, bytes]]]:
    lines = head.split(b"\r\n")
    status = HTTPStatus.INTERNAL_SERVER_ERROR
    if lines:
        parts = lines[0].split(b" ", 2)
        if len(parts) >= 2:
            try:
                status = HTTPStatus(int(parts[1]))
            except ValueError:
                status = HTTPStatus.INTERNAL_SERVER_ERROR
    headers: list[tuple[bytes, bytes]] = []
    for line in lines[1:]:
        name, separator, value = line.partition(b":")
        if not separator:
            continue
        if name.lower() in {b"connection", b"date", b"server"}:
            continue
        headers.append((name.strip().lower(), value.strip()))
    return int(status), headers


async def _send_asgi_error(send: ASGISend) -> None:
    body = b"internal server error"
    await send(
        {
            "type": "http.response.start",
            "status": HTTPStatus.INTERNAL_SERVER_ERROR,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def _http_status(status: int) -> HTTPStatus:
    try:
        return HTTPStatus(status)
    except ValueError:
        return HTTPStatus.BAD_GATEWAY


def _proxy_passthrough_headers(
    headers: Message,
    *,
    include_content_length: bool = True,
) -> dict[str, str]:
    passthrough = [
        "Accept-Ranges",
        "Cache-Control",
        "Content-Encoding",
        "Content-Range",
        "ETag",
        "Last-Modified",
    ]
    if include_content_length:
        passthrough.append("Content-Length")
    forwarded: dict[str, str] = {}
    for key in passthrough:
        value = headers.get(key)
        if value:
            forwarded[key] = value
    return forwarded


def _dash_base_url(proxy_host: str, proxy_path: str, hdnea_token: str = "") -> str:
    encrypted_host = secure_url.encrypt_url(proxy_host)
    encrypted_path = secure_url.encrypt_url(proxy_path)
    base_url = f"/render.dash/host/{encrypted_host}/path/{encrypted_path}"
    if not hdnea_token:
        return base_url
    encrypted_hdnea = secure_url.encrypt_url("__hdnea__=" + hdnea_token)
    return f"{base_url}/hdnea/{encrypted_hdnea}"


def _rewrite_mpd_base_url(body: bytes, dash_base_url: str) -> bytes:
    replacement = f"<BaseURL>{dash_base_url}/dash/</BaseURL>".encode()
    base_url_pattern = re.compile(br"<BaseURL>.*?</BaseURL>", re.DOTALL)
    if base_url_pattern.search(body):
        return base_url_pattern.sub(replacement, body)

    period_pattern = re.compile(br"<Period(\s+[^>]*?)?\s*/?>")
    insert = f"\n<BaseURL>{dash_base_url}/</BaseURL>".encode()
    return period_pattern.sub(lambda match: match.group(0) + insert, body)


def _extract_set_cookie(headers: Any, name: str) -> str:
    for header in headers.get_all("Set-Cookie", []):
        for part in header.split(";"):
            trimmed = part.strip()
            prefix = f"{name}="
            if trimmed.startswith(prefix):
                return trimmed.removeprefix(prefix)
    return ""


def _cookie_header_from_set_cookie(headers: Any) -> str:
    cookies = []
    for header in headers.get_all("Set-Cookie", []):
        cookie = header.split(";", 1)[0].strip()
        if cookie:
            cookies.append(cookie)
    return "; ".join(cookies)


def _dash_passthrough_query(parsed_query: dict[str, list[str]]) -> str:
    pairs = [
        (key, value)
        for key, values in parsed_query.items()
        if key not in {"host", "path"}
        for value in values
    ]
    return ("?" + urlencode(pairs)) if pairs else ""


def get_catchup_epg(channel_id: str, offset: int) -> list[dict[str, Any]]:
    url = (
        "https://jiotvapi.cdn.jio.com/apis/v1.3/getepg/get?"
        f"offset={offset}&channel_id={channel_id}&langId=6"
    )
    resp = request(
        url,
        headers={
            "Host": "jiotvapi.cdn.jio.com",
            constants.USER_AGENT: "okhttp/4.12.13",
            constants.ACCEPT_ENCODING: "gzip",
        },
    )
    if resp.status != 200:
        raise RuntimeError(f"catchup EPG failed with status {resp.status}")
    data = resp.json()
    epg_items = data.get("epg", [])
    now_ms = int(time.time() * 1000)
    result = []
    for item in reversed(epg_items):
        start = int(item.get("startEpoch", 0) or 0)
        end = int(item.get("endEpoch", 0) or 0)
        if start < 100000000000:
            start *= 1000
        if end < 100000000000:
            end *= 1000
        if start > now_ms:
            continue
        item["startEpoch"] = start
        item["endEpoch"] = end
        item["showtime"] = datetime.fromtimestamp(start / 1000).strftime("%I:%M %p")
        item["endtime"] = datetime.fromtimestamp(end / 1000).strftime("%I:%M %p")
        item["IsLive"] = start <= now_ms < end
        if isinstance(item.get("srno"), (int, float)):
            item["srno"] = str(int(item["srno"]))
        result.append(item)
    return result


def _merge_params(first: str, second: str) -> str:
    values: list[tuple[str, str]] = []
    values.extend(parse_qs(first, keep_blank_values=True).items())
    flattened: list[tuple[str, str]] = []
    for key, items in values:
        for item in items:
            flattened.append((key, item))
    for key, items in parse_qs(second, keep_blank_values=True).items():
        for item in items:
            flattened.append((key, item))
    return urlencode(flattened)


def _format_catchup_times(start: str, end: str) -> tuple[str, str]:
    try:
        start_ms = int(start)
        end_ms = int(end)
    except ValueError:
        return start, end
    return (
        datetime.fromtimestamp(start_ms / 1000, timezone.utc).strftime("%Y%m%dT%H%M%S"),
        datetime.fromtimestamp(end_ms / 1000, timezone.utc).strftime("%Y%m%dT%H%M%S"),
    )


def _generate_datetime() -> str:
    now = datetime.now()
    return f"{now:%y%m%d%H%M}{int(now.microsecond / 1000):03d}"
