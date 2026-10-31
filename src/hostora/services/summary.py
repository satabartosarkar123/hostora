from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from hostora.db.models import Event
from hostora.engine.newsvendor import order_quantity
from hostora.engine.prediction import ReplySeats, predict_arrivals
from hostora.errors import ProblemError
from hostora.schemas.common import Meal
from hostora.schemas.summary import (
    EventSummary,
    FunctionSummary,
    MealCounts,
    PredictionOut,
    SeatCounts,
)
from hostora.services.learning import slider_to_cost_ratio

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_REPLIES_SQL = text("""
SELECT f.id AS function_id, f.name, f.meal, f.position,
  COUNT(i.household_id)                                                     AS invited,
  COUNT(r.household_id)                                                     AS replied,
  COALESCE(SUM(r.adults + r.children) FILTER (WHERE r.status = 'yes'), 0)   AS yes_seats,
  COALESCE(SUM(r.adults + r.children) FILTER (WHERE r.status = 'maybe'), 0) AS maybe_seats,
  COUNT(r.household_id) FILTER (WHERE r.status = 'no')                      AS no_households,
  COALESCE(SUM(h.seat_cap) FILTER (WHERE i.household_id IS NOT NULL AND r.status IS NULL), 0)
                                                                            AS no_reply_seats,
  COALESCE(SUM(r.children) FILTER (WHERE r.status = 'yes'), 0)              AS children,
  COALESCE(SUM((r.meals->>'veg')::int)     FILTER (WHERE r.status = 'yes'), 0) AS veg,
  COALESCE(SUM((r.meals->>'jain')::int)    FILTER (WHERE r.status = 'yes'), 0) AS jain,
  COALESCE(SUM((r.meals->>'non_veg')::int) FILTER (WHERE r.status = 'yes'), 0) AS non_veg
FROM fn f
LEFT JOIN invitation i ON i.function_id = f.id
LEFT JOIN household h  ON h.id = i.household_id
LEFT JOIN rsvp r       ON r.household_id = i.household_id AND r.function_id = f.id
WHERE f.event_key = :event_key
GROUP BY f.id
ORDER BY f.position
""")

_SCANS_SQL = text("""
WITH per_pass AS (
  SELECT s.function_id, s.pass_id, SUM(s.seats) AS scanned, MAX(p.seats) AS allowed
  FROM scan s LEFT JOIN pass p ON p.id = s.pass_id
  WHERE s.event_key = :event_key
  GROUP BY s.function_id, s.pass_id
)
SELECT function_id,
  COALESCE(SUM(scanned), 0)                                         AS arrivals,
  COUNT(*) FILTER (WHERE allowed IS NOT NULL AND scanned > allowed) AS possible_duplicates,
  COUNT(*) FILTER (WHERE allowed IS NULL)                           AS unknown_passes
FROM per_pass GROUP BY function_id
""")


async def get_event_summary(session: AsyncSession, event_key: str) -> EventSummary:
    event = (
        await session.execute(
            select(Event).where(Event.event_key == event_key, Event.purged_at.is_(None))
        )
    ).scalar_one_or_none()
    if event is None:
        raise ProblemError(404, "Event not found")

    reply_rows = (await session.execute(_REPLIES_SQL, {"event_key": event_key})).mappings().all()
    scan_rows = {
        str(row["function_id"]): row
        for row in (await session.execute(_SCANS_SQL, {"event_key": event_key})).mappings().all()
    }

    functions: list[FunctionSummary] = []
    for row in reply_rows:
        fn_id = str(row["function_id"])
        yes_seats = int(row["yes_seats"])
        maybe_seats = int(row["maybe_seats"])
        no_households = int(row["no_households"])
        no_reply_seats = int(row["no_reply_seats"])

        pred = predict_arrivals(
            ReplySeats(yes=yes_seats, maybe=maybe_seats, no_reply=no_reply_seats)
        )
        scan_info = scan_rows.get(fn_id)
        arrivals = int(scan_info["arrivals"]) if scan_info else 0
        possible_duplicates = int(scan_info["possible_duplicates"]) if scan_info else 0
        unknown_passes = int(scan_info["unknown_passes"]) if scan_info else 0

        functions.append(
            FunctionSummary(
                function_id=fn_id,
                name=str(row["name"]),
                meal=Meal(str(row["meal"])),
                invited_households=int(row["invited"]),
                replied_households=int(row["replied"]),
                seats=SeatCounts(
                    yes=yes_seats,
                    maybe=maybe_seats,
                    no_households=no_households,
                    no_reply=no_reply_seats,
                ),
                meals=MealCounts(
                    veg=int(row["veg"]),
                    jain=int(row["jain"]),
                    non_veg=int(row["non_veg"]),
                ),
                children=int(row["children"]),
                prediction=PredictionOut(
                    low=pred.low,
                    likely=pred.likely,
                    high=pred.high,
                    p80=pred.p80,
                ),
                recommended_plates=(
                    round(
                        order_quantity(
                            [pred.low + int((pred.high - pred.low) * (i / 49)) for i in range(50)],
                            *slider_to_cost_ratio(getattr(event, "short_waste_slider", 5)),
                        )
                    )
                    if pred.likely > 0
                    else pred.p80
                ),
                arrivals=arrivals,
                possible_duplicates=possible_duplicates,
                unknown_passes=unknown_passes,
            )
        )

    return EventSummary(
        event_key=event_key,
        title=event.title,
        functions=functions,
        updated_at=datetime.now(UTC),
    )
