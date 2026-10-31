from __future__ import annotations

import hmac
import secrets
from typing import TYPE_CHECKING

from hostora.errors import ProblemError

if TYPE_CHECKING:
    from fastapi import Request


def ensure_csrf_token(request: Request) -> str:
    csrf = request.session.get("csrf")
    if not isinstance(csrf, str) or not csrf:
        csrf = secrets.token_urlsafe(24)
        request.session["csrf"] = csrf
    return csrf


async def verify_csrf(request: Request, form_csrf: str | None = None) -> None:
    expected = request.session.get("csrf")
    if not isinstance(expected, str) or not expected:
        raise ProblemError(403, "CSRF token missing from session")

    provided = request.headers.get("x-csrf-token") or form_csrf
    if not provided:
        content_type = request.headers.get("content-type", "")
        if (
            "application/x-www-form-urlencoded" in content_type
            or "multipart/form-data" in content_type
        ):
            form = await request.form()
            val = form.get("csrf_token")
            if isinstance(val, str):
                provided = val

    if not provided or not hmac.compare_digest(provided, expected):
        raise ProblemError(403, "CSRF verification failed")
