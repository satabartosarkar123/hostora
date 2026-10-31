from __future__ import annotations

import base64
import hashlib
import hmac

from hostora.engine.codes import make_link_token, verify_link_token
from hostora.errors import ProblemError

__all__ = [
    "make_gate_token",
    "make_link_token",
    "sign_cron",
    "verify_cron_signature",
    "verify_gate_token",
    "verify_link_token",
]


def _gate_mac(secret: str, event_key: str, function_id: str, exp: int) -> str:
    msg = f"gate.{event_key}.{function_id}.{exp}".encode("ascii")
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")[:22]


def make_gate_token(secret: str, event_key: str, function_id: str, exp: int) -> str:
    mac = _gate_mac(secret, event_key, function_id, exp)
    return f"{function_id}.{exp}.{mac}"


def verify_gate_token(secret: str, event_key: str, token: str, now_ts: int) -> str:
    parts = token.split(".")
    if len(parts) != 3:
        raise ProblemError(401, "Invalid gate token")
    function_id, exp_str, mac = parts
    if not function_id or not exp_str.isdigit():
        raise ProblemError(401, "Invalid gate token")
    exp = int(exp_str)
    expected = _gate_mac(secret, event_key, function_id, exp)
    if not hmac.compare_digest(mac, expected):
        raise ProblemError(401, "Invalid gate token")
    if exp < now_ts:
        raise ProblemError(401, "Expired gate token")
    return function_id


def sign_cron(secret: str, timestamp: int, job: str) -> str:
    msg = f"{timestamp}.{job}".encode("ascii")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_cron_signature(
    secret: str,
    timestamp_hdr: str | None,
    signature_hdr: str | None,
    job: str,
    now_ts: int,
) -> None:
    if not timestamp_hdr or not signature_hdr:
        raise ProblemError(401, "Missing cron signature")
    try:
        ts = int(timestamp_hdr)
    except ValueError as exc:
        raise ProblemError(401, "Invalid cron timestamp") from exc
    if abs(now_ts - ts) > 300:
        raise ProblemError(401, "Cron timestamp out of window")
    expected = sign_cron(secret, ts, job)
    if not hmac.compare_digest(signature_hdr.lower(), expected.lower()):
        raise ProblemError(401, "Invalid cron signature")
