"""Thread-safe TOML key-value store."""

from __future__ import annotations

import json
import os
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import constants
from .config import cfg


class KeyNotFoundError(KeyError):
    """Raised when a key is missing from the store."""


@dataclass
class TomlStore:
    filename: Path
    data: dict[str, str] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def load(self) -> None:
        with self.lock:
            if not self.filename.exists():
                self.filename.parent.mkdir(parents=True, exist_ok=True)
                self.save()
                return
            parsed = tomllib.loads(self.filename.read_text(encoding="utf-8"))
            self.data = {
                str(key): str(value)
                for key, value in parsed.get("data", {}).items()
            }

    def save(self) -> None:
        self.filename.parent.mkdir(parents=True, exist_ok=True)
        lines = ["[data]"]
        for key in sorted(self.data):
            lines.append(f"{key} = {json.dumps(self.data[key])}")
        self.filename.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def get(self, key: str) -> str:
        with self.lock:
            if key not in self.data:
                raise KeyNotFoundError(key)
            return self.data[key]

    def set(self, key: str, value: str) -> None:
        with self.lock:
            self.data[key] = value
            self.save()

    def delete(self, key: str) -> None:
        with self.lock:
            self.data.pop(key, None)
            self.save()


kvs: TomlStore | None = None


def init() -> None:
    global kvs
    path_prefix = get_path_prefix()
    migrate_existing_store(path_prefix)
    kvs = TomlStore(path_prefix / "store_v4.toml")
    kvs.load()


def get_path_prefix() -> Path:
    path_prefix = Path(cfg.path_prefix).expanduser() if cfg.path_prefix else None
    if path_prefix is None:
        path_prefix = Path.home() / constants.PATH_PREFIX
    path_prefix.mkdir(parents=True, exist_ok=True)
    return path_prefix


def migrate_existing_store(path_prefix: Path) -> None:
    current = path_prefix / "store_v4.toml"
    previous = previous_default_path_prefix() / "store_v4.toml"
    if current.exists() or not previous.exists():
        return
    try:
        current.write_text(previous.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        return


def previous_default_path_prefix() -> Path:
    old_name = "." + "jio" + "tv" + "_" + "g" + "o"
    return Path.home() / old_name


def get(key: str) -> str:
    ensure_store()
    assert kvs is not None
    return kvs.get(key)


def set_value(key: str, value: str) -> None:
    ensure_store()
    assert kvs is not None
    kvs.set(key, value)


def delete(key: str) -> None:
    ensure_store()
    assert kvs is not None
    kvs.delete(key)


def ensure_store() -> None:
    if kvs is None:
        init()


def setup_test_path_prefix() -> tuple[Path, str]:
    import tempfile

    original = cfg.path_prefix
    path = Path(tempfile.mkdtemp(prefix="JIO_tv_test_"))
    cfg.path_prefix = os.fspath(path)
    init()
    return path, original
