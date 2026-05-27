from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

from jio_py import constants, secure_url, templates
from jio_py.config import JioTVConfig, parse_simple_yaml
from jio_py.models import Bitrates, LiveURLOutput
from jio_py.server import (
    JioTVRequestHandler,
    _asgi_headers,
    _asgi_request_target,
    _cookie_header_from_set_cookie,
    _dash_passthrough_query,
    _parse_raw_http_response_head,
    _rewrite_mpd_base_url,
    state,
)
from jio_py.streaming import (
    extract_hdnea_from_url,
    hdnea_remaining_lifetime,
    strip_hdnea_from_url,
    to_absolute_stream_url,
)
from jio_py.television import EncryptedURLConfig, create_encrypted_url, load_custom_channels
from jio_py.tokens import should_refresh_token


class SecureURLTests(unittest.TestCase):
    def test_round_trip_encryption(self) -> None:
        secure_url.init()
        original = "https://example.com/path/playlist.m3u8?hdnea=abc&x=1"
        encrypted = secure_url.encrypt_url(original)
        self.assertNotEqual(original, encrypted)
        self.assertEqual(secure_url.decrypt_url(encrypted), original)


class ConfigTests(unittest.TestCase):
    def test_simple_yaml_parser(self) -> None:
        parsed = parse_simple_yaml(
            """
epg: true
title: Test App
default_categories: [8, 5]
channels:
  - id: custom_news_1
    name: Sample News
    is_hd: true
"""
        )
        self.assertEqual(parsed["epg"], True)
        self.assertEqual(parsed["title"], "Test App")
        self.assertEqual(parsed["default_categories"], [8, 5])
        self.assertEqual(parsed["channels"][0]["id"], "custom_news_1")

    def test_config_apply_types(self) -> None:
        config = JioTVConfig()
        config.apply(
            {
                "debug": "true",
                "default_languages": "1,6",
                "title": "Converted",
            }
        )
        self.assertTrue(config.debug)
        self.assertEqual(config.default_languages, [1, 6])
        self.assertEqual(config.title, "Converted")

    def test_drm_defaults_to_enabled(self) -> None:
        self.assertTrue(JioTVConfig().drm)


class TelevisionURLTests(unittest.TestCase):
    def test_create_encrypted_url_shape(self) -> None:
        secure_url.init()
        result = create_encrypted_url(
            EncryptedURLConfig(
                base_url="https://cdn.example.com/live/",
                match="segment.ts",
                params="token=abc",
                channel_id="123",
                endpoint_url="/render.ts",
            )
        )
        self.assertTrue(result.startswith("/render.ts?auth="))
        self.assertIn("channel_key_id=123", result)

    def test_load_custom_channels_prefixes_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "custom-channels.yml"
            path.write_text(
                """
channels:
  - id: test_channel
    name: Test Channel
    url: https://example.com/test.m3u8
    logo_url: https://example.com/logo.png
    category: 12
    language: 6
    is_hd: true
""",
                encoding="utf-8",
            )
            channels = load_custom_channels(str(path))
        self.assertEqual(channels[0].id, "cc_test_channel")
        self.assertTrue(channels[0].is_hd)


class StreamingTests(unittest.TestCase):
    def test_hdnea_helpers(self) -> None:
        expires = int(time.time()) + 120
        token = f"exp={expires}~acl=/*"
        url = f"https://example.com/live.m3u8?x=1&hdnea={token}"
        self.assertEqual(extract_hdnea_from_url(url), token)
        self.assertNotIn("hdnea", strip_hdnea_from_url(url))
        remaining, ok = hdnea_remaining_lifetime(token)
        self.assertTrue(ok)
        self.assertGreater(remaining, 0)

    def test_absolute_stream_url(self) -> None:
        self.assertEqual(
            to_absolute_stream_url("//example.com/live.m3u8"),
            "https://example.com/live.m3u8",
        )
        self.assertTrue(to_absolute_stream_url("path/live.m3u8").startswith("https://"))


class DashProxyTests(unittest.TestCase):
    def test_rewrite_mpd_replaces_existing_base_url(self) -> None:
        body = b"<MPD><Period><BaseURL>https://cdn.example.com/dash/</BaseURL></Period></MPD>"
        rewritten = _rewrite_mpd_base_url(body, "/render.dash/host/a/path/b")
        self.assertIn(b"<BaseURL>/render.dash/host/a/path/b/dash/</BaseURL>", rewritten)
        self.assertNotIn(b"cdn.example.com", rewritten)

    def test_rewrite_mpd_inserts_base_url_when_missing(self) -> None:
        body = b'<MPD><Period id="1"><AdaptationSet /></Period></MPD>'
        rewritten = _rewrite_mpd_base_url(body, "/render.dash/host/a/path/b")
        self.assertIn(b'<Period id="1">', rewritten)
        self.assertIn(b"<BaseURL>/render.dash/host/a/path/b/</BaseURL>", rewritten)

    def test_dash_passthrough_query_drops_proxy_keys(self) -> None:
        query = _dash_passthrough_query(
            {"host": ["enc-host"], "path": ["enc-path"], "token": ["abc"], "empty": [""]}
        )
        self.assertEqual(query, "?token=abc&empty=")

    def test_handler_query_accepts_html_escaped_keys(self) -> None:
        value = JioTVRequestHandler._query(
            object(),
            {"amp;channel_id": ["476"], "auth": ["abc"]},
            "channel_id",
        )
        self.assertEqual(value, "476")

    def test_cookie_header_from_set_cookie(self) -> None:
        headers = Message()
        headers.add_header("Set-Cookie", "__hdnea__=abc; Path=/; HttpOnly")
        headers.add_header("Set-Cookie", "foo=bar; Domain=example.com")
        self.assertEqual(_cookie_header_from_set_cookie(headers), "__hdnea__=abc; foo=bar")

    def test_client_upstream_headers_forward_range_only(self) -> None:
        handler = object.__new__(JioTVRequestHandler)
        handler.headers = Message()
        handler.headers.add_header("Range", "bytes=100-")
        handler.headers.add_header("If-Range", "etag-1")
        handler.headers.add_header("Authorization", "do-not-forward")
        self.assertEqual(
            handler._client_upstream_headers(),
            {"Range": "bytes=100-", "If-Range": "etag-1"},
        )


class LiveWarmupTests(unittest.TestCase):
    def setUp(self) -> None:
        state.live_result_cache.clear()
        state.live_warmup_inflight.clear()

    def tearDown(self) -> None:
        state.live_result_cache.clear()
        state.live_warmup_inflight.clear()

    def test_live_result_cache_returns_recent_entry(self) -> None:
        handler = object.__new__(JioTVRequestHandler)
        live_result = LiveURLOutput(bitrates=Bitrates(auto="https://example.com/live.m3u8"))
        handler._cache_live_result("155", live_result)
        self.assertIs(handler._cached_live_result("155"), live_result)


class PlaybackRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_drm = state.enable_drm
        state.enable_drm = True

    def tearDown(self) -> None:
        state.enable_drm = self.previous_drm

    def test_sony_max_sd_forces_mpd_player(self) -> None:
        handler = object.__new__(JioTVRequestHandler)
        handler._is_custom_channel = lambda _channel_id: False
        self.assertIn("289", constants.SONY_LIST)
        self.assertTrue(handler._should_use_mpd_player("289", "hls"))

    def test_auto_mode_prefers_mpd_when_drm_enabled(self) -> None:
        handler = object.__new__(JioTVRequestHandler)
        handler._is_custom_channel = lambda _channel_id: False
        self.assertTrue(handler._should_use_mpd_player("143", "auto"))
        self.assertFalse(handler._should_use_mpd_player("143", "hls"))


class ASGIBridgeTests(unittest.TestCase):
    def test_asgi_request_target_keeps_query_string(self) -> None:
        target = _asgi_request_target(
            {
                "raw_path": b"/login/verifyOTP",
                "query_string": b"source=test",
            }
        )
        self.assertEqual(target, "/login/verifyOTP?source=test")

    def test_asgi_headers_add_host_and_content_length(self) -> None:
        headers = _asgi_headers(
            {
                "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
                "server": ("127.0.0.1", 5001),
            },
            10,
        )
        self.assertEqual(headers.get("host"), "127.0.0.1:5001")
        self.assertEqual(headers.get("content-length"), "10")
        self.assertEqual(headers.get("content-type"), "application/x-www-form-urlencoded")

    def test_parse_raw_http_response_head_skips_connection_header(self) -> None:
        status, headers = _parse_raw_http_response_head(
            b"HTTP/1.0 302 Found\r\n"
            b"Server: JIO_tv\r\n"
            b"Date: Mon, 25 May 2026 00:00:00 GMT\r\n"
            b"Location: /x\r\n"
            b"Connection: close\r\n\r\n"
        )
        self.assertEqual(status, 302)
        self.assertIn((b"location", b"/x"), headers)
        self.assertNotIn((b"connection", b"close"), headers)
        self.assertNotIn((b"server", b"JIO_tv"), headers)


class TemplateTests(unittest.TestCase):
    def test_hls_player_uses_stability_buffer_settings(self) -> None:
        html = templates.render_hls_player("/live/155.m3u8")
        self.assertIn("lowLatencyMode: false", html)
        self.assertIn("maxBufferLength: 30", html)
        self.assertIn("fragLoadingMaxRetry: 6", html)

    def test_hls_player_preloads_live_manifest(self) -> None:
        html = templates.render_hls_player("/live/155.m3u8")
        self.assertIn('rel="preload"', html)
        self.assertIn('href="/live/155.m3u8"', html)

    def test_play_iframe_prioritizes_autoplay_media(self) -> None:
        html = templates.render_play("Test", "/player/155?q=auto", "155")
        self.assertIn('loading="eager"', html)
        self.assertIn('fetchpriority="high"', html)
        self.assertIn('allow="autoplay; encrypted-media; fullscreen"', html)

    def test_drm_player_keeps_query_ampersands_inside_script_urls(self) -> None:
        html = templates.render_drm_player(
            "/render.mpd?auth=abc&channel_id=476&q=auto",
            "/drm?auth=key&channel_id=476&channel=abc",
            hls_player_fallback_url="/player/476?q=auto&af=1",
        )
        self.assertIn('"/render.mpd?auth=abc&channel_id=476&q=auto"', html)
        self.assertIn('"/drm?auth=key&channel_id=476&channel=abc"', html)
        self.assertNotIn("amp;channel_id", html)

    def test_drm_player_uses_stability_buffer_settings(self) -> None:
        html = templates.render_drm_player("/render.mpd?auth=abc&channel_id=476&q=auto")
        self.assertIn("bufferingGoal: 30", html)
        self.assertIn("rebufferingGoal: 6", html)
        self.assertIn("lowLatencyMode: false", html)


class TokenTests(unittest.TestCase):
    def test_fallback_token_refresh_threshold(self) -> None:
        now = datetime.now(timezone.utc)
        old_refresh = str(int((now - timedelta(hours=2)).timestamp()))
        self.assertTrue(
            should_refresh_token(
                "not-a-jwt",
                old_refresh,
                timedelta(seconds=30),
                timedelta(hours=2),
                timedelta(minutes=10),
                now,
            )
        )


if __name__ == "__main__":
    unittest.main()
