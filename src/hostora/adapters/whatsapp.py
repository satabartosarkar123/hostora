from __future__ import annotations

from urllib.parse import quote

from hostora.data.loader import get_data
from hostora.security.crypto import normalize_phone


def build_whatsapp_url(
    *,
    phone_digits: str | None,
    language: str,
    host_name: str,
    event_title: str,
    link: str,
    code: str,
) -> str:
    data = get_data()
    invite_templates = data.traditions.get("invite_text", {})
    template = (
        invite_templates.get(language)
        or invite_templates.get("en")
        or ("{host} invites you to {event}. Please reply here: {link} (code {code})")
    )
    message = str(template).format(
        host=host_name,
        event=event_title,
        link=link,
        code=code,
    )
    encoded = quote(message)
    if phone_digits:
        digits = normalize_phone(phone_digits)
        if digits:
            return f"https://wa.me/{digits}?text={encoded}"
    return f"https://wa.me/?text={encoded}"
