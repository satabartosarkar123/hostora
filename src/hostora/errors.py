from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic.alias_generators import to_camel
from starlette.exceptions import HTTPException as StarletteHTTPException

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

logger = structlog.get_logger(__name__)


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        field: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.title = title
        self.detail = detail
        self.field = field
        self.headers = headers


def _wants_html(request: Request) -> bool:
    path = request.url.path
    if path.startswith(("/v1/", "/internal/", "/healthz", "/readyz", "/auth/")):
        return False
    accept = request.headers.get("accept", "")
    return "text/html" in accept or not path.startswith("/v1")


def _problem_response(
    status: int,
    title: str,
    detail: str | None = None,
    field: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {  # Any: RFC 9457 problem details payload
        "type": "about:blank",
        "title": title,
        "status": status,
    }
    if detail is not None:
        body["detail"] = detail
    if field is not None:
        body["field"] = field
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=headers,
    )


def _html_error_response(
    request: Request,
    status: int,
    title: str,
    detail: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and status == 404:
        try:
            resp: Response = templates.TemplateResponse(
                request,
                "guest/not_found.html",
                {"title": title, "detail": detail},
                status_code=404,
                headers=headers,
            )
            return resp
        except Exception:
            logger.debug("not_found_template_missing")
    msg = detail or title
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body><h1>{title}</h1>"
        f"<p>{msg}</p><p><a href='/'>Back to Hostora</a></p></body></html>"
    )
    return HTMLResponse(content=html, status_code=status, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def handle_problem_error(request: Request, exc: ProblemError) -> Response:
        if _wants_html(request):
            return _html_error_response(
                request, exc.status, exc.title, exc.detail, headers=exc.headers
            )
        return _problem_response(
            exc.status, exc.title, detail=exc.detail, field=exc.field, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
        errors = exc.errors()
        field: str | None = None
        detail = "Validation failed"
        if errors:
            first = errors[0]
            detail = str(first.get("msg", detail))
            loc = [str(p) for p in first.get("loc", ()) if p not in ("body", "query", "path")]
            if loc:
                field = to_camel(loc[0])
        return _problem_response(422, "Invalid request", detail=detail, field=field)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
        title = str(exc.detail) if exc.detail else "HTTP error"
        if _wants_html(request):
            return _html_error_response(request, exc.status_code, title)
        return _problem_response(exc.status_code, title)

    @app.exception_handler(Exception)
    async def handle_unhandled_error(request: Request, exc: Exception) -> Response:
        req_id = getattr(request.state, "request_id", None)
        logger.exception("unhandled_error", request_id=req_id, error_type=type(exc).__name__)
        if _wants_html(request):
            return _html_error_response(request, 500, "Internal server error")
        return _problem_response(500, "Internal server error")
