from __future__ import annotations

import base64
import contextlib
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import uvicorn
from playwright.sync_api import Response, sync_playwright

from hostora.config import Settings, assert_distinct_test_db, get_settings
from hostora.engine.passes import generate_key_pair_hex
from hostora.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

MAX_BYTES = 150 * 1024  # 150 KB


@contextlib.contextmanager
def _start_local_demo_server() -> Iterator[str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])

    origin = f"http://127.0.0.1:{port}"
    env_test_url = os.environ.get("TEST_DATABASE_URL")
    env_prod_url = os.environ.get("DATABASE_URL")
    if not env_test_url:
        with contextlib.suppress(Exception):
            loaded = get_settings()
            env_test_url = loaded.test_database_url
            env_prod_url = env_prod_url or loaded.database_url
    if env_prod_url and env_test_url:
        assert_distinct_test_db(env_prod_url, env_test_url)
    db_url = (
        env_test_url or env_prod_url or "postgresql://hostora:hostora@localhost:5432/hostora_test"
    )
    priv_hex, pub_hex = generate_key_pair_hex()
    settings = Settings(
        env="test",
        public_origin=origin,
        database_url=db_url,
        test_database_url=db_url,
        secret_key=secrets.token_urlsafe(48),
        token_hmac_secret=secrets.token_urlsafe(48),
        phone_enc_key=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        pass_signing_key=priv_hex,
        pass_public_key=pub_hex,
        cron_secret=secrets.token_urlsafe(32),
        demo_mode=True,
        data_dir=Path("data"),
    )
    app = create_app(settings)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{origin}/healthz", timeout=0.5)
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)

    seed_resp = httpx.post(
        f"{origin}/v1/demo/seed",
        headers={"Authorization": "Demo demo-host"},
        timeout=15.0,
    )
    seed_resp.raise_for_status()
    invite_url = str(seed_resp.json()["inviteLinks"][0])

    try:
        yield invite_url
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def measure_url_weight(url: str) -> int:
    responses: list[Response] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        device = p.devices.get("Moto G4") or p.devices["Pixel 5"]
        context = browser.new_context(**device)
        page = context.new_page()
        page.on("response", lambda resp: responses.append(resp))
        page.goto(url, wait_until="networkidle")
        total = 0
        for resp in responses:
            try:
                sizes = resp.request.sizes()
                body_size = int(sizes.get("responseBodySize", 0))
                if body_size <= 0:
                    body_size = len(resp.body())
            except Exception:
                body_size = 0
            total += max(0, body_size)
        context.close()
        browser.close()
    return total


def main() -> int:
    if len(sys.argv) > 1:
        total = measure_url_weight(sys.argv[1])
    else:
        with _start_local_demo_server() as url:
            total = measure_url_weight(url)

    kb = total / 1024.0
    print(f"Guest invite page transferred: {total} bytes ({kb:.2f} KB) / budget 150.00 KB")
    if total > MAX_BYTES:
        print(f"ERROR: Page weight {kb:.2f} KB exceeds 150 KB limit!", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
