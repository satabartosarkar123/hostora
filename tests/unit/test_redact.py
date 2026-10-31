from hostora.planner.redact import assert_clean, redact_brief


def test_redact_brief_removes_emails_phones_urls_and_names() -> None:
    raw = (
        "Call Sharma Family at +91 98765-43210 or email host@example.com "
        "and see https://example.com/menu for 50 guests"
    )
    cleaned = redact_brief(raw, forbidden_terms=["Sharma Family"])
    assert "98765" not in cleaned
    assert "host@example.com" not in cleaned
    assert "https://example.com" not in cleaned
    assert "Sharma Family" not in cleaned
    assert "[number]" in cleaned
    assert "[email]" in cleaned
    assert "[link]" in cleaned
    assert "[name]" in cleaned
    assert "50 guests" in cleaned
    assert assert_clean(cleaned, forbidden_terms=["Sharma Family"])


def test_assert_clean_rejects_pii() -> None:
    assert not assert_clean("Phone 9876543210 here")
    assert not assert_clean("Contact user@domain.org")
    assert not assert_clean("Invite Khan Family please", forbidden_terms=["Khan Family"])
    assert assert_clean("Normal brief for 45 guests with ₹60000 budget")
