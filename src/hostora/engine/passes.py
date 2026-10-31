"""Ed25519-signed gate passes, verifiable offline.

Format: v1.{event_key}.{pass_id}.{seats}.{exp}.{base64url signature}
The gate page verifies the same format in the browser with the vendored
noble-ed25519 module; the raw 32-byte private key here is the same seed format.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


@dataclass(frozen=True, slots=True)
class PassClaims:
    event_key: str
    pass_id: str
    seats: int
    exp: int  # unix seconds


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def pass_payload(c: PassClaims) -> str:
    return f"v1.{c.event_key}.{c.pass_id}.{c.seats}.{c.exp}"


def sign_pass(claims: PassClaims, private_key_hex: str) -> str:
    if "." in claims.event_key or "." in claims.pass_id:
        raise ValueError("event_key and pass_id must not contain dots")
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    payload = pass_payload(claims)
    return f"{payload}.{_b64u_encode(key.sign(payload.encode()))}"


def verify_pass(token: str, public_key_hex: str, now_sec: int) -> PassClaims | None:
    """Claims if the pass is well-formed, unexpired and genuinely signed; else None."""
    parts = token.split(".")
    if len(parts) != 6 or parts[0] != "v1":
        return None
    _, event_key, pass_id, seats_s, exp_s, sig_s = parts
    if not (seats_s.isdigit() and exp_s.isdigit()):
        return None
    seats, exp = int(seats_s), int(exp_s)
    if seats < 1 or exp < now_sec:
        return None
    claims = PassClaims(event_key=event_key, pass_id=pass_id, seats=seats, exp=exp)
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(_b64u_decode(sig_s), pass_payload(claims).encode())
    except (InvalidSignature, ValueError, binascii.Error):
        return None
    return claims


def generate_key_pair_hex() -> tuple[str, str]:
    """(private_key_hex, public_key_hex), raw 32-byte Ed25519 keys."""
    key = Ed25519PrivateKey.generate()
    private_hex = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
    public_hex = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private_hex, public_hex
