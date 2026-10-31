from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from hostora.adapters.commerce import build_offers_for_item, compute_days_left
from hostora.data.loader import get_data


def test_commerce_offers_by_category_and_days_left() -> None:
    data = get_data()
    food_item = SimpleNamespace(category="food", search_query="chicken biryani")
    bev_item = SimpleNamespace(category="beverage", search_query="soft drinks")
    decor_item = SimpleNamespace(category="decor", search_query="fairy lights")

    # Food always uses food channels regardless of days_left
    for days in (0, 3, 14):
        food_offers = build_offers_for_item(food_item, days, data.channels)
        assert food_offers
        assert all(o.kind == "food" for o in food_offers)
        assert all("chicken+biryani" in o.url for o in food_offers)

    # Beverage: <1 -> quick; 1-7 -> quick then ecommerce; >7 -> ecommerce then quick
    bev_0 = build_offers_for_item(bev_item, 0, data.channels)
    assert all(o.kind == "quick" for o in bev_0)

    bev_3 = build_offers_for_item(bev_item, 3, data.channels)
    assert bev_3[0].kind == "quick"
    assert bev_3[-1].kind == "ecommerce"

    bev_10 = build_offers_for_item(bev_item, 10, data.channels)
    assert bev_10[0].kind == "ecommerce"
    assert bev_10[-1].kind == "quick"

    # Decor: <1 -> quick; 1-7 -> ecommerce then quick; >7 -> ecommerce only
    dec_0 = build_offers_for_item(decor_item, 0, data.channels)
    assert all(o.kind == "quick" for o in dec_0)

    dec_3 = build_offers_for_item(decor_item, 3, data.channels)
    assert dec_3[0].kind == "ecommerce"
    assert dec_3[-1].kind == "quick"

    dec_10 = build_offers_for_item(decor_item, 10, data.channels)
    assert all(o.kind == "ecommerce" for o in dec_10)


def test_compute_days_left() -> None:
    now = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)
    future = now + timedelta(days=5)
    assert compute_days_left(future, now) == 5
    past = now - timedelta(days=2)
    assert compute_days_left(past, now) == 0
