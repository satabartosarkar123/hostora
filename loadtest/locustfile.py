from __future__ import annotations

import random
import uuid
from typing import Any

import httpx
from locust import HttpUser, between, events, task

_SHARED_STATE: dict[str, Any] = {
    "event_key": "DEM01",
    "tokens": [],
}


@events.test_start.add_listener  # type: ignore[untyped-decorator]
def on_test_start(environment: Any, **_kwargs: Any) -> None:
    host = (environment.host or "http://127.0.0.1:8000").rstrip("/")
    headers = {"Authorization": "Demo demo-host"}
    with httpx.Client(base_url=host, timeout=20.0) as client:
        seed_res = client.post("/v1/demo/seed", headers=headers)
        seed_res.raise_for_status()

        inv_res = client.post("/v1/events/DEM01/invites:prepare", headers=headers)
        inv_res.raise_for_status()
        tokens = [row["link"].rstrip("/").split("/")[-1] for row in inv_res.json()]
        _SHARED_STATE["tokens"] = tokens


class GuestUser(HttpUser):
    weight = 4
    wait_time = between(0.2, 0.8)  # type: ignore[no-untyped-call]

    @task
    def open_invite_and_reply(self) -> None:
        tokens: list[str] = _SHARED_STATE["tokens"]
        if not tokens:
            return

        token = random.choice(tokens)  # noqa: S311
        event_key = _SHARED_STATE["event_key"]

        inv_resp = self.client.get(
            f"/v1/invites/{event_key}/{token}",
            name="/v1/invites/[event_key]/[token]",
        )
        if inv_resp.status_code != 200:
            return
        fns = inv_resp.json().get("functions", [])
        if not fns:
            return
        fn_id = fns[0]["id"]

        self.client.put(
            f"/v1/invites/{event_key}/{token}/rsvp",
            name="/v1/invites/[event_key]/[token]/rsvp",
            headers={"Idempotency-Key": f"locust-{uuid.uuid4().hex}"},
            json={
                "functions": [
                    {
                        "functionId": fn_id,
                        "attending": "yes",
                        "adults": 1,
                        "children": 0,
                        "meals": {"veg": 1, "jain": 0, "nonVeg": 0},
                        "dietaryRules": [],
                        "allergies": [],
                    }
                ]
            },
        )


class HostUser(HttpUser):
    weight = 1
    wait_time = between(0.5, 1.5)  # type: ignore[no-untyped-call]

    @task
    def poll_summary(self) -> None:
        event_key = _SHARED_STATE["event_key"]
        self.client.get(
            f"/v1/events/{event_key}/summary",
            name="/v1/events/[event_key]/summary",
            headers={"Authorization": "Demo demo-host"},
        )
