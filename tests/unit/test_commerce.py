from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from hostora.adapters.commerce import build_offers_for_item, compute_minutes_left
from hostora.data.loader import get_data


def test_commerce_offers_by_category_and_minutes_left() -> None:
    data = get_data()
    food_item = SimpleNamespace(category="food", search_query="chicken biryani")
    bev_item = SimpleNamespace(category="beverage", search_query="soft drinks")
    decor_item = SimpleNamespace(category="decor", search_query="fairy lights")

    # Food uses food channels when feasible (>= 90 min left with 30m safety)
    for mins in (120, 4320, 20160):
        food_offers = build_offers_for_item(food_item, mins, data.channels)
        assert food_offers
        assert all(o.kind == "food" for o in food_offers)
        assert all("chicken+biryani" in o.url for o in food_offers)

    # Beverage at 120 minutes: only quick commerce fits (multi-day ecommerce is infeasible!)
    bev_120 = build_offers_for_item(bev_item, 120, data.channels)
    assert bev_120
    assert all(o.kind == "quick" for o in bev_120)
    assert all(o.channel not in ("amazon", "flipkart") for o in bev_120)

    # Beverage at 5 days (7200 min): quick first, then ecommerce
    bev_5d = build_offers_for_item(bev_item, 7200, data.channels)
    assert bev_5d[0].kind == "quick"
    assert bev_5d[-1].kind == "ecommerce"

    # Beverage at 10 days (14400 min): ecommerce first, then quick
    bev_10d = build_offers_for_item(bev_item, 14400, data.channels)
    assert bev_10d[0].kind == "ecommerce"
    assert bev_10d[-1].kind == "quick"

    # Decor at 120 minutes: multi-day ecommerce cannot arrive in time -> empty (unfulfillable)
    dec_120 = build_offers_for_item(decor_item, 120, data.channels)
    assert dec_120 == []

    # Decor at 10 days (14400 min): ecommerce is feasible
    dec_10d = build_offers_for_item(decor_item, 14400, data.channels)
    assert dec_10d
    assert all(o.kind == "ecommerce" for o in dec_10d)


def test_compute_minutes_left_across_midnight_and_timezones() -> None:
    ist = ZoneInfo("Asia/Kolkata")
    now_utc = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)  # 23:30 IST on Oct 31
    starts_ist = datetime(2026, 11, 1, 1, 30, tzinfo=ist)  # 01:30 IST on Nov 1 (across midnight)
    assert compute_minutes_left(starts_ist, now_utc) == 120

    past = now_utc - timedelta(hours=2)
    assert compute_minutes_left(past, now_utc) == 0
