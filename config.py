"""Configuration loading for the Python application."""

from __future__ import annotations

import ast
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


COMMON_CONFIG_FILES = (
    "JIO_tv.yml",
    "JIO_tv.yaml",
    "JIO_tv.toml",
    "JIO_tv.json",
    "config.json",
    "config.yml",
    "config.toml",
    "config.yaml",
)


@dataclass(slots=True)
class JioTVConfig:
    epg: bool = False
    debug: bool = False
    disable_ts_handler: bool = False
    disable_logout: bool = False
    drm: bool = True
    title: str = ""
    disable_url_encryption: bool = False
    proxy: str = ""
    path_prefix: str = ""
    log_path: str = ""
    log_to_stdout: bool = False
    custom_channels_file: str = ""
    default_categories: list[int] = field(default_factory=list)
    default_languages: list[int] = field(default_factory=list)
    asgi_worker_threads: int = 256
    asgi_request_queue_limit: int = 5000
    asgi_queue_timeout_seconds: int = 5
    asgi_response_queue_size: int = 64
    channels_cache_ttl: int = 300
    manifest_cache_ttl: int = 3
    segment_cache_ttl: int = 20
    segment_cache_max_bytes: int = 536870912
    segment_cache_item_max_bytes: int = 8388608

    def load(self, filename: str = "") -> None:
        path = Path(filename) if filename else common_file_exists()
        data = load_config_data(path) if path else load_env_data()
        self.apply(data)

    def apply(self, data: dict[str, Any]) -> None:
        for key in self.__dataclass_fields__:
            if key not in data:
                continue
            current = getattr(self, key)
            value = data[key]
            if isinstance(current, bool):
                setattr(self, key, parse_bool(value))
            elif isinstance(current, int):
                setattr(self, key, parse_int(value, current))
            elif isinstance(current, list):
                setattr(self, key, parse_int_list(value))
            else:
                setattr(self, key, "" if value is None else str(value))

    def get(self, key: str) -> Any:
        snake_key = camel_to_snake(key)
        return getattr(self, snake_key, None)


cfg = JioTVConfig()


def common_file_exists() -> Path | None:
    for filename in COMMON_CONFIG_FILES:
        path = Path(filename)
        if path.exists():
            return path
    return None


def load_config_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8")
    if suffix == ".json":
        return json.loads(raw)
    if suffix == ".toml":
        return tomllib.loads(raw)
    if suffix in {".yml", ".yaml"}:
        return parse_simple_yaml(raw)
    raise ValueError(f"unsupported config format: {path}")


def load_env_data() -> dict[str, Any]:
    mapping = {
        "epg": "JIOTV_EPG",
        "debug": "JIOTV_DEBUG",
        "disable_ts_handler": "JIOTV_DISABLE_TS_HANDLER",
        "disable_logout": "JIOTV_DISABLE_LOGOUT",
        "drm": "JIOTV_DRM",
        "title": "JIOTV_TITLE",
        "disable_url_encryption": "JIOTV_DISABLE_URL_ENCRYPTION",
        "proxy": "JIOTV_PROXY",
        "path_prefix": "JIOTV_PATH_PREFIX",
        "log_path": "JIOTV_LOG_PATH",
        "log_to_stdout": "JIOTV_LOG_TO_STDOUT",
        "custom_channels_file": "JIOTV_CUSTOM_CHANNELS_FILE",
        "default_categories": "JIOTV_DEFAULT_CATEGORIES",
        "default_languages": "JIOTV_DEFAULT_LANGUAGES",
        "asgi_worker_threads": "JIOTV_ASGI_WORKER_THREADS",
        "asgi_request_queue_limit": "JIOTV_ASGI_REQUEST_QUEUE_LIMIT",
        "asgi_queue_timeout_seconds": "JIOTV_ASGI_QUEUE_TIMEOUT_SECONDS",
        "asgi_response_queue_size": "JIOTV_ASGI_RESPONSE_QUEUE_SIZE",
        "channels_cache_ttl": "JIOTV_CHANNELS_CACHE_TTL",
        "manifest_cache_ttl": "JIOTV_MANIFEST_CACHE_TTL",
        "segment_cache_ttl": "JIOTV_SEGMENT_CACHE_TTL",
        "segment_cache_max_bytes": "JIOTV_SEGMENT_CACHE_MAX_BYTES",
        "segment_cache_item_max_bytes": "JIOTV_SEGMENT_CACHE_ITEM_MAX_BYTES",
    }
    return {
        key: os.environ[env_key]
        for key, env_key in mapping.items()
        if env_key in os.environ
    }


def parse_simple_yaml(raw: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_key: str | None = None
    current_items: list[dict[str, Any]] | None = None
    current_item: dict[str, Any] | None = None

    for line in raw.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        if stripped.startswith("  - ") and current_items is not None:
            current_item = {}
            current_items.append(current_item)
            key, value = split_yaml_pair(stripped[4:])
            current_item[key] = parse_scalar(value)
            continue
        if stripped.startswith("    ") and current_item is not None:
            key, value = split_yaml_pair(stripped.strip())
            current_item[key] = parse_scalar(value)
            continue
        if ":" not in stripped:
            continue

        key, value = split_yaml_pair(stripped.strip())
        if value == "":
            current_key = key
            current_items = []
            data[current_key] = current_items
            current_item = None
        else:
            data[key] = parse_scalar(value)
            current_key = None
            current_items = None
            current_item = None
    return data


def split_yaml_pair(value: str) -> tuple[str, str]:
    key, _, raw_value = value.partition(":")
    return key.strip(), raw_value.strip()


def parse_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text == "":
        return ""
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if text.startswith("[") and text.endswith("]"):
        try:
            return ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return text
    if (
        (text.startswith('"') and text.endswith('"'))
        or (text.startswith("'") and text.endswith("'"))
    ):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        return text


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_int_list(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parsed = parse_scalar(value)
        if isinstance(parsed, str):
            parsed = [item.strip() for item in parsed.split(",") if item.strip()]
    else:
        parsed = value
    return [int(item) for item in parsed]


def camel_to_snake(value: str) -> str:
    out = []
    for char in value:
        if char.isupper() and out:
            out.append("_")
        out.append(char.lower())
    return "".join(out)
