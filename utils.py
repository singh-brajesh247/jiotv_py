"""Utility helpers for the Python application."""

from __future__ import annotations

import base64
import json
import logging
import secrets
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import store
from .config import cfg
from .constants import (
    ACCEPT_ENCODING,
    ACCEPT_ENCODING_GZIP,
    ACCESS_TOKEN,
    AUTH_MEDIA_DOMAIN,
    CONTENT_TYPE,
    CONTENT_TYPE_JSON_UTF8,
    DEVICE_TYPE,
    DEVICE_TYPE_PHONE,
    JIOTV_API_DOMAIN,
    OS,
    OS_ANDROID,
    VERSION_CODE_406,
)
from .diagnostics import body_preview, log, redact_url, token_preview
from .http_client import json_request, request
from .models import JioTVCredentials



def init_logger() -> logging.Logger:
    log.setLevel(logging.DEBUG if cfg.debug else logging.INFO)
    log.handlers.clear()
    log.propagate = False

    log_dir = Path(cfg.log_path).expanduser() if cfg.log_path else store.get_path_prefix()
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "JIO_tv.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "[%(levelname)s] %(asctime)s %(filename)s:%(lineno)d %(message)s"
        if cfg.debug
        else "[%(levelname)s] %(asctime)s %(message)s"
    )
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

    if cfg.log_to_stdout:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        log.addHandler(stream_handler)
    return log


def get_path_prefix() -> Path:
    return store.get_path_prefix()


def get_device_id() -> str:
    try:
        return store.get("deviceId")
    except store.KeyNotFoundError:
        device_id = secrets.token_hex(8)
        store.set_value("deviceId", device_id)
        return device_id


def get_credentials() -> JioTVCredentials | None:
    try:
        return JioTVCredentials(
            sso_token=store.get("ssoToken"),
            unique_id=store.get("uniqueId"),
            crm=store.get("crm"),
            access_token=optional_store_get("accessToken"),
            refresh_token=optional_store_get("refreshToken"),
            last_token_refresh_time=optional_store_get("lastTokenRefreshTime"),
            last_sso_token_refresh_time=optional_store_get("lastSSOTokenRefreshTime"),
        )
    except store.KeyNotFoundError:
        return None


def optional_store_get(key: str) -> str:
    try:
        return store.get(key)
    except store.KeyNotFoundError:
        return ""


def write_credentials(credentials: JioTVCredentials) -> None:
    now = str(int(time.time()))
    if not credentials.last_token_refresh_time:
        credentials.last_token_refresh_time = now
    if not credentials.last_sso_token_refresh_time:
        credentials.last_sso_token_refresh_time = now
    for key, value in credentials.to_store().items():
        store.set_value(key, value)


def check_logged_in() -> bool:
    return get_credentials() is not None


def logout() -> None:
    try:
        perform_server_logout()
    except Exception as exc:  # noqa: BLE001 - logout should clear local state.
        log.info("server-side logout failed: %s", exc)
    for key in (
        "ssoToken",
        "crm",
        "uniqueId",
        "accessToken",
        "refreshToken",
        "lastTokenRefreshTime",
        "lastSSOTokenRefreshTime",
    ):
        store.delete(key)


def perform_server_logout() -> None:
    credentials = get_credentials()
    if credentials is None or not credentials.refresh_token:
        raise RuntimeError("refreshToken is missing")

    payload = {
        "appName": "RJIL_JioTV",
        "deviceId": get_device_id(),
        "refreshToken": credentials.refresh_token,
    }
    headers = {
        ACCEPT_ENCODING: ACCEPT_ENCODING_GZIP,
        DEVICE_TYPE: DEVICE_TYPE_PHONE,
        "versioncode": VERSION_CODE_406,
        OS: OS_ANDROID,
        CONTENT_TYPE: CONTENT_TYPE_JSON_UTF8,
    }
    if credentials.access_token:
        headers[ACCESS_TOKEN] = credentials.access_token
    if credentials.unique_id:
        headers["uniqueid"] = credentials.unique_id

    resp = json_request(
        f"https://{AUTH_MEDIA_DOMAIN}/tokenservice/apis/v1/logout?langId=6",
        "POST",
        payload,
        headers,
    )
    if resp.status < 200 or resp.status >= 300:
        raise RuntimeError(f"server logout failed with status {resp.status}")


def login_send_otp(number: str) -> bool:
    encoded_number = base64.b64encode(number.encode()).decode()
    resp = json_request(
        f"https://{JIOTV_API_DOMAIN}/userservice/apis/v1/loginotp/send",
        "POST",
        {"number": encoded_number},
        {"appname": "RJIL_JioTV", OS: OS_ANDROID, DEVICE_TYPE: DEVICE_TYPE_PHONE},
    )
    if resp.status != 204:
        raise RuntimeError(f"request failed with status {resp.status}: {resp.body!r}")
    return True


def login_verify_otp(number: str, otp: str) -> dict[str, str]:
    payload = {
        "number": base64.b64encode(number.encode()).decode(),
        "otp": otp,
        "deviceInfo": {
            "consumptionDeviceName": "SM-G930F",
            "info": {
                "type": "android",
                "platform": {"name": "SM-G930F"},
                "androidId": get_device_id(),
            },
        },
    }
    resp = json_request(
        f"https://{JIOTV_API_DOMAIN}/userservice/apis/v1/loginotp/verify",
        "POST",
        payload,
        {"appname": "RJIL_JioTV", OS: OS_ANDROID, DEVICE_TYPE: DEVICE_TYPE_PHONE},
    )
    if resp.status != 200:
        raise RuntimeError(f"request failed with status {resp.status}")
    result = json.loads(resp.body.decode("utf-8"))
    access_token = result.get("authToken", "")
    if not access_token:
        return {"status": "failed", "message": "Invalid OTP"}

    user = result.get("sessionAttributes", {}).get("user", {})
    credentials = JioTVCredentials(
        sso_token=result.get("ssoToken", ""),
        crm=user.get("subscriberId", ""),
        unique_id=user.get("unique", ""),
        access_token=access_token,
        refresh_token=result.get("refreshToken", ""),
        last_token_refresh_time=str(int(time.time())),
        last_sso_token_refresh_time=str(int(time.time())),
    )
    write_credentials(credentials)
    return {
        "status": "success",
        "accessToken": credentials.access_token,
        "refreshToken": credentials.refresh_token,
        "ssoToken": credentials.sso_token,
        "crm": credentials.crm,
        "uniqueId": credentials.unique_id,
    }


def generate_current_time() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def generate_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def build_hls_play_url(quality: str, channel_id: str) -> str:
    if quality:
        return f"/live/{quality}/{channel_id}.m3u8"
    return f"/live/{channel_id}.m3u8"


def contains_string(item: str, values: list[str]) -> bool:
    return item in values


def read_json_response(url: str, headers: dict[str, str] | None = None) -> Any:
    resp = request(url, headers=headers)
    if resp.status != 200:
        raise RuntimeError(f"request failed with status {resp.status}: {resp.body!r}")
    return json.loads(resp.body.decode("utf-8"))


def check_and_read_file(path: str | Path) -> tuple[bool, bytes, Exception | None]:
    file_path = Path(path)
    if not file_path.exists():
        return False, b"", None
    try:
        return True, file_path.read_bytes(), None
    except OSError as exc:
        return True, b"", exc


def select_quality(quality: str, auto: str, high: str, medium: str, low: str) -> str:
    if quality in {"high", "h"}:
        return high
    if quality in {"medium", "med", "m"}:
        return medium
    if quality in {"low", "l"}:
        return low
    return auto


def json_dumps(data: Any) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode("utf-8")
