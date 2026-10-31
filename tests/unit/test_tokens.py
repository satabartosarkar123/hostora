from __future__ import annotations

import pytest

from hostora.errors import ProblemError
from hostora.realtime.broadcaster import Broadcaster
from hostora.security.tokens import (
    make_gate_token,
    make_link_token,
    sign_cron,
    verify_cron_signature,
    verify_gate_token,
    verify_link_token,
)


async def test_broadcaster_pub_sub_and_full_queue() -> None:
    bcast = Broadcaster()
    async with bcast.subscribe("EVNT1") as q:
        for i in range(20):
            await bcast.publish("EVNT1", f"msg-{i}")
        assert q.qsize() == 16
        first = q.get_nowait()
        assert first == "msg-4"


def test_link_token_roundtrip() -> None:
    secret = "hmac-secret"
    tok = make_link_token(secret, "EVNT1", "HHLD1")
    assert verify_link_token(secret, "EVNT1", tok) == "HHLD1"
    assert verify_link_token(secret, "EVNT2", tok) is None


def test_gate_token_roundtrip_and_expiry() -> None:
    secret = "gate-secret"
    tok = make_gate_token(secret, "EVNT1", "fn_1234", exp=2000)
    assert verify_gate_token(secret, "EVNT1", tok, now_ts=1500) == "fn_1234"

    with pytest.raises(ProblemError) as exc_exp:
        verify_gate_token(secret, "EVNT1", tok, now_ts=2001)
    assert exc_exp.value.status == 401

    with pytest.raises(ProblemError):
        verify_gate_token(secret, "EVNT2", tok, now_ts=1500)

    with pytest.raises(ProblemError):
        verify_gate_token(secret, "EVNT1", "bad-token", now_ts=1500)


def test_cron_signature_verification() -> None:
    secret = "cron-secret"
    sig = sign_cron(secret, 1000, "retention")
    verify_cron_signature(secret, "1000", sig, "retention", now_ts=1100)

    with pytest.raises(ProblemError) as exc_old:
        verify_cron_signature(secret, "1000", sig, "retention", now_ts=1301)
    assert exc_old.value.status == 401

    with pytest.raises(ProblemError) as exc_bad:
        verify_cron_signature(secret, "1000", "deadbeef", "retention", now_ts=1000)
    assert exc_bad.value.status == 401

    with pytest.raises(ProblemError) as exc_missing:
        verify_cron_signature(secret, None, sig, "retention", now_ts=1000)
    assert exc_missing.value.status == 401
