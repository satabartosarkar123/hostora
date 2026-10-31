from __future__ import annotations

from hostora.web.i18n import load_locales, translate


def test_locales_have_identical_keys() -> None:
    locales = load_locales()
    assert set(locales["en"].keys()) == set(locales["hi"].keys())
    assert len(locales["en"]) >= 30


def test_translate_and_fallback() -> None:
    assert translate("app.name", "en") == "Hostora"
    assert "होस्टोरा" in translate("app.name", "hi")
    assert translate("invite.greeting", "en", name="Sharma") == "Invitation for Sharma"
    assert translate("nonexistent.key", "hi") == "nonexistent.key"
