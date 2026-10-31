from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import jwt

from hostora.errors import ProblemError

JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)
_jwks = jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600)


@dataclass(frozen=True, slots=True)
class HostIdentity:
    uid: str
    name: str | None = None


async def verify_firebase_token(id_token: str, project_id: str) -> HostIdentity:
    if not project_id:
        raise ProblemError(401, "Firebase sign-in is not configured")

    def _verify() -> dict[str, Any]:  # Any: JWT claims are untyped JSON
        key = _jwks.get_signing_key_from_jwt(id_token)
        claims: dict[str, Any] = jwt.decode(  # Any: PyJWT decode returns dict[str, Any]
            id_token,
            key.key,
            algorithms=["RS256"],
            audience=project_id,
            issuer=f"https://securetoken.google.com/{project_id}",
            options={"require": ["exp", "iat", "sub"]},
            leeway=30,
        )
        return claims

    try:
        claims = await asyncio.to_thread(_verify)
    except jwt.PyJWTError as exc:
        raise ProblemError(401, "Invalid sign-in token") from exc
    uid = claims.get("sub")
    if not isinstance(uid, str) or not uid:
        raise ProblemError(401, "Invalid sign-in token")
    name = claims.get("name")
    return HostIdentity(uid=uid, name=name if isinstance(name, str) else None)
