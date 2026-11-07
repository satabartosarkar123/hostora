from __future__ import annotations

import httpx
import pytest
import respx

from hostora.adapters.osm import _haversine_km, query_overpass_vendors, validate_osm_tag
from hostora.config import get_settings


def test_haversine_km() -> None:
    # Bangalore to Delhi approx 1740 km
    dist = _haversine_km(12.9716, 77.5946, 28.6139, 77.2090)
    assert 1700 < dist < 1800


@pytest.mark.asyncio
async def test_validate_osm_tag_respx() -> None:
    settings = get_settings()
    assert await validate_osm_tag("shop", "bakery") is True
    assert await validate_osm_tag("unknown", "invalid") is False

    settings.demo_mode = False
    try:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://taginfo.openstreetmap.org/api/4/tag/stats").mock(
                return_value=httpx.Response(200, json={"data": [{"count": 5000}]})
            )
            assert await validate_osm_tag("shop", "bakery") is True

            # Non-200 status
            mock.get("https://taginfo.openstreetmap.org/api/4/tag/stats").mock(
                return_value=httpx.Response(500)
            )
            assert await validate_osm_tag("shop", "bakery") is False

        # Exception fallback
        assert await validate_osm_tag("shop", "confectionery") is True
        assert await validate_osm_tag("unknown", "invalid") is False
    finally:
        settings.demo_mode = True


@pytest.mark.asyncio
async def test_query_overpass_vendors() -> None:
    settings = get_settings()
    settings.demo_mode = True
    demo_vendors = await query_overpass_vendors(12.97, 77.59, "craft", "caterer")
    assert len(demo_vendors) >= 1
    assert "Nearby" in demo_vendors[0]["label"]

    settings.demo_mode = False
    try:
        with respx.mock(assert_all_called=False) as mock:
            mock.post("https://overpass-api.de/api/interpreter").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "elements": [
                            {
                                "lat": 12.98,
                                "lon": 77.60,
                                "tags": {"name": "Annapurna Caterers"},
                            }
                        ]
                    },
                )
            )
            vendors = await query_overpass_vendors(12.97, 77.59, "craft", "caterer")
            assert len(vendors) == 1
            assert vendors[0]["name"] == "Annapurna Caterers"

            # 500 error
            mock.post("https://overpass-api.de/api/interpreter").mock(
                return_value=httpx.Response(500)
            )
            v_empty = await query_overpass_vendors(12.97, 77.59, "craft", "caterer")
            assert v_empty == []

        # Exception fallback
        v_fallback = await query_overpass_vendors(12.97, 77.59, "craft", "caterer")
        assert len(v_fallback) == 1
        assert "Local Caterer" in v_fallback[0]["name"]
    finally:
        settings.demo_mode = True
