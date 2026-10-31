import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.engine.passes import verify_pass


@pytest.mark.db
async def test_gate_manifest_duplicates_and_idempotent_batch_sync(
    client: httpx.AsyncClient,
    db: AsyncSession,
    settings: Settings,
    host_headers: dict[str, str],
) -> None:
    _ = db
    seed = await client.post("/v1/demo/seed", headers=host_headers)
    assert seed.status_code == 200
    event_key = seed.json()["eventKey"]

    ev = await client.get(f"/v1/events/{event_key}", headers=host_headers)
    reception_fn = next(f for f in ev.json()["functions"] if f["name"] == "Reception")
    fn_id = reception_fn["id"]

    # 1. Create gate link
    link_resp = await client.post(
        f"/v1/events/{event_key}/gate-links",
        headers=host_headers,
        json={"functionId": fn_id, "hours": 24},
    )
    assert link_resp.status_code == 200
    gate_url = link_resp.json()["url"]
    gate_token = gate_url.split("?t=")[1]

    # 2. Fetch manifest and verify privacy (only passId, displayName, seats)
    manifest_resp = await client.get(
        f"/v1/gate/{event_key}/manifest",
        params={"t": gate_token},
    )
    assert manifest_resp.status_code == 200
    manifest = manifest_resp.json()
    assert manifest["eventKey"] == event_key
    assert manifest["functionId"] == fn_id
    assert len(manifest["passes"]) > 0
    first_pass = manifest["passes"][0]
    assert set(first_pass.keys()) == {"passId", "displayName", "seats"}

    # 3. Forged pass is rejected by verify_pass
    forged_token = f"v1.{event_key}.{first_pass['passId']}.{first_pass['seats']}.9999999999.AAAA"
    assert verify_pass(forged_token, settings.pass_public_key, 1700000000) is None

    # 4. Same pass scanned on two devices -> possible_duplicates == 1
    batch_dev1 = {
        "functionId": fn_id,
        "scans": [
            {
                "deviceId": "dev_alpha",
                "seq": 1,
                "passId": first_pass["passId"],
                "seats": first_pass["seats"],
                "at": 1761900000000,
            }
        ],
    }
    res1 = await client.post(
        f"/v1/gate/{event_key}/scans:batch",
        params={"t": gate_token},
        json=batch_dev1,
    )
    assert res1.status_code == 200
    assert res1.json()["accepted"] == 1
    assert res1.json()["ignored"] == 0
    assert res1.json()["arrivals"] == first_pass["seats"]
    assert res1.json()["possibleDuplicates"] == 0

    batch_dev2 = {
        "functionId": fn_id,
        "scans": [
            {
                "deviceId": "dev_beta",
                "seq": 1,
                "passId": first_pass["passId"],
                "seats": first_pass["seats"],
                "at": 1761900005000,
            }
        ],
    }
    res2 = await client.post(
        f"/v1/gate/{event_key}/scans:batch",
        params={"t": gate_token},
        json=batch_dev2,
    )
    assert res2.status_code == 200
    assert res2.json()["accepted"] == 1
    assert res2.json()["possibleDuplicates"] == 1
    assert res2.json()["arrivals"] == first_pass["seats"] * 2

    # 5. Re-uploading the same batch -> ignored == len(batch) and arrivals unchanged
    res_dup = await client.post(
        f"/v1/gate/{event_key}/scans:batch",
        params={"t": gate_token},
        json=batch_dev1,
    )
    assert res_dup.status_code == 200
    assert res_dup.json()["accepted"] == 0
    assert res_dup.json()["ignored"] == len(batch_dev1["scans"])
    assert res_dup.json()["arrivals"] == first_pass["seats"] * 2

    # 6. Verify /gate/sw.js and /gate/{event_key}
    sw_resp = await client.get("/gate/sw.js")
    assert sw_resp.status_code == 200
    assert sw_resp.headers.get("service-worker-allowed") == "/gate/"

    gate_page = await client.get(f"/gate/{event_key}", params={"t": gate_token})
    assert gate_page.status_code == 200
    assert "Venue QR Gate" in gate_page.text
