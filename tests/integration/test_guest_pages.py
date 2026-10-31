from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.db


async def test_guest_html_pages_and_calendar_and_pass_svg(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    _ = db
    r_landing = await client.get("/?lang=hi")
    assert r_landing.status_code == 200
    assert "होस्टोरा" in r_landing.text
    assert r_landing.cookies.get("lang") == "hi"

    r_privacy = await client.get("/privacy")
    assert r_privacy.status_code == 200
    assert "Privacy Notice" in r_privacy.text

    r_code_get = await client.get("/r")
    assert r_code_get.status_code == 200

    r_code_bad = await client.post("/r", data={"code": "ZZZZZ-ZZZZZ"})
    assert r_code_bad.status_code == 404

    seed_resp = await client.post("/v1/demo/seed", headers=host_headers)
    assert seed_resp.status_code == 200
    invite_link = seed_resp.json()["inviteLinks"][0]
    path = "/" + invite_link.split("/i/", 1)[1]
    invite_path = f"/i{path}"
    token = invite_path.rsplit("/", 1)[-1]

    r_inv = await client.get(invite_path)
    assert r_inv.status_code == 200
    assert "DEM01" in invite_path

    hh_list = await client.get("/v1/events/DEM01/households", headers=host_headers)
    first_code = hh_list.json()["households"][0]["code"]
    r_code_ok = await client.post("/r", data={"code": first_code}, follow_redirects=False)
    assert r_code_ok.status_code == 303

    inv_json = (await client.get(f"/v1/invites/DEM01/{token}")).json()
    fn_ids = [f["id"] for f in inv_json["functions"]]

    bad_form: dict[str, object] = {
        "idempotency_key": "html-bad-meals-01",
        "language": "en",
        "function_ids": fn_ids,
    }
    for fid in fn_ids:
        bad_form[f"attending_{fid}"] = "yes"
        bad_form[f"adults_{fid}"] = "2"
        bad_form[f"children_{fid}"] = "0"
        bad_form[f"veg_{fid}"] = "1"
        bad_form[f"jain_{fid}"] = "0"
        bad_form[f"non_veg_{fid}"] = "0"

    r_bad_post = await client.post(invite_path, data=bad_form)
    assert r_bad_post.status_code == 422
    assert "Meal choices must add up" in r_bad_post.text

    good_form: dict[str, object] = {
        "idempotency_key": "html-good-rsvp-01",
        "language": "en",
        "function_ids": fn_ids,
    }
    for fid in fn_ids:
        good_form[f"attending_{fid}"] = "yes"
        good_form[f"adults_{fid}"] = "1"
        good_form[f"children_{fid}"] = "0"
        good_form[f"veg_{fid}"] = "1"
        good_form[f"jain_{fid}"] = "0"
        good_form[f"non_veg_{fid}"] = "0"
        good_form[f"allergies_{fid}"] = "peanuts"
        good_form[f"arrival_{fid}"] = "on_time"

    r_good_post = await client.post(invite_path, data=good_form, follow_redirects=False)
    assert r_good_post.status_code == 303
    done_path = r_good_post.headers["location"]

    r_done = await client.get(f"{done_path}?lang=en")
    assert r_done.status_code == 200
    assert "Thank you!" in r_done.text

    r_ics = await client.get(f"{invite_path}/calendar.ics?fn={fn_ids[0]}")
    assert r_ics.status_code == 200
    assert "BEGIN:VCALENDAR" in r_ics.text

    inv_after = (await client.get(f"/v1/invites/DEM01/{token}")).json()
    if inv_after["passes"]:
        pass_id = inv_after["passes"][0]["passId"]
        r_svg = await client.get(f"{invite_path}/pass/{pass_id}.svg")
        assert r_svg.status_code == 200
        assert "<svg" in r_svg.text
