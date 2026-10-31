from __future__ import annotations

from starlette.requests import Request

from hostora.security.ratelimit import client_ip


def _make_request(headers: list[tuple[bytes, bytes]]) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": ("10.0.0.5", 12345),
    }
    return Request(scope)


def test_client_ip_extracts_forwarded_for() -> None:
    req = _make_request([(b"x-forwarded-for", b"198.51.100.7, 10.0.0.1")])
    assert client_ip(req) == "198.51.100.7"


def test_client_ip_falls_back_to_socket_peer() -> None:
    req = _make_request([])
    assert client_ip(req) == "10.0.0.5"
