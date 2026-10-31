from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.templating import Jinja2Templates

from hostora.data.loader import get_data
from hostora.security.csrf import ensure_csrf_token
from hostora.web.i18n import resolve_language, translate

if TYPE_CHECKING:
    from fastapi import Request

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    data_ver = get_data().version[:8]
    templates.env.globals["asset_v"] = data_ver
    templates.env.globals["t"] = translate
    return templates


def template_context(
    request: Request,
    *,
    lang: str | None = None,
    include_csrf: bool = False,
    **extra: Any,  # Any: template variables
) -> dict[str, Any]:  # Any: template context dict
    resolved_lang = lang or resolve_language(request)

    def _t(key: str, **kwargs: object) -> str:
        return translate(key, lang=resolved_lang, **kwargs)

    ctx: dict[str, Any] = {
        "request": request,
        "lang": resolved_lang,
        "t": _t,
        "asset_v": get_data().version[:8],
        "demo_mode": request.app.state.settings.demo_mode,
    }
    if include_csrf:
        ctx["csrf_token"] = ensure_csrf_token(request)
    ctx.update(extra)
    return ctx
