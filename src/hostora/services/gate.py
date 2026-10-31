from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hostora.db.models import EventLog, Fn, Household, Pass, Scan
from hostora.errors import ProblemError
from hostora.schemas.common import GateMode
from hostora.schemas.gate import GateManifest, GateManifestPass, GateScanBatch
from hostora.security.session import require_event_access
from hostora.security.tokens import make_gate_token, verify_gate_token

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.config import Settings
    from hostora.realtime.broadcaster import Broadcaster
    from hostora.security.firebase import HostIdentity

_FN_SCAN_SUMMARY_SQL = text("""
WITH per_pass AS (
  SELECT s.pass_id, SUM(s.seats) AS scanned, MAX(p.seats) AS allowed
  FROM scan s LEFT JOIN pass p ON p.id = s.pass_id
  WHERE s.event_key = :event_key AND s.function_id = :function_id
  GROUP BY s.pass_id
)
SELECT
  COALESCE(SUM(scanned), 0)                                         AS arrivals,
  COUNT(*) FILTER (WHERE allowed IS NOT NULL AND scanned > allowed) AS possible_duplicates,
  COUNT(*) FILTER (WHERE allowed IS NULL)                           AS unknown_passes
FROM per_pass
""")


async def create_gate_link(
    session: AsyncSession,
    event_key: str,
    host: HostIdentity,
    *,
    function_id: str,
    hours: int,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, str]:
    ts = now or datetime.now(UTC)
    async with session.begin():
        await require_event_access(session, event_key, host, need="edit")
        fn = (
            await session.execute(select(Fn).where(Fn.id == function_id, Fn.event_key == event_key))
        ).scalar_one_or_none()
        if fn is None:
            raise ProblemError(422, "Function does not belong to this event", field="functionId")

    exp = int((ts + timedelta(hours=hours)).timestamp())
    secret = settings.token_hmac_secret.get_secret_value()
    gate_token = make_gate_token(secret, event_key, function_id, exp)
    origin = settings.public_origin.rstrip("/")
    return {"url": f"{origin}/gate/{event_key}?t={gate_token}"}


async def get_gate_manifest(
    session: AsyncSession,
    event_key: str,
    token: str,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> GateManifest:
    ts = int((now or datetime.now(UTC)).timestamp())
    secret = settings.token_hmac_secret.get_secret_value()
    function_id = verify_gate_token(secret, event_key, token, ts)

    async with session.begin():
        fn = (
            await session.execute(select(Fn).where(Fn.id == function_id, Fn.event_key == event_key))
        ).scalar_one_or_none()
        if fn is None:
            raise ProblemError(404, "Event not found")

        rows = (
            await session.execute(
                select(Pass, Household.display_name)
                .join(Household, Household.id == Pass.household_id)
                .where(
                    Pass.event_key == event_key,
                    Pass.function_id == function_id,
                    Pass.revoked.is_(False),
                )
                .order_by(Household.display_name, Pass.id)
            )
        ).all()

        passes = [
            GateManifestPass(
                pass_id=p.id,
                display_name=str(display_name),
                seats=p.seats,
            )
            for p, display_name in rows
        ]

        return GateManifest(
            event_key=event_key,
            function_id=fn.id,
            function_name=fn.name,
            gate_mode=GateMode(fn.gate_mode),
            public_key=settings.pass_public_key,
            passes=passes,
        )


async def submit_gate_scans(
    session: AsyncSession,
    broadcaster: Broadcaster,
    *,
    event_key: str,
    token: str,
    batch: GateScanBatch,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, Any]:  # Any: JSON response dict
    ts_dt = now or datetime.now(UTC)
    secret = settings.token_hmac_secret.get_secret_value()
    token_fn_id = verify_gate_token(secret, event_key, token, int(ts_dt.timestamp()))
    if batch.function_id != token_fn_id:
        raise ProblemError(422, "Function mismatch for gate token", field="functionId")

    accepted = 0
    ignored = 0

    async with session.begin():
        fn = (
            await session.execute(select(Fn).where(Fn.id == token_fn_id, Fn.event_key == event_key))
        ).scalar_one_or_none()
        if fn is None:
            raise ProblemError(404, "Event not found")

        for scan_in in batch.scans:
            scanned_at = datetime.fromtimestamp(scan_in.at / 1000.0, tz=UTC)
            stmt = (
                pg_insert(Scan)
                .values(
                    event_key=event_key,
                    device_id=scan_in.device_id,
                    seq=scan_in.seq,
                    pass_id=scan_in.pass_id,
                    function_id=token_fn_id,
                    seats=scan_in.seats,
                    scanned_at=scanned_at,
                    received_at=ts_dt,
                )
                .on_conflict_do_nothing(index_elements=[Scan.event_key, Scan.device_id, Scan.seq])
                .returning(Scan.seq)
            )
            inserted = (await session.execute(stmt)).scalar_one_or_none()
            if inserted is not None:
                accepted += 1
            else:
                ignored += 1

        if accepted > 0:
            session.add(
                EventLog(
                    event_key=event_key,
                    kind="scan.batch",
                    payload={
                        "function_id": token_fn_id,
                        "accepted": accepted,
                        "ignored": ignored,
                    },
                    created_at=ts_dt,
                )
            )

        summary_row = (
            (
                await session.execute(
                    _FN_SCAN_SUMMARY_SQL,
                    {"event_key": event_key, "function_id": token_fn_id},
                )
            )
            .mappings()
            .one()
        )
        arrivals = int(summary_row["arrivals"])
        possible_duplicates = int(summary_row["possible_duplicates"])

    if accepted > 0:
        await broadcaster.publish(event_key, "summary")

    return {
        "arrivals": arrivals,
        "possibleDuplicates": possible_duplicates,
        "accepted": accepted,
        "ignored": ignored,
    }
