"""Small urllib-based HTTP client used by the converted server."""

from __future__ import annotations

import gzip
import http.client
import json
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message
from typing import Any, BinaryIO, Iterator
from urllib.parse import unquote, urlparse, urlunparse
from urllib.error import HTTPError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

from .config import cfg
from .constants import CONTENT_TYPE, CONTENT_TYPE_JSON, USER_AGENT, USER_AGENT_OKHTTP
from .diagnostics import log, redact_url


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    proxy_url: str = ""

    @property
    def is_socks(self) -> bool:
        return self.scheme in SOCKS_PROXY_SCHEMES

    @property
    def remote_dns(self) -> bool:
        return self.scheme in {"socks4a", "socks5h"}


@dataclass(slots=True)
class HTTPResponse:
    status: int
    headers: Message
    body: bytes
    url: str

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


@dataclass(slots=True)
class StreamingHTTPResponse:
    status: int
    headers: Message
    stream: BinaryIO
    url: str


HTTP_PROXY_SCHEMES = {"http", "https"}
SOCKS_PROXY_SCHEMES = {"socks4", "socks4a", "socks5", "socks5h"}
SOCKS_PROXY_ALIASES = {
    "socks": "socks5",
    "sock5": "socks5",
    "sock5h": "socks5h",
}


def parse_proxy_url(proxy: str) -> ProxyConfig | None:
    proxy = proxy.strip()
    if not proxy:
        return None

    candidate = proxy if "://" in proxy else f"http://{proxy}"
    parsed = urlparse(candidate)
    scheme = SOCKS_PROXY_ALIASES.get(parsed.scheme.lower(), parsed.scheme.lower())
    if scheme not in HTTP_PROXY_SCHEMES | SOCKS_PROXY_SCHEMES:
        supported = ", ".join(
            sorted(HTTP_PROXY_SCHEMES | SOCKS_PROXY_SCHEMES | set(SOCKS_PROXY_ALIASES))
        )
        raise ValueError(
            f"unsupported proxy scheme {parsed.scheme!r}; supported schemes: {supported}"
        )
    if not parsed.hostname:
        raise ValueError("proxy URL must include a host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("proxy URL has an invalid port") from exc
    if port is None:
        raise ValueError("proxy URL must include a port")

    proxy_url = candidate
    if parsed.scheme.lower() != scheme:
        proxy_url = urlunparse(parsed._replace(scheme=scheme))

    return ProxyConfig(
        scheme=scheme,
        host=parsed.hostname,
        port=port,
        username=unquote(parsed.username) if parsed.username is not None else None,
        password=unquote(parsed.password) if parsed.password is not None else None,
        proxy_url=proxy_url,
    )


def validate_proxy_config(proxy: str) -> ProxyConfig | None:
    proxy_config = parse_proxy_url(proxy)
    if proxy_config and proxy_config.is_socks:
        _load_socks_module()
    return proxy_config


def describe_proxy(proxy: str) -> str:
    proxy_config = parse_proxy_url(proxy)
    if proxy_config is None:
        return ""
    auth = " credentials=yes" if proxy_config.username else ""
    dns = " remote_dns=yes" if proxy_config.remote_dns else ""
    return f"{proxy_config.scheme}://{proxy_config.host}:{proxy_config.port}{auth}{dns}"


def _load_socks_module() -> Any:
    try:
        import socks  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "SOCKS proxy support requires PySocks. Install dependencies with "
            "`python3 -m pip install -r jiotv_py/requirements-python.txt` "
            "or install `PySocks` directly."
        ) from exc
    return socks


class SocksConnectionMixin:
    def __init__(
        self,
        host: str,
        port: int | None = None,
        *,
        proxy_config: ProxyConfig,
        **kwargs: Any,
    ) -> None:
        self._proxy_config = proxy_config
        super().__init__(host, port=port, **kwargs)
        self._create_connection = self._create_socks_connection

    def _create_socks_connection(
        self,
        address: tuple[str, int],
        timeout: float | object = socket._GLOBAL_DEFAULT_TIMEOUT,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        socks = _load_socks_module()
        proxy_type = {
            "socks4": socks.SOCKS4,
            "socks4a": socks.SOCKS4,
            "socks5": socks.SOCKS5,
            "socks5h": socks.SOCKS5,
        }[self._proxy_config.scheme]
        sock = socks.socksocket()
        sock.set_proxy(
            proxy_type=proxy_type,
            addr=self._proxy_config.host,
            port=self._proxy_config.port,
            rdns=self._proxy_config.remote_dns,
            username=self._proxy_config.username,
            password=self._proxy_config.password,
        )
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        try:
            sock.connect(address)
        except Exception:
            sock.close()
            raise
        return sock


class SocksHTTPConnection(SocksConnectionMixin, http.client.HTTPConnection):
    pass


class SocksHTTPSConnection(SocksConnectionMixin, http.client.HTTPSConnection):
    pass


class SocksHTTPHandler(HTTPHandler):
    def __init__(self, proxy_config: ProxyConfig) -> None:
        super().__init__()
        self._proxy_config = proxy_config

    def http_open(self, req):  # noqa: ANN001
        return self.do_open(self._connection, req)

    def _connection(self, host: str, **kwargs: Any) -> SocksHTTPConnection:
        return SocksHTTPConnection(host, proxy_config=self._proxy_config, **kwargs)


class SocksHTTPSHandler(HTTPSHandler):
    def __init__(self, proxy_config: ProxyConfig) -> None:
        super().__init__()
        self._proxy_config = proxy_config

    def https_open(self, req):  # noqa: ANN001
        return self.do_open(
            self._connection,
            req,
            context=self._context,
        )

    def _connection(self, host: str, **kwargs: Any) -> SocksHTTPSConnection:
        return SocksHTTPSConnection(host, proxy_config=self._proxy_config, **kwargs)


def make_opener(follow_redirects: bool = True) -> OpenerDirector:
    handlers = []
    if cfg.proxy:
        proxy_config = validate_proxy_config(cfg.proxy)
        if proxy_config is not None:
            if proxy_config.is_socks:
                handlers.extend(
                    [
                        SocksHTTPHandler(proxy_config),
                        SocksHTTPSHandler(proxy_config),
                        ProxyHandler({}),
                    ]
                )
            else:
                handlers.append(HTTPSHandler())
                handlers.append(
                    ProxyHandler(
                        {
                            "http": proxy_config.proxy_url,
                            "https": proxy_config.proxy_url,
                        }
                    )
                )
    else:
        handlers.append(HTTPSHandler())
    if not follow_redirects:
        handlers.append(NoRedirectHandler())
    return build_opener(*handlers)


def request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 30,
    follow_redirects: bool = True,
) -> HTTPResponse:
    request_headers = {USER_AGENT: USER_AGENT_OKHTTP}
    if headers:
        request_headers.update(headers)
    req = Request(url, data=body, headers=request_headers, method=method.upper())
    opener = make_opener(follow_redirects=follow_redirects)
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw_body = resp.read()
            headers_obj = resp.headers
            status = resp.status
            final_url = resp.url
    except HTTPError as exc:
        raw_body = exc.read()
        headers_obj = exc.headers
        status = exc.code
        final_url = exc.url
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        log.exception(
            "upstream %s %s failed after %.1fms",
            method.upper(),
            redact_url(url),
            elapsed_ms,
        )
        raise

    if "gzip" in headers_obj.get("Content-Encoding", "").lower():
        raw_body = gzip.decompress(raw_body)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if status >= 400:
        log.warning(
            "upstream %s %s -> %s %.1fms bytes=%s",
            method.upper(),
            redact_url(url),
            status,
            elapsed_ms,
            len(raw_body),
        )
    else:
        log.debug(
            "upstream %s %s -> %s %.1fms bytes=%s",
            method.upper(),
            redact_url(url),
            status,
            elapsed_ms,
            len(raw_body),
        )
    return HTTPResponse(status=status, headers=headers_obj, body=raw_body, url=final_url)


@contextmanager
def stream_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 30,
    follow_redirects: bool = True,
) -> Iterator[StreamingHTTPResponse]:
    request_headers = {USER_AGENT: USER_AGENT_OKHTTP}
    if headers:
        request_headers.update(headers)
    req = Request(url, data=body, headers=request_headers, method=method.upper())
    opener = make_opener(follow_redirects=follow_redirects)
    started = time.perf_counter()
    stream: BinaryIO | None = None
    status = 0
    try:
        stream = opener.open(req, timeout=timeout)
        status = stream.status
        yield StreamingHTTPResponse(
            status=status,
            headers=stream.headers,
            stream=stream,
            url=stream.url,
        )
    except HTTPError as exc:
        status = exc.code
        stream = exc
        yield StreamingHTTPResponse(
            status=status,
            headers=exc.headers,
            stream=exc,
            url=exc.url,
        )
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        log.exception(
            "upstream %s %s failed after %.1fms",
            method.upper(),
            redact_url(url),
            elapsed_ms,
        )
        raise
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if stream is not None:
            stream.close()
        if status >= 400:
            log.warning(
                "upstream %s %s -> %s %.1fms streamed",
                method.upper(),
                redact_url(url),
                status,
                elapsed_ms,
            )
        elif status:
            log.debug(
                "upstream %s %s -> %s %.1fms streamed",
                method.upper(),
                redact_url(url),
                status,
                elapsed_ms,
            )


def json_request(
    url: str,
    method: str,
    payload: Any,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> HTTPResponse:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {CONTENT_TYPE: CONTENT_TYPE_JSON}
    if headers:
        request_headers.update(headers)
    return request(url, method=method, headers=request_headers, body=body, timeout=timeout)


def form_request(
    url: str,
    payload: str,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> HTTPResponse:
    request_headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if headers:
        request_headers.update(headers)
    return request(
        url,
        method="POST",
        headers=request_headers,
        body=payload.encode(),
        timeout=timeout,
    )
