import asyncio
import json

import httpx
import pytest


@pytest.mark.db
async def test_sse_live_summary_updates_within_3s(
    live_server: str,
    host_headers: dict[str, str],
) -> None:
    async with httpx.AsyncClient(base_url=live_server, timeout=10.0) as client:
        seed_resp = await client.post("/v1/demo/seed", headers=host_headers)
        assert seed_resp.status_code == 200
        event_key = seed_resp.json()["eventKey"]

        # Get an unreplied household link via /demo/join
        join_resp = await client.get("/demo/join", follow_redirects=False)
        assert join_resp.status_code == 303
        loc = join_resp.headers["location"]
        token = loc.rstrip("/").split("/")[-1]

        inv_resp = await client.get(f"/v1/invites/{event_key}/{token}")
        assert inv_resp.status_code == 200
        fn_id = inv_resp.json()["functions"][0]["id"]

        received_event: asyncio.Future[dict[str, object]] = (
            asyncio.get_running_loop().create_future()
        )
        connected = asyncio.Event()

        async def _listen_sse() -> None:
            async with client.stream(
                "GET",
                f"/v1/events/{event_key}/live?format=json",
                headers=host_headers,
            ) as stream:
                assert stream.status_code == 200
                connected.set()
                event_name = ""
                async for line in stream.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event_name == "summary":
                        payload = json.loads(line.split(":", 1)[1].strip())
                        if not received_event.done():
                            received_event.set_result(payload)
                        return

        task = asyncio.create_task(_listen_sse())
        try:
            await asyncio.wait_for(connected.wait(), timeout=3.0)
            await asyncio.sleep(0.1)

            rsvp_resp = await client.put(
                f"/v1/invites/{event_key}/{token}/rsvp",
                headers={"Idempotency-Key": "sse-live-test-key-01"},
                json={
                    "functions": [
                        {
                            "functionId": fn_id,
                            "attending": "yes",
                            "adults": 1,
                            "children": 0,
                            "meals": {"veg": 1, "jain": 0, "nonVeg": 0},
                        }
                    ]
                },
            )
            assert rsvp_resp.status_code == 200

            summary_payload = await asyncio.wait_for(received_event, timeout=3.0)
            assert summary_payload["eventKey"] == event_key
            assert isinstance(summary_payload["functions"], list)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.db
async def test_pg_notify_broadcaster_fanout(
    db: None,
    settings: object,
) -> None:
    from hostora.config import Settings
    from hostora.realtime.broadcaster import PgNotifyBroadcaster

    _ = db
    assert isinstance(settings, Settings)
    dsn = settings.database_url
    b1 = PgNotifyBroadcaster(dsn)
    b2 = PgNotifyBroadcaster(dsn)
    await b1.start()
    await b2.start()
    try:
        async with b1.subscribe("DEM01") as q1:
            await b2.publish("DEM01", "summary")
            msg = await asyncio.wait_for(q1.get(), timeout=2.0)
            assert msg == "summary"
    finally:
        await b1.close()
        await b2.close()
