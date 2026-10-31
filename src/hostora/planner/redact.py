from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)
_PHONE_LIKE_RE = re.compile(r"\+?\d[\d\s\-]{5,}\d")
_DIGIT_RUN_RE = re.compile(r"\d")


def _replace_phone_runs(text: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        s = match.group(0)
        digits = _DIGIT_RUN_RE.findall(s)
        if len(digits) >= 7:
            return "[number]"
        return s

    return _PHONE_LIKE_RE.sub(_sub, text)


def redact_brief(text: str, forbidden_terms: Iterable[str] = ()) -> str:
    cleaned = _URL_RE.sub("[link]", text)
    cleaned = _EMAIL_RE.sub("[email]", cleaned)
    cleaned = _replace_phone_runs(cleaned)
    for term in forbidden_terms:
        t = term.strip()
        if len(t) >= 3:
            cleaned = re.sub(re.escape(t), "[name]", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:1000]


def assert_clean(text: str, forbidden_terms: Iterable[str] = ()) -> bool:
    """Return True if text has no email, 7+ digit run, or forbidden household name."""
    if _EMAIL_RE.search(text):
        return False
    for match in _PHONE_LIKE_RE.finditer(text):
        if len(_DIGIT_RUN_RE.findall(match.group(0))) >= 7:
            return False
    lowered = text.lower()
    for term in forbidden_terms:
        t = term.strip().lower()
        if len(t) >= 3 and t in lowered:
            return False
    return True
