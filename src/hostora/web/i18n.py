from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from fastapi import Request

logger = structlog.get_logger(__name__)
_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
_WARNED_MISSING: set[tuple[str, str]] = set()


@lru_cache
def load_locales() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for lang in ("en", "hi"):
        path = _LOCALES_DIR / f"{lang}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[lang] = {str(k): str(v) for k, v in raw.items()}
    return out


def translate(key: str, lang: str = "en", **kwargs: object) -> str:
    locales = load_locales()
    lang_map = locales.get(lang) or locales["en"]
    template = lang_map.get(key)
    if template is None:
        if (lang, key) not in _WARNED_MISSING:
            _WARNED_MISSING.add((lang, key))
            logger.warning("missing_translation_key", lang=lang, key=key)
        template = locales["en"].get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except KeyError:
            return template
    return template


def resolve_language(request: Request, fallback: str = "en") -> str:
    q_lang = request.query_params.get("lang")
    if q_lang in ("en", "hi"):
        return q_lang
    c_lang = request.cookies.get("lang")
    if c_lang in ("en", "hi"):
        return c_lang
    if fallback in ("en", "hi"):
        return fallback
    return "en"
