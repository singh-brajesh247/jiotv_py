"""Small urllib-based HTTP client used by the converted server."""

from __future__ import annotations

import gzip
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message
from typing import Any, BinaryIO, Iterator
from urllib.error import HTTPError
from urllib.request import (
    HTTPRedirectHandler,
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


def make_opener(follow_redirects: bool = True) -> OpenerDirector:
    handlers = [HTTPSHandler()]
    if cfg.proxy:
        handlers.append(ProxyHandler({"http": cfg.proxy, "https": cfg.proxy}))
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
