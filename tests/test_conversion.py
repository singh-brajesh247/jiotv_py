from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from jiotv_py import constants, secure_url, television, templates
from jiotv_py.cli import build_parser
from jiotv_py.config import JioTVConfig, cfg, parse_simple_yaml
from jiotv_py.diagnostics import body_preview, redact_url
from jiotv_py.http_client import (
    ProxyConfig,
    SocksHTTPSHandler,
    describe_proxy,
    parse_proxy_url,
)
from jiotv_py.models import Bitrates, LiveURLOutput, SSAI
from jiotv_py.server import (
    CachedResponse,
    JioTVRequestHandler,
    _asgi_headers,
    _asgi_request_target,
    _cookie_header_from_set_cookie,
    _dash_passthrough_query,
    _filter_broken_sony_variants,
    _get_segment_cache,
    _manifest_reference_base_url,
    _parse_raw_http_response_head,
    _rewrite_mpd_base_url,
    _segment_cache_key,
    _set_segment_cache,
    state,
)
from jiotv_py.streaming import (
    extract_hdnea_from_url,
    hdnea_remaining_lifetime,
    live_hls_url_candidates,
    select_best_live_hls_url,
    select_best_live_ssai_url,
    strip_hdnea_from_url,
    to_absolute_stream_url,
)
from jiotv_py.television import (
    EncryptedURLConfig,
    _extract_ssai_media_url,
    _with_ssai_session_params,
    create_encrypted_url,
    load_custom_channels,
)
from jiotv_py.tokens import should_refresh_token


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

    def test_config_apply_scale_knobs_as_ints(self) -> None:
        config = JioTVConfig()
        config.apply(
            {
                "asgi_worker_threads": "512",
                "asgi_request_queue_limit": "5000",
                "channels_cache_ttl": "120",
                "segment_cache_max_bytes": "1048576",
            }
        )
        self.assertEqual(config.asgi_worker_threads, 512)
        self.assertEqual(config.asgi_request_queue_limit, 5000)
        self.assertEqual(config.channels_cache_ttl, 120)
        self.assertEqual(config.segment_cache_max_bytes, 1048576)

    def test_drm_defaults_to_enabled(self) -> None:
        self.assertTrue(JioTVConfig().drm)

    def test_proxy_url_defaults_to_http_scheme(self) -> None:
        proxy = parse_proxy_url("127.0.0.1:8080")
        self.assertIsNotNone(proxy)
        assert proxy is not None
        self.assertEqual(proxy.scheme, "http")
        self.assertEqual(proxy.proxy_url, "http://127.0.0.1:8080")
        self.assertFalse(proxy.is_socks)

    def test_socks_proxy_url_supports_remote_dns_and_credentials(self) -> None:
        proxy = parse_proxy_url("socks5h://user:p%40ss@proxy.example:1080")
        self.assertIsNotNone(proxy)
        assert proxy is not None
        self.assertEqual(proxy.scheme, "socks5h")
        self.assertEqual(proxy.host, "proxy.example")
        self.assertEqual(proxy.port, 1080)
        self.assertEqual(proxy.username, "user")
        self.assertEqual(proxy.password, "p@ss")
        self.assertTrue(proxy.is_socks)
        self.assertTrue(proxy.remote_dns)
        self.assertEqual(
            describe_proxy("socks5h://user:p%40ss@proxy.example:1080"),
            "socks5h://proxy.example:1080 credentials=yes remote_dns=yes",
        )

    def test_proxy_url_rejects_unsupported_scheme(self) -> None:
        with self.assertRaises(ValueError):
            parse_proxy_url("ftp://proxy.example:21")

    def test_proxy_cli_argument_is_accepted_before_or_after_serve(self) -> None:
        parser = build_parser()
        root_args = parser.parse_args(["--proxy", "http://127.0.0.1:8080", "serve"])
        serve_args = parser.parse_args(["serve", "--proxy", "socks5://127.0.0.1:1080"])
        self.assertEqual(root_args.proxy, "http://127.0.0.1:8080")
        self.assertEqual(serve_args.proxy, "socks5://127.0.0.1:1080")

    def test_socks_https_handler_uses_version_safe_context_args(self) -> None:
        handler = SocksHTTPSHandler(ProxyConfig("socks5", "127.0.0.1", 1080))
        handler.do_open = lambda http_class, req, **kwargs: kwargs  # type: ignore[method-assign]
        kwargs = handler.https_open(object())
        self.assertIn("context", kwargs)
        self.assertNotIn("check_hostname", kwargs)


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

    def test_extract_ssai_media_url(self) -> None:
        body = b'{"mediaURL":"https://example.com/resolved.m3u8","vastURL":"https://ads"}'
        self.assertEqual(
            _extract_ssai_media_url(body),
            "https://example.com/resolved.m3u8",
        )
        self.assertEqual(_extract_ssai_media_url(b"#EXTM3U\n"), "")

    def test_ssai_session_params_match_android_session_request(self) -> None:
        result = _with_ssai_session_params("https://example.com/session?x=1")
        self.assertIn("x=1", result)
        self.assertIn("cgi=defaultCGI", result)
        self.assertIn("vr=AN-2.4.15", result)
        self.assertIn("md_dvm=Android", result)

    def test_ssai_session_params_skip_media_manifests(self) -> None:
        url = "https://example.com/live/playlist.m3u8?x=1"
        self.assertEqual(_with_ssai_session_params(url), url)


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

    def test_live_url_selection_prefers_normal_hls_by_default(self) -> None:
        live_result = LiveURLOutput(
            bitrates=Bitrates(auto="https://example.com/hls.m3u8"),
            ssai=SSAI(bitrates=Bitrates(auto="https://example.com/ssai-session")),
        )
        self.assertEqual(
            select_best_live_hls_url(live_result, "auto"),
            "https://example.com/hls.m3u8",
        )

    def test_live_url_selection_can_prefer_ssai_bitrates(self) -> None:
        live_result = LiveURLOutput(
            bitrates=Bitrates(auto="https://example.com/hls.m3u8"),
            ssai=SSAI(
                bitrates=Bitrates(
                    auto="https://example.com/ssai-auto",
                    high="https://example.com/ssai-high",
                )
            ),
        )
        self.assertEqual(
            select_best_live_ssai_url(live_result, "high"),
            "https://example.com/ssai-high",
        )
        self.assertEqual(
            select_best_live_hls_url(live_result, "high", prefer_ssai=True),
            "https://example.com/ssai-high",
        )

    def test_live_candidates_can_order_ssai_first(self) -> None:
        live_result = LiveURLOutput(
            bitrates=Bitrates(auto="https://example.com/hls.m3u8"),
            ssai=SSAI(bitrates=Bitrates(auto="https://example.com/ssai-auto")),
        )
        self.assertEqual(
            live_hls_url_candidates(live_result, "auto", prefer_ssai=True)[:2],
            ["https://example.com/ssai-auto", "https://example.com/hls.m3u8"],
        )

    def test_live_url_output_parses_android_ssai_fields(self) -> None:
        result = LiveURLOutput.from_api(
            {
                "code": 200,
                "algoNumber": 7,
                "playbackToken": "play-token",
                "ssai": {
                    "bitrates": {"auto": "https://example.com/ssai-auto"},
                    "ssaiPlaybackUrl": "https://example.com/ssai-playback.m3u8",
                },
            }
        )
        self.assertEqual(result.algo_number, 7)
        self.assertEqual(result.playback_token, "play-token")
        self.assertEqual(result.ssai.bitrates.auto, "https://example.com/ssai-auto")
        self.assertEqual(
            result.ssai.playback_url,
            "https://example.com/ssai-playback.m3u8",
        )


class APICompatibilityTests(unittest.TestCase):
    def test_android_406_channel_listing_api_is_used(self) -> None:
        self.assertIn("/apis/v3.1/getMobileChannelList/get/", constants.CHANNELS_API_URL)
        self.assertIn("version=406", constants.CHANNELS_API_URL)
        self.assertEqual(constants.ACCESS_TOKEN, "accesstoken")

    def test_redaction_handles_lowercase_access_token_header_name(self) -> None:
        self.assertIn(
            "accesstoken=secret...",
            redact_url("https://example.com/path?accesstoken=secret-token-value"),
        )
        self.assertIn(
            '"accesstoken":"<redacted>"',
            body_preview(b'{"accesstoken":"secret-token-value"}'),
        )


class ChannelsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_ttl = cfg.channels_cache_ttl
        self.previous_custom_channels_file = cfg.custom_channels_file
        cfg.channels_cache_ttl = 300
        cfg.custom_channels_file = ""
        television.clear_channels_cache()

    def tearDown(self) -> None:
        cfg.channels_cache_ttl = self.previous_ttl
        cfg.custom_channels_file = self.previous_custom_channels_file
        television.clear_channels_cache()

    def test_channels_cache_reuses_fetch_and_returns_mutable_clone(self) -> None:
        calls = 0

        def fake_read_json_response(_url: str, _headers: dict[str, str]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "code": 200,
                "message": "ok",
                "result": [
                    {
                        "channel_id": "143",
                        "channel_name": "News",
                        "logoUrl": "logo.png",
                    }
                ],
            }

        with patch("jiotv_py.television.read_json_response", side_effect=fake_read_json_response):
            first = television.channels()
            first.result[0].url = "mutated-by-handler"
            second = television.channels()

        self.assertEqual(calls, 1)
        self.assertEqual(second.result[0].id, "143")
        self.assertEqual(second.result[0].url, "")

    def test_channels_fetch_falls_back_to_alternate_endpoint(self) -> None:
        calls = []

        def fake_read_json_response(url: str, _headers: dict[str, str]) -> dict[str, object]:
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("request failed with status 450")
            return {
                "code": 200,
                "message": "ok",
                "result": [{"channel_id": "155", "channel_name": "Sports"}],
            }

        with patch("jiotv_py.television.read_json_response", side_effect=fake_read_json_response):
            response = television.channels()

        self.assertEqual(len(calls), 2)
        self.assertIn("jiotvapi.cdn.jio.com", calls[0])
        self.assertIn("jiotv.data.cdn.jio.com", calls[1])
        self.assertEqual(response.result[0].id, "155")

    def test_channels_refresh_failure_serves_stale_cache(self) -> None:
        cfg.channels_cache_ttl = 1
        state = {"fail": False}

        def fake_read_json_response(_url: str, _headers: dict[str, str]) -> dict[str, object]:
            if state["fail"]:
                raise RuntimeError("upstream down")
            return {
                "code": 200,
                "message": "ok",
                "result": [{"channel_id": "143", "channel_name": "News"}],
            }

        with patch("jiotv_py.television.read_json_response", side_effect=fake_read_json_response):
            self.assertEqual(television.channels().result[0].id, "143")
            time.sleep(1.01)
            state["fail"] = True
            self.assertEqual(television.channels().result[0].id, "143")


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

    def test_manifest_rewrite_handles_fmp4_segments_and_relative_keys(self) -> None:
        secure_url.init()
        handler = object.__new__(JioTVRequestHandler)
        manifest = (
            "#EXTM3U\n"
            '#EXT-X-KEY:METHOD=AES-128,URI="keys/live.key"\n'
            '#EXT-X-MAP:URI="init.mp4"\n'
            "video/seg-1.m4s?range=1\n"
        )
        rewritten = handler._rewrite_manifest(
            manifest,
            "https://cdn.example.com/live/master.m3u8?foo=bar&hdnea=old",
            "fresh-token",
            "143",
            "auto",
        )
        self.assertIn("/render.key?auth=", rewritten)
        self.assertIn("/render.ts?auth=", rewritten)

        key_url = next(
            item.removeprefix('URI="').removesuffix('"')
            for item in rewritten.replace(",", "\n").splitlines()
            if item.startswith('URI="/render.key')
        )
        segment_url = next(line for line in rewritten.splitlines() if line.startswith("/render.ts"))
        init_url = next(
            item.split('URI="', 1)[1].removesuffix('"')
            for item in rewritten.replace(",", "\n").splitlines()
            if 'URI="/render.ts' in item
        )

        self.assertEqual(
            secure_url.decrypt_url(parse_qs(urlparse(key_url).query)["auth"][0]),
            "https://cdn.example.com/live/keys/live.key?foo=bar&__hdnea__=fresh-token",
        )
        self.assertEqual(
            secure_url.decrypt_url(parse_qs(urlparse(init_url).query)["auth"][0]),
            "https://cdn.example.com/live/init.mp4?foo=bar&__hdnea__=fresh-token",
        )
        self.assertEqual(
            secure_url.decrypt_url(parse_qs(urlparse(segment_url).query)["auth"][0]),
            "https://cdn.example.com/live/video/seg-1.m4s?range=1&foo=bar&__hdnea__=fresh-token",
        )

    def test_manifest_reference_base_handles_protocol_relative_urls(self) -> None:
        self.assertEqual(
            _manifest_reference_base_url("https://cdn.example.com/live/", "//media.example.com/a.m4s"),
            "https:",
        )
        self.assertEqual(
            _manifest_reference_base_url("https://cdn.example.com/live/", "https://media.example.com/a.m4s"),
            "",
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


class SegmentCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_ttl = cfg.segment_cache_ttl
        self.previous_max = cfg.segment_cache_max_bytes
        self.previous_item_max = cfg.segment_cache_item_max_bytes
        cfg.segment_cache_ttl = 20
        cfg.segment_cache_max_bytes = 8
        cfg.segment_cache_item_max_bytes = 8
        state.segment_cache.clear()
        state.segment_cache_bytes = 0

    def tearDown(self) -> None:
        cfg.segment_cache_ttl = self.previous_ttl
        cfg.segment_cache_max_bytes = self.previous_max
        cfg.segment_cache_item_max_bytes = self.previous_item_max
        state.segment_cache.clear()
        state.segment_cache_bytes = 0

    def test_segment_cache_strips_hdnea_from_key_and_evicts_oldest(self) -> None:
        first_key = _segment_cache_key("https://cdn.example.com/a.ts?hdnea=old&x=1", "fresh")
        same_key = _segment_cache_key("https://cdn.example.com/a.ts?hdnea=new&x=1", "fresh")
        self.assertEqual(first_key, same_key)

        _set_segment_cache(
            first_key,
            CachedResponse(
                body=b"1234",
                status=HTTPStatus.OK,
                content_type="video/mp2t",
                headers={},
                expires_at=time.time() + 20,
                size=4,
            ),
        )
        _set_segment_cache(
            "second",
            CachedResponse(
                body=b"5678",
                status=HTTPStatus.OK,
                content_type="video/mp2t",
                headers={},
                expires_at=time.time() + 20,
                size=4,
            ),
        )
        _set_segment_cache(
            "third",
            CachedResponse(
                body=b"90",
                status=HTTPStatus.OK,
                content_type="video/mp2t",
                headers={},
                expires_at=time.time() + 20,
                size=2,
            ),
        )

        self.assertIsNone(_get_segment_cache(first_key))
        self.assertIsNotNone(_get_segment_cache("second"))
        self.assertIsNotNone(_get_segment_cache("third"))


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

    def test_current_jio_sony_ids_use_sony_routing(self) -> None:
        for channel_id in {"154", "289", "762", "823", "852", "1396"}:
            self.assertIn(channel_id, constants.SONY_LIST)

    def test_broken_sony_250_variant_is_removed_from_master_manifest(self) -> None:
        manifest = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=250000\n"
            "Sony_Max_SD_250.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=500000\n"
            "Sony_Max_SD_500.m3u8\n"
        )
        filtered, removed = _filter_broken_sony_variants(manifest, "289")
        self.assertEqual(removed, 1)
        self.assertNotIn("Sony_Max_SD_250.m3u8", filtered)
        self.assertIn("Sony_Max_SD_500.m3u8", filtered)
        self.assertEqual(filtered.count("#EXT-X-STREAM-INF"), 1)

    def test_broken_sony_250_filter_does_not_touch_non_sony_channels(self) -> None:
        manifest = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=250000\nSony_SAB_250.m3u8\n"
        filtered, removed = _filter_broken_sony_variants(manifest, "173")
        self.assertEqual(removed, 0)
        self.assertEqual(filtered, manifest)

    def test_auto_mode_uses_hls_for_regular_channels_and_mpd_for_sony(self) -> None:
        handler = object.__new__(JioTVRequestHandler)
        handler._is_custom_channel = lambda _channel_id: False
        self.assertFalse(handler._should_use_mpd_player("143", "auto"))
        self.assertTrue(handler._should_use_mpd_player("143", "hd"))
        self.assertTrue(handler._should_use_mpd_player("289", "auto"))
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
        self.assertIn("lowLatencyMode: true", html)
        self.assertIn("maxBufferLength: 15", html)
        self.assertIn("liveSyncDurationCount: 2", html)
        self.assertIn("liveMaxLatencyDurationCount: 4", html)
        self.assertIn("fragLoadingMaxRetry: 6", html)

    def test_hls_player_preloads_live_manifest(self) -> None:
        html = templates.render_hls_player("/live/155.m3u8")
        self.assertIn('rel="preload"', html)
        self.assertIn('href="/live/155.m3u8"', html)

    def test_hls_player_applies_smooth_start_to_android_special_channels(self) -> None:
        html = templates.render_hls_player("/live/155.m3u8", channel_id="155")
        self.assertIn("const smoothStartSeconds = 14.5", html)
        self.assertIn("target = Math.max(start, end - smoothStartSeconds)", html)
        normal = templates.render_hls_player("/live/143.m3u8", channel_id="143")
        self.assertIn("const smoothStartSeconds = 0", normal)

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
        self.assertIn("bufferingGoal: 15", html)
        self.assertIn("rebufferingGoal: 2", html)
        self.assertIn("lowLatencyMode: true", html)

    def test_index_can_render_channel_fetch_error(self) -> None:
        html = templates.render_index(
            "Test",
            [],
            True,
            error="Could not fetch channels",
        )
        self.assertIn("No channels found", html)
        self.assertIn("Could not fetch channels", html)


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
