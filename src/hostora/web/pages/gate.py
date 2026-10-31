from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from hostora.config import Settings
from hostora.security.tokens import verify_gate_token
from hostora.web.deps import get_settings_dep
from hostora.web.i18n import resolve_language
from hostora.web.templating import template_context

router = APIRouter(tags=["gate-pages"])

_SW_PATH = Path(__file__).resolve().parent.parent.parent / "static" / "gate-sw.js"


@router.get("/gate/sw.js")
async def gate_service_worker() -> Response:
    js = _SW_PATH.read_text(encoding="utf-8")
    return Response(
        content=js,
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/gate/",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/gate/{event_key}")
async def gate_app_page(
    event_key: str,
    t: Annotated[str, Query(min_length=5)],
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    now_ts = int(datetime.now(UTC).timestamp())
    secret = settings.token_hmac_secret.get_secret_value()
    function_id = verify_gate_token(secret, event_key, t, now_ts)
    lang = resolve_language(request)
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "gate/gate.html",
        template_context(
            request,
            lang=lang,
            event_key=event_key,
            gate_token=t,
            function_id=function_id,
        ),
    )
    return resp
