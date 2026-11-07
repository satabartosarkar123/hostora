from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_DIGITS_RE = re.compile(r"\D+")
_AAD = b"hostora.phone.v1"
_STORE_AAD = b"hostora.store_token.v1"


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def normalize_phone(phone: str) -> str:
    digits = _DIGITS_RE.sub("", phone)
    if len(digits) == 10:
        return f"91{digits}"
    return digits


def encrypt_phone(phone: str, key_b64: str) -> str:
    key = base64.b64decode(key_b64)
    if len(key) != 32:
        raise ValueError("PHONE_ENC_KEY must decode to 32 bytes")
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, normalize_phone(phone).encode("utf-8"), _AAD)
    return f"{_b64u_encode(nonce)}.{_b64u_encode(ct)}"


def decrypt_phone(value: str, key_b64: str) -> str:
    key = base64.b64decode(key_b64)
    if len(key) != 32:
        raise ValueError("PHONE_ENC_KEY must decode to 32 bytes")
    parts = value.split(".")
    if len(parts) != 2:
        raise ValueError("Invalid encrypted phone format")
    nonce = _b64u_decode(parts[0])
    ct = _b64u_decode(parts[1])
    pt = AESGCM(key).decrypt(nonce, ct, _AAD)
    return pt.decode("utf-8")


def mask_phone(phone_digits: str) -> str:
    norm = normalize_phone(phone_digits)
    if len(norm) >= 4:
        prefix = f"+{norm[:2]}" if norm.startswith("91") and len(norm) == 12 else "+"
        return f"{prefix}•••••{norm[-2:]}"
    return "+•••••"


def phone_hash(phone: str, secret: str) -> str:
    norm = normalize_phone(phone)
    return hmac.new(secret.encode("utf-8"), norm.encode("utf-8"), hashlib.sha256).hexdigest()


def ip_hash(ip: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), ip.strip().encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:16]


def encrypt_store_token(token_payload: str, key_b64: str) -> str:
    key = base64.b64decode(key_b64)
    if len(key) != 32:
        raise ValueError("STORE_TOKEN_KEY must decode to 32 bytes")
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, token_payload.encode("utf-8"), _STORE_AAD)
    return f"{_b64u_encode(nonce)}.{_b64u_encode(ct)}"


def decrypt_store_token(value: str, key_b64: str) -> str:
    key = base64.b64decode(key_b64)
    if len(key) != 32:
        raise ValueError("STORE_TOKEN_KEY must decode to 32 bytes")
    parts = value.split(".")
    if len(parts) != 2:
        raise ValueError("Invalid encrypted store token format")
    nonce = _b64u_decode(parts[0])
    ct = _b64u_decode(parts[1])
    pt = AESGCM(key).decrypt(nonce, ct, _STORE_AAD)
    return pt.decode("utf-8")
