from hostora.adapters.whatsapp import build_whatsapp_url


def test_whatsapp_url_with_and_without_phone() -> None:
    url_with_phone = build_whatsapp_url(
        phone_digits="98765 43210",
        language="en",
        host_name="Arjun & Sara",
        event_title="Reception",
        link="https://hostora.test/i/DEM01/tok",
        code="DEM01-ABCDE",
    )
    assert url_with_phone.startswith("https://wa.me/919876543210?text=")
    assert "Arjun%20%26%20Sara" in url_with_phone
    assert "DEM01-ABCDE" in url_with_phone

    url_no_phone = build_whatsapp_url(
        phone_digits=None,
        language="hi",
        host_name="Arjun & Sara",
        event_title="Reception",
        link="https://hostora.test/i/DEM01/tok",
        code="DEM01-ABCDE",
    )
    assert url_no_phone.startswith("https://wa.me/?text=")
    assert "DEM01-ABCDE" in url_no_phone
