"""Household codes and signed invite-link tokens.

Codes use the Crockford base32 alphabet (no I, L, O or U) so they survive being
read aloud or typed from a printed card.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_VALID = re.compile(r"^[0-9A-HJKMNP-TV-Z]+$")
_LINK_TAG_CHARS = 11  # 55 bits


def random_key(length: int = 5) -> str:
    """Random key of `length` Crockford base32 characters (5 bits each)."""
    return "".join(ALPHABET[b & 31] for b in secrets.token_bytes(length))


def normalize_code(value: str) -> str | None:
    """Upper-case, drop spaces and dashes, map look-alikes (O->0, I/L->1). None if invalid."""
    cleaned = re.sub(r"[\s-]", "", value.upper()).replace("O", "0")
    cleaned = re.sub(r"[IL]", "1", cleaned)
    if not cleaned or not _VALID.match(cleaned):
        return None
    return cleaned


def split_household_code(value: str) -> tuple[str, str] | None:
    """Typed code `XXXXX-XXXXX` -> (event_key, household_key), or None."""
    code = normalize_code(value)
    if code is None or len(code) != 10:
        return None
    return code[:5], code[5:]


def format_household_code(event_key: str, household_key: str) -> str:
    return f"{event_key}-{household_key}"


def _hmac_base32(secret: str, message: str, chars: int) -> str:
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return "".join(ALPHABET[digest[i] & 31] for i in range(chars))


def make_link_token(secret: str, event_key: str, household_key: str) -> str:
    """16-char token = household_key (5) + HMAC tag (11)."""
    return household_key + _hmac_base32(secret, f"{event_key}.{household_key}", _LINK_TAG_CHARS)


def verify_link_token(secret: str, event_key: str, token: str) -> str | None:
    """Household key if the token is valid for this event, else None. Constant-time compare."""
    if len(token) != 16:
        return None
    household_key = token[:5]
    expected = make_link_token(secret, event_key, household_key)
    return household_key if hmac.compare_digest(expected, token) else None
