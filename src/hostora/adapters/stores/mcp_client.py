"""Official MCP client wrapper with OAuth 2.1 + PKCE, AES-GCM token storage, runtime tool
discovery, contract verification, and strict safety/privacy guards (AGENTS.md section 25.2).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlencode

import httpx
import httpx2
import mcp
import structlog
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hostora.data.loader import get_data
from hostora.db.models import StoreConnection
from hostora.engine.delivery import Eta
from hostora.engine.delivery import Offer as DeliveryOffer
from hostora.engine.pricing import BuyOption, PriceQuote
from hostora.planner.redact import assert_clean
from hostora.security.crypto import decrypt_store_token, encrypt_store_token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.config import Settings

logger = structlog.get_logger(__name__)

StoreName = Literal["swiggy", "zomato"]
StoreStatus = Literal["connected", "not_approved_yet", "disconnected"]

_FORBIDDEN_TOOL_PATTERNS = re.compile(
    r"(place_.*order|submit_.*order|create_.*order|confirm_.*order|pay_.*|.*_payment|make_payment|charge_.*)",
    re.IGNORECASE,
)


class StoreError(RuntimeError):
    """Base error for store MCP adapter failures."""


class StoreContractError(StoreError):
    """Raised when a connected MCP server is missing a required tool or schema."""


class StoreSafetyError(StoreError):
    """Raised when an attempt is made to invoke an order-placing or payment tool."""


class StorePrivacyError(StoreError):
    """Raised when household PII or restricted data is detected in MCP arguments."""


@dataclass(frozen=True, slots=True)
class LiveStoreOffer:
    store: str
    channel: str
    item_id: str
    product_name: str
    pack_size: int
    unit: str
    price_inr: float
    inr_per_unit: float
    eta_min_minutes: float
    eta_max_minutes: float
    url: str
    available: bool = True

    def to_delivery_offer(self) -> DeliveryOffer:
        return DeliveryOffer(
            channel=self.channel,
            eta=Eta(
                min_minutes=self.eta_min_minutes,
                max_minutes=self.eta_max_minutes,
                source="live",
            ),
            price_inr=self.price_inr,
            url=self.url,
        )

    def to_buy_option(self, label: str) -> BuyOption:
        return BuyOption(
            channel=self.channel,
            label=label,
            url=self.url,
            inr_per_unit=self.inr_per_unit,
            pack_size=self.pack_size,
            product_name=self.product_name,
        )

    def to_price_quote(self, observed_on: Any) -> PriceQuote:
        return PriceQuote(
            source="live",
            low_inr_per_unit=self.inr_per_unit,
            high_inr_per_unit=self.inr_per_unit,
            observed_on=observed_on,
            detail=f"Live · {self.channel} · {self.product_name}",
        )


@dataclass(frozen=True, slots=True)
class CartBuildResult:
    store: str
    channel: str
    added_items: int
    total_inr: float
    eta_min_minutes: float
    eta_max_minutes: float
    checkout_url: str


@dataclass(slots=True)
class StoreTokenData:
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"  # noqa: S105
    scopes: list[str] = field(default_factory=list)
    expires_at: datetime | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "token_type": self.token_type,
                "scopes": self.scopes,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> StoreTokenData:
        data = json.loads(raw)
        exp_raw = data.get("expires_at")
        exp = datetime.fromisoformat(exp_raw) if isinstance(exp_raw, str) and exp_raw else None
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=data.get("refresh_token"),
            token_type=str(data.get("token_type", "Bearer")),
            scopes=list(data.get("scopes") or []),
            expires_at=exp,
        )


def generate_pkce_pair() -> tuple[str, str]:
    """Generate OAuth 2.1 PKCE (code_verifier, code_challenge) using S256."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def get_store_config(store: str) -> dict[str, Any]:
    sources = get_data().sources
    stores_cfg = sources.get("stores", {})
    if isinstance(stores_cfg, dict):
        cfg = stores_cfg.get(store, {})
        if isinstance(cfg, dict):
            return cfg
    return {}


def build_oauth_authorize_url(
    store: str,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: Sequence[str] = ("mcp:tools",),
) -> str:
    cfg = get_store_config(store)
    auth_url = str(cfg.get("oauth_authorize_url", f"https://mcp.{store}.com/oauth/authorize"))
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": " ".join(scopes),
    }
    return f"{auth_url}?{urlencode(params)}"


async def exchange_oauth_code(
    store: str,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str = "hostora-local",
    http_client: httpx.AsyncClient | None = None,
) -> StoreTokenData:
    cfg = get_store_config(store)
    token_url = str(cfg.get("oauth_token_url", f"https://mcp.{store}.com/oauth/token"))
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=10.0)
    try:
        resp = await client.post(token_url, data=payload)
        resp.raise_for_status()
        body = resp.json()
    finally:
        if owns_client:
            await client.aclose()

    expires_in = int(body.get("expires_in", 3600))
    scopes_raw = body.get("scope", "")
    scopes = scopes_raw.split() if isinstance(scopes_raw, str) else list(scopes_raw or [])
    return StoreTokenData(
        access_token=str(body["access_token"]),
        refresh_token=body.get("refresh_token"),
        token_type=str(body.get("token_type", "Bearer")),
        scopes=scopes,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
    )


async def refresh_oauth_token(
    store: str,
    *,
    refresh_token: str,
    client_id: str = "hostora-local",
    http_client: httpx.AsyncClient | None = None,
) -> StoreTokenData:
    cfg = get_store_config(store)
    token_url = str(cfg.get("oauth_token_url", f"https://mcp.{store}.com/oauth/token"))
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=10.0)
    try:
        resp = await client.post(token_url, data=payload)
        resp.raise_for_status()
        body = resp.json()
    finally:
        if owns_client:
            await client.aclose()

    expires_in = int(body.get("expires_in", 3600))
    scopes_raw = body.get("scope", "")
    scopes = scopes_raw.split() if isinstance(scopes_raw, str) else list(scopes_raw or [])
    return StoreTokenData(
        access_token=str(body["access_token"]),
        refresh_token=str(body.get("refresh_token") or refresh_token),
        token_type=str(body.get("token_type", "Bearer")),
        scopes=scopes,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
    )


async def save_store_token(
    session: AsyncSession,
    settings: Settings,
    host_uid: str,
    store: str,
    token_data: StoreTokenData,
) -> None:
    """Encrypt ``token_data`` with AES-GCM under ``STORE_TOKEN_KEY`` and upsert row."""
    key_b64 = settings.store_token_key.get_secret_value()
    enc = encrypt_store_token(token_data.to_json(), key_b64)
    now = datetime.now(UTC)
    stmt = pg_insert(StoreConnection).values(
        host_uid=host_uid,
        store=store,
        token_enc=enc,
        scopes=token_data.scopes,
        connected_at=now,
        expires_at=token_data.expires_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[StoreConnection.host_uid, StoreConnection.store],
        set_={
            "token_enc": enc,
            "scopes": token_data.scopes,
            "connected_at": now,
            "expires_at": token_data.expires_at,
        },
    )
    await session.execute(stmt)
    await session.flush()


async def load_store_token(
    session: AsyncSession,
    settings: Settings,
    host_uid: str,
    store: str,
    *,
    auto_refresh: bool = True,
    refresh_fn: Callable[[str, str], Awaitable[StoreTokenData]] | None = None,
) -> StoreTokenData | None:
    """Load and decrypt a host's store OAuth token, refreshing automatically when expired."""
    row = (
        await session.execute(
            select(StoreConnection).where(
                StoreConnection.host_uid == host_uid,
                StoreConnection.store == store,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    key_b64 = settings.store_token_key.get_secret_value()
    token_data = StoreTokenData.from_json(decrypt_store_token(row.token_enc, key_b64))
    if row.expires_at is not None:
        token_data.expires_at = row.expires_at

    now = datetime.now(UTC)
    if (
        auto_refresh
        and token_data.expires_at is not None
        and token_data.expires_at <= now
        and token_data.refresh_token
    ):
        if refresh_fn is not None:
            refreshed = await refresh_fn(store, token_data.refresh_token)
        else:
            refreshed = await refresh_oauth_token(store, refresh_token=token_data.refresh_token)
        await save_store_token(session, settings, host_uid, store, refreshed)
        return refreshed

    return token_data


async def disconnect_store(
    session: AsyncSession,
    host_uid: str,
    store: str,
) -> bool:
    """Delete a host's encrypted token for ``store`` immediately."""
    res = await session.execute(
        delete(StoreConnection).where(
            StoreConnection.host_uid == host_uid,
            StoreConnection.store == store,
        )
    )
    await session.flush()
    return bool(getattr(res, "rowcount", 0))


async def get_store_connection_status(
    session: AsyncSession,
    settings: Settings,
    host_uid: str,
    store: str,
) -> StoreStatus:
    """Return ``connected``, ``not_approved_yet``, or ``disconnected``."""
    row = (
        await session.execute(
            select(StoreConnection.store).where(
                StoreConnection.host_uid == host_uid,
                StoreConnection.store == store,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return "connected"

    cfg = get_store_config(store)
    enabled = bool(cfg.get("enabled", False))
    mode = str(cfg.get("mode", "localhost"))
    if not enabled or (settings.env == "prod" and mode != "production"):
        return "not_approved_yet"
    return "disconnected"


def discover_store_capabilities(
    store: str,
    tools: Sequence[Any],
    *,
    required_capabilities: Sequence[str] = (),
) -> dict[str, str]:
    """Map MCP ``list_tools`` output to abstract store capabilities and validate schemas.

    Raises ``StoreContractError`` if any capability in ``required_capabilities`` cannot be
    mapped or has a malformed inputSchema.
    """
    cfg = get_store_config(store)
    cap_cfg = cfg.get("capabilities", {})
    if not isinstance(cap_cfg, dict):
        cap_cfg = {}

    forbidden_set = {
        str(x).lower() for x in (cap_cfg.get("forbidden_tools") or []) if isinstance(x, str)
    }

    tool_by_name: dict[str, Any] = {}
    for t in tools:
        t_name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
        if isinstance(t_name, str) and t_name:
            tool_by_name[t_name] = t

    default_candidates: dict[str, list[str]] = {
        "search_products": ["search_products", "instamart_search_products", "product_search"],
        "search_restaurants": [
            "search_restaurants",
            "search_dishes",
            "get_restaurants_for_keyword",
        ],
        "get_menu": [
            "get_menu",
            "get_restaurant_menu",
            "get_menu_items_listing",
            "get_restaurant_menu_by_categories",
        ],
        "eta": [
            "get_eta",
            "check_serviceability",
            "get_addresses",
            "get_saved_addresses_for_user",
            "get_delivery_eta",
        ],
        "cart_add": [
            "add_to_cart",
            "update_food_cart",
            "update_instamart_cart",
            "create_cart",
        ],
        "cart_view": ["get_cart", "view_cart"],
        "checkout_link": ["get_checkout_url", "checkout_link", "checkout_cart"],
        "order_status": ["get_order_status", "track_order", "get_order_tracking_info"],
    }

    mapped: dict[str, str] = {}
    for cap, defaults in default_candidates.items():
        configured = cap_cfg.get(cap)
        candidates = (
            [str(x) for x in configured] if isinstance(configured, list) else list(defaults)
        )
        # 1. Exact candidate match
        for cand in candidates:
            if cand.lower() in forbidden_set or _FORBIDDEN_TOOL_PATTERNS.search(cand):
                continue
            if cand in tool_by_name:
                mapped[cap] = cand
                break
        if cap in mapped:
            continue

        # 2. Keyword description fallback (never matching forbidden tools)
        for t_name, t_obj in tool_by_name.items():
            if t_name.lower() in forbidden_set or _FORBIDDEN_TOOL_PATTERNS.search(t_name):
                continue
            desc = str(
                getattr(t_obj, "description", None)
                or (t_obj.get("description") if isinstance(t_obj, dict) else "")
                or ""
            ).lower()
            if cap == "search_products" and "product" in desc and "search" in desc:
                mapped[cap] = t_name
                break
            if cap == "search_restaurants" and "restaurant" in desc and "search" in desc:
                mapped[cap] = t_name
                break
            if cap == "cart_add" and "cart" in desc and ("add" in desc or "update" in desc):
                mapped[cap] = t_name
                break

    for req_cap in required_capabilities:
        if req_cap not in mapped:
            available = ", ".join(sorted(tool_by_name)) or "none"
            raise StoreContractError(
                f"Store '{store}' MCP contract failed: missing required capability '{req_cap}' "
                f"(available tools: {available})"
            )
        tool_obj = tool_by_name[mapped[req_cap]]
        schema = (
            getattr(tool_obj, "input_schema", None)
            or getattr(tool_obj, "inputSchema", None)
            or (
                (tool_obj.get("inputSchema") or tool_obj.get("input_schema"))
                if isinstance(tool_obj, dict)
                else None
            )
        )
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise StoreContractError(
                f"Store '{store}' MCP contract failed: tool '{mapped[req_cap]}' for capability "
                f"'{req_cap}' has invalid inputSchema"
            )

    return mapped


def assert_not_order_or_payment_tool(store: str, tool_name: str) -> None:
    """Enforce Ground Rule 7: never invoke any order-placing or payment tool."""
    cfg = get_store_config(store)
    cap_cfg = cfg.get("capabilities", {})
    forbidden_list = cap_cfg.get("forbidden_tools", []) if isinstance(cap_cfg, dict) else []
    forbidden_set = {str(x).lower() for x in forbidden_list}
    if tool_name.lower() in forbidden_set or _FORBIDDEN_TOOL_PATTERNS.search(tool_name):
        raise StoreSafetyError(
            f"Safety guard blocked forbidden order/payment tool '{tool_name}' on store '{store}'"
        )


def assert_store_payload_clean(
    arguments: Mapping[str, Any],
    *,
    forbidden_terms: Sequence[str] = (),
) -> None:
    """Enforce Section 25.4: household names, phones, diet data and briefs never go to any store."""
    serialized = json.dumps(arguments, ensure_ascii=False)
    if not assert_clean(serialized, forbidden_terms=forbidden_terms):
        raise StorePrivacyError(
            "Privacy guard blocked store MCP call: household PII or phone number detected"
        )


class McpStoreSession:
    """Connected MCP session with capability discovery and safety/privacy enforcement."""

    def __init__(
        self,
        store: str,
        client: mcp.Client,
        *,
        forbidden_terms: Sequence[str] = (),
        required_capabilities: Sequence[str] = (),
    ) -> None:
        self.store = store
        self._client = client
        self._forbidden_terms = tuple(forbidden_terms)
        self._required_capabilities = tuple(required_capabilities)
        self.capabilities: dict[str, str] = {}

    async def initialize(self) -> dict[str, str]:
        tools_res = await self._client.list_tools()
        self.capabilities = discover_store_capabilities(
            self.store,
            tools_res.tools,
            required_capabilities=self._required_capabilities,
        )
        return self.capabilities

    async def call_capability(
        self,
        capability: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not self.capabilities:
            await self.initialize()
        tool_name = self.capabilities.get(capability)
        if not tool_name:
            raise StoreContractError(
                f"Store '{self.store}' has no tool mapped for capability '{capability}'"
            )
        return await self.call_tool(tool_name, arguments, capability=capability)

    async def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        capability: str = "direct",
    ) -> dict[str, Any]:
        assert_not_order_or_payment_tool(self.store, tool_name)
        assert_store_payload_clean(arguments, forbidden_terms=self._forbidden_terms)

        # Log MCP call without payloads or tokens (AGENTS.md section 25.4)
        logger.info(
            "store.mcp.call",
            store=self.store,
            capability=capability,
            tool=tool_name,
        )
        res = await self._client.call_tool(tool_name, dict(arguments))
        for block in res.content:
            text_val = getattr(block, "text", None)
            if isinstance(text_val, str) and text_val.strip():
                try:
                    parsed = json.loads(text_val)
                    if isinstance(parsed, dict):
                        return parsed
                    return {"items": parsed}
                except json.JSONDecodeError:
                    return {"text": text_val}
        return {}


def _unwrap_store_error(exc: BaseExceptionGroup) -> StoreError | None:
    for sub in exc.exceptions:
        if isinstance(sub, StoreError):
            return sub
        if isinstance(sub, BaseExceptionGroup):
            nested = _unwrap_store_error(sub)
            if nested is not None:
                return nested
    return None


@asynccontextmanager
async def open_store_mcp_session(
    store: str,
    url: str,
    *,
    access_token: str | None = None,
    server_override: Any = None,
    forbidden_terms: Sequence[str] = (),
    required_capabilities: Sequence[str] = (),
) -> AsyncIterator[McpStoreSession]:
    """Open an MCP session over streamable HTTP (or an injected ``MCPServer`` in tests)."""
    try:
        if server_override is not None:
            async with mcp.Client(server_override) as client:
                sess = McpStoreSession(
                    store,
                    client,
                    forbidden_terms=forbidden_terms,
                    required_capabilities=required_capabilities,
                )
                await sess.initialize()
                yield sess
            return

        headers: dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with (
            httpx2.AsyncClient(headers=headers, timeout=15.0) as http_client,
            mcp.Client(streamable_http_client(url, http_client=http_client)) as client,
        ):
            sess = McpStoreSession(
                store,
                client,
                forbidden_terms=forbidden_terms,
                required_capabilities=required_capabilities,
            )
            await sess.initialize()
            yield sess
    except BaseExceptionGroup as eg:
        unwrapped = _unwrap_store_error(eg)
        if unwrapped is not None:
            raise unwrapped from eg
        raise
