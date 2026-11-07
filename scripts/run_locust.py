from __future__ import annotations

import base64
import secrets
import subprocess
import sys
import threading
import time

import uvicorn

from hostora.config import Settings, assert_distinct_test_db, get_settings
from hostora.engine.passes import generate_key_pair_hex
from hostora.main import create_app


def run() -> None:
    import os
    from pathlib import Path

    port = 18080
    origin = f"http://127.0.0.1:{port}"
    loaded = get_settings()
    env_test_url = os.environ.get("LOCUST_DATABASE_URL") or loaded.test_database_url
    env_prod_url = loaded.database_url
    if env_prod_url and env_test_url:
        assert_distinct_test_db(env_prod_url, env_test_url)
    db_url = env_test_url or "postgresql://hostora:hostora@localhost:5432/hostora_test"
    priv_hex, pub_hex = generate_key_pair_hex()
    settings = Settings(
        env="test",
        public_origin=origin,
        database_url=db_url,
        test_database_url=db_url,
        demo_mode=True,
        secret_key=secrets.token_hex(32),
        token_hmac_secret=secrets.token_hex(32),
        phone_enc_key=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        store_token_key=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        pass_signing_key=priv_hex,
        pass_public_key=pub_hex,
        cron_secret=secrets.token_hex(32),
        log_level="ERROR",
    )
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    time.sleep(1.5)

    users = os.environ.get("LOCUST_USERS", "100")
    spawn_rate = os.environ.get("LOCUST_SPAWN_RATE", "10")
    run_time = os.environ.get("LOCUST_RUN_TIME", "2m")
    Path("loadtest/results").mkdir(parents=True, exist_ok=True)

    cmd = [
        "uv",
        "run",
        "locust",
        "-f",
        "loadtest/locustfile.py",
        "--headless",
        "-H",
        origin,
        "-u",
        users,
        "-r",
        spawn_rate,
        "-t",
        run_time,
        "--only-summary",
        "--csv",
        "loadtest/results/local",
    ]
    res = subprocess.run(cmd, check=False)  # noqa: S603
    server.should_exit = True
    t.join(timeout=2.0)
    sys.exit(res.returncode)


if __name__ == "__main__":
    run()
