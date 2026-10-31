from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

if TYPE_CHECKING:
    from fastapi import Request, Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        settings = request.app.state.settings
        fb_domain = settings.firebase_auth_domain
        frame_src = f"https://{fb_domain}" if fb_domain else "'none'"
        csp = (
            "default-src 'self'; "
            "img-src 'self' data: https://*.tile.openstreetmap.org; "
            "script-src 'self' https://www.gstatic.com https://apis.google.com; "
            "connect-src 'self' https://*.googleapis.com https://securetoken.googleapis.com; "
            f"frame-src {frame_src}; "
            "style-src 'self' 'unsafe-inline'"
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(self), geolocation=(self)")
        response.headers.setdefault("Content-Security-Policy", csp)
        if settings.env == "prod":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
