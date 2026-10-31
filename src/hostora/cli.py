"""`hostora` command-line interface. More commands are added in later milestones."""

from __future__ import annotations

import base64
import secrets

import typer

from hostora.engine.passes import generate_key_pair_hex

app = typer.Typer(help="Hostora developer and operations commands.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Hostora developer and operations commands."""


@app.command("gen-keys")
def gen_keys() -> None:
    """Print fresh secrets as .env lines. Paste into .env locally or into your host's secrets."""
    private_hex, public_hex = generate_key_pair_hex()
    lines = {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "TOKEN_HMAC_SECRET": secrets.token_urlsafe(48),
        "PHONE_ENC_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "PASS_SIGNING_KEY": private_hex,
        "PASS_PUBLIC_KEY": public_hex,
        "CRON_SECRET": secrets.token_urlsafe(32),
    }
    for name, value in lines.items():
        typer.echo(f"{name}={value}")


if __name__ == "__main__":
    app()
