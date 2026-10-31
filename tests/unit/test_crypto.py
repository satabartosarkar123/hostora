from __future__ import annotations

import base64
import secrets

import pytest

from hostora.security.crypto import (
    decrypt_phone,
    encrypt_phone,
    ip_hash,
    mask_phone,
    normalize_phone,
    phone_hash,
)


def test_normalize_and_mask_phone() -> None:
    assert normalize_phone("98765 43210") == "919876543210"
    assert normalize_phone("+91-98765-43210") == "919876543210"
    assert mask_phone("9876543210") == "+91•••••10"
    assert mask_phone("12") == "+•••••"


def test_encrypt_decrypt_phone_roundtrip() -> None:
    key_b64 = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    ct1 = encrypt_phone("9876543210", key_b64)
    ct2 = encrypt_phone("+91 98765 43210", key_b64)
    assert ct1 != ct2  # random nonce
    assert decrypt_phone(ct1, key_b64) == "919876543210"
    assert decrypt_phone(ct2, key_b64) == "919876543210"

    bad_key = base64.b64encode(b"short").decode("ascii")
    with pytest.raises(ValueError, match="32 bytes"):
        encrypt_phone("9876543210", bad_key)
    with pytest.raises(ValueError, match="32 bytes"):
        decrypt_phone(ct1, bad_key)
    with pytest.raises(ValueError, match="Invalid encrypted phone"):
        decrypt_phone("invalid", key_b64)


def test_phone_and_ip_hashes() -> None:
    secret = "test-secret"
    h1 = phone_hash("9876543210", secret)
    h2 = phone_hash("+91 98765 43210", secret)
    assert h1 == h2
    assert len(h1) == 64

    ip1 = ip_hash("203.0.113.10", secret)
    ip2 = ip_hash("203.0.113.11", secret)
    assert ip1 != ip2
    assert len(ip1) == 16
