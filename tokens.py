"""Token expiry detection and refresh routines."""

from __future__ import annotations

import base64
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import constants
from .http_client import json_request, request
from .models import JioTVCredentials
from .utils import get_credentials, get_device_id, log, write_credentials


JWT_TOKEN_REFRESH_LEAD_TIME = timedelta(seconds=30)
ACCESS_TOKEN_FALLBACK_TTL = timedelta(hours=2)
ACCESS_TOKEN_FALLBACK_LEAD_TIME = timedelta(minutes=10)
SSO_TOKEN_FALLBACK_TTL = timedelta(hours=24)
SSO_TOKEN_FALLBACK_LEAD_TIME = timedelta(hours=1)
MIN_CREDENTIAL_VALIDATION_INTERVAL = timedelta(seconds=5)
CREDENTIAL_REFRESH_RETRY_BACKOFF = timedelta(seconds=20)

_refresh_lock = threading.RLock()
_next_validation_time: datetime | None = None


def parse_jwt_expiry(token: str) -> tuple[datetime | None, bool]:
    parts = token.split(".")
    if len(parts) != 3:
        return None, False
    try:
        payload = _decode_jwt_component(parts[1])
        claims = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None, False
    exp_raw = claims.get("exp")
    exp_unix = normalize_unix_timestamp(exp_raw)
    if exp_unix is None or exp_unix <= 0:
        return None, False
    return datetime.fromtimestamp(exp_unix, timezone.utc), True


def _decode_jwt_component(component: str) -> bytes:
    padding = "=" * (-len(component) % 4)
    return base64.urlsafe_b64decode(component + padding)


def normalize_unix_timestamp(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def fallback_token_refresh_threshold(
    last_refresh: str,
    fallback_ttl: timedelta,
    fallback_lead: timedelta,
) -> tuple[datetime | None, bool]:
    if not last_refresh:
        return None, False
    try:
        last_refresh_unix = int(last_refresh)
    except ValueError:
        return None, False
    threshold = datetime.fromtimestamp(last_refresh_unix, timezone.utc)
    return threshold + fallback_ttl - fallback_lead, True


def should_refresh_token(
    token: str,
    last_refresh: str,
    jwt_lead: timedelta,
    fallback_ttl: timedelta,
    fallback_lead: timedelta,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    if not token:
        return True
    expiry, ok = parse_jwt_expiry(token)
    if ok and expiry is not None:
        return expiry <= now + jwt_lead
    threshold, ok = fallback_token_refresh_threshold(
        last_refresh,
        fallback_ttl,
        fallback_lead,
    )
    if not ok or threshold is None:
        return True
    return threshold <= now


def next_token_validation_check(
    token: str,
    last_refresh: str,
    jwt_lead: timedelta,
    fallback_ttl: timedelta,
    fallback_lead: timedelta,
    now: datetime | None = None,
) -> tuple[datetime | None, bool]:
    now = now or datetime.now(timezone.utc)
    min_check_time = now + MIN_CREDENTIAL_VALIDATION_INTERVAL
    if token:
        expiry, ok = parse_jwt_expiry(token)
        if ok and expiry is not None:
            next_check = max(expiry - jwt_lead, min_check_time)
            return next_check, True
    threshold, ok = fallback_token_refresh_threshold(
        last_refresh,
        fallback_ttl,
        fallback_lead,
    )
    if not ok or threshold is None:
        return None, False
    return max(threshold, min_check_time), True


def is_access_token_expired(credentials: JioTVCredentials | None) -> bool:
    if credentials is None or not credentials.access_token:
        return True
    return should_refresh_token(
        credentials.access_token,
        credentials.last_token_refresh_time,
        JWT_TOKEN_REFRESH_LEAD_TIME,
        ACCESS_TOKEN_FALLBACK_TTL,
        ACCESS_TOKEN_FALLBACK_LEAD_TIME,
    )


def is_sso_token_expired(credentials: JioTVCredentials | None) -> bool:
    if credentials is None or not credentials.sso_token:
        return True
    return should_refresh_token(
        credentials.sso_token,
        credentials.last_sso_token_refresh_time,
        JWT_TOKEN_REFRESH_LEAD_TIME,
        SSO_TOKEN_FALLBACK_TTL,
        SSO_TOKEN_FALLBACK_LEAD_TIME,
    )


def ensure_fresh_tokens() -> bool:
    with _refresh_lock:
        credentials = get_credentials()
        if credentials is None:
            log.info("token check skipped: no credentials")
            raise RuntimeError("failed to get credentials")

        refreshed = False
        if (
            credentials.access_token
            and credentials.refresh_token
            and is_access_token_expired(credentials)
        ):
            log.info("AccessToken expired or near expiry; refreshing")
            refresh_access_token()
            refreshed = True

        credentials = get_credentials() or credentials
        if (
            credentials.sso_token
            and credentials.unique_id
            and is_sso_token_expired(credentials)
        ):
            log.info("SSOToken expired or near expiry; refreshing")
            refresh_sso_token()
            refreshed = True
        if not refreshed:
            log.debug("token check ok: no refresh needed")
        return refreshed


def ensure_fresh_credentials() -> bool:
    global _next_validation_time
    with _refresh_lock:
        now = datetime.now(timezone.utc)
        if _next_validation_time and now < _next_validation_time:
            return True
        credentials = get_credentials()
        if credentials is None:
            log.info("credential freshness check skipped: no credentials")
            _next_validation_time = now + CREDENTIAL_REFRESH_RETRY_BACKOFF
            return True

        refresh_access = (
            credentials.access_token
            and credentials.refresh_token
            and is_access_token_expired(credentials)
        )
        refresh_sso = (
            credentials.sso_token
            and credentials.unique_id
            and is_sso_token_expired(credentials)
        )
        if not refresh_access and not refresh_sso:
            _next_validation_time = calculate_next_credential_validation_time(
                credentials,
                now,
            )
            log.debug("credential freshness ok; next check at %s", _next_validation_time)
            return True
        return perform_token_refresh(refresh_access, refresh_sso, now)


def force_refresh_credentials() -> bool:
    global _next_validation_time
    with _refresh_lock:
        now = datetime.now(timezone.utc)
        credentials = get_credentials()
        if credentials is None:
            log.info("forced credential refresh failed: no credentials")
            _next_validation_time = now + CREDENTIAL_REFRESH_RETRY_BACKOFF
            return False
        refresh_access = bool(credentials.access_token and credentials.refresh_token)
        refresh_sso = bool(credentials.sso_token and credentials.unique_id)
        if not refresh_access and not refresh_sso:
            _next_validation_time = now + CREDENTIAL_REFRESH_RETRY_BACKOFF
            return False
        return perform_token_refresh(refresh_access, refresh_sso, now)


def perform_token_refresh(
    refresh_access: bool,
    refresh_sso: bool,
    now: datetime,
) -> bool:
    global _next_validation_time
    refreshed = False
    if refresh_access:
        try:
            log.info("refreshing AccessToken")
            refresh_access_token()
            refreshed = True
        except Exception as exc:  # noqa: BLE001
            log.warning("AccessToken refresh failed: %s", exc)
    if refresh_sso:
        try:
            log.info("refreshing SSOToken")
            refresh_sso_token()
            refreshed = True
        except Exception as exc:  # noqa: BLE001
            log.warning("SSOToken refresh failed: %s", exc)
    if refreshed:
        credentials = get_credentials()
        _next_validation_time = (
            calculate_next_credential_validation_time(credentials, now)
            if credentials
            else now + MIN_CREDENTIAL_VALIDATION_INTERVAL
        )
        return True
    _next_validation_time = now + CREDENTIAL_REFRESH_RETRY_BACKOFF
    log.warning("token refresh cycle failed; retry after %s", _next_validation_time)
    return False


def calculate_next_credential_validation_time(
    credentials: JioTVCredentials,
    now: datetime,
) -> datetime:
    next_checks = []
    access_check, ok = next_token_validation_check(
        credentials.access_token,
        credentials.last_token_refresh_time,
        JWT_TOKEN_REFRESH_LEAD_TIME,
        ACCESS_TOKEN_FALLBACK_TTL,
        ACCESS_TOKEN_FALLBACK_LEAD_TIME,
        now,
    )
    if ok and access_check:
        next_checks.append(access_check)
    sso_check, ok = next_token_validation_check(
        credentials.sso_token,
        credentials.last_sso_token_refresh_time,
        JWT_TOKEN_REFRESH_LEAD_TIME,
        SSO_TOKEN_FALLBACK_TTL,
        SSO_TOKEN_FALLBACK_LEAD_TIME,
        now,
    )
    if ok and sso_check:
        next_checks.append(sso_check)
    if not next_checks:
        return now + CREDENTIAL_REFRESH_RETRY_BACKOFF
    return min(next_checks)


def refresh_access_token() -> None:
    credentials = get_credentials()
    if credentials is None or not credentials.refresh_token:
        raise RuntimeError("RefreshToken is empty, cannot refresh AccessToken")

    payload = {
        "appName": "RJIL_JioTV",
        "deviceId": get_device_id(),
        "refreshToken": credentials.refresh_token,
    }
    headers = {
        constants.DEVICE_TYPE: constants.DEVICE_TYPE_PHONE,
        constants.VERSION_CODE: constants.VERSION_CODE_406,
        constants.OS: constants.OS_ANDROID,
        constants.CONTENT_TYPE: constants.CONTENT_TYPE_JSON_UTF8,
        constants.HOST: constants.AUTH_MEDIA_DOMAIN,
        constants.USER_AGENT: constants.USER_AGENT_OKHTTP,
        constants.ACCESS_TOKEN: credentials.access_token,
    }
    resp = json_request(constants.REFRESH_TOKEN_URL, "POST", payload, headers)
    if resp.status != 200:
        raise RuntimeError(f"AccessToken refresh failed with status {resp.status}")
    data = resp.json()
    access_token = data.get("authToken", "")
    if not access_token:
        raise RuntimeError("AccessToken not found in response")
    credentials.access_token = access_token
    credentials.last_token_refresh_time = str(int(time.time()))
    write_credentials(credentials)
    log.info("AccessToken refreshed successfully")


def refresh_sso_token() -> None:
    credentials = get_credentials()
    if credentials is None or not credentials.sso_token or not credentials.unique_id:
        raise RuntimeError("SSOToken or UniqueID is empty")
    device_id = get_device_id()
    headers = {
        constants.DEVICE_TYPE: constants.DEVICE_TYPE_PHONE,
        constants.VERSION_CODE: constants.VERSION_CODE_406,
        constants.OS: constants.OS_ANDROID,
        constants.HOST: constants.TV_MEDIA_DOMAIN,
        constants.USER_AGENT: constants.USER_AGENT_OKHTTP,
        "ssoToken": credentials.sso_token,
        "uniqueid": credentials.unique_id,
        "deviceid": device_id,
    }
    resp = request(constants.REFRESH_SSO_TOKEN_URL, headers=headers)
    if resp.status != 200:
        raise RuntimeError(f"SSOToken refresh failed with status {resp.status}")
    data = resp.json()
    sso_token = data.get("ssoToken", "")
    if not sso_token:
        raise RuntimeError("SSOToken not found in response")
    credentials.sso_token = sso_token
    credentials.last_sso_token_refresh_time = str(int(time.time()))
    write_credentials(credentials)
    log.info("SSOToken refreshed successfully")
