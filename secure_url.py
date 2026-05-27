"""AES-CTR URL encryption for stream proxy URLs."""

from __future__ import annotations

import base64
import binascii
import secrets
from urllib.parse import quote, unquote

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .config import cfg


_key: bytes = b""
_disable_url_encryption = False


def generate_key() -> bytes:
    return secrets.token_bytes(32)


def init() -> None:
    global _key, _disable_url_encryption
    _disable_url_encryption = cfg.disable_url_encryption
    if not _disable_url_encryption:
        _key = generate_key()


def encrypt_url(input_url: str) -> str:
    if _disable_url_encryption:
        return quote(input_url, safe="")
    if not _key:
        init()
    iv = secrets.token_bytes(16)
    cipher = Cipher(algorithms.AES(_key), modes.CTR(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(input_url.encode()) + encryptor.finalize()
    return base64.urlsafe_b64encode(iv + ciphertext).decode()


def decrypt_url(encrypted_url: str) -> str:
    if _disable_url_encryption:
        return unquote(encrypted_url)
    if not _key:
        init()
    try:
        raw = base64.urlsafe_b64decode(encrypted_url.encode())
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid encrypted URL encoding") from exc
    if len(raw) < 16:
        raise ValueError("ciphertext too short")
    iv, ciphertext = raw[:16], raw[16:]
    cipher = Cipher(algorithms.AES(_key), modes.CTR(iv))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(ciphertext) + decryptor.finalize()
    try:
        return decrypted.decode()
    except UnicodeDecodeError as exc:
        raise ValueError("encrypted URL could not be decrypted with current key") from exc
