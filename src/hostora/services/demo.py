from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from hostora.db.models import Event, EventLog, Fn, Household, Idempotency, Pass, Rsvp
from hostora.engine.codes import ALPHABET, make_link_token, random_key
from hostora.engine.passes import PassClaims, sign_pass
from hostora.errors import ProblemError
from hostora.realtime.broadcaster import Broadcaster
from hostora.schemas.common import Attending, EventType, GateMode, Language, Meal, RuleId
from hostora.schemas.events import (
    CreateEventInput,
    FunctionInput,
    HouseholdBatch,
    HouseholdInput,
    Venue,
)
from hostora.schemas.rsvp import IssuedPass, Meals, RsvpFunction, RsvpInput, RsvpResult
from hostora.security.crypto import ip_hash
from hostora.security.firebase import HostIdentity
from hostora.security.ratelimit import hit
from hostora.services.events import create_event
from hostora.services.households import add_households_batch

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.config import Settings

DEMO_EVENT_KEY = "DEM01"
DEMO_HOST = HostIdentity(uid="demo-host", name="Arjun & Sara")

_SURNAMES = [
    "Sharma",
    "Khan",
    "Iyer",
    "Fernandes",
    "Banerjee",
    "Gill",
    "Mehta",
    "Qureshi",
    "Nair",
    "Chatterjee",
    "Patel",
    "D'Souza",
    "Verma",
    "Siddiqui",
    "Rao",
]
_FIRST_NAMES = [
    "Aarav",
    "Zoya",
    "Rohan",
    "Ananya",
    "Kabir",
    "Meera",
    "Vikram",
    "Farah",
    "Dev",
    "Priya",
    "Imran",
    "Neha",
]


def _det_key(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(length))


async def seed_demo_event(
    session: AsyncSession,
    settings: Settings,
    broadcaster: Broadcaster | None = None,
) -> dict[str, Any]:  # Any: JSON response dict
    now = datetime.now(UTC)
    bcast = broadcaster or Broadcaster()

    async with session.begin():
        old_hhs = (
            await session.scalars(select(Household.id).where(Household.event_key == DEMO_EVENT_KEY))
        ).all()
        if old_hhs:
            scopes = [f"rsvp:{hid}" for hid in old_hhs]
            await session.execute(delete(Idempotency).where(Idempotency.scope.in_(scopes)))
        await session.execute(delete(EventLog).where(EventLog.event_key == DEMO_EVENT_KEY))
        await session.execute(delete(Event).where(Event.event_key == DEMO_EVENT_KEY))

    starts_lunch = (now + timedelta(days=5)).replace(hour=7, minute=30, second=0, microsecond=0)
    starts_reception = (now + timedelta(days=5)).replace(hour=14, minute=0, second=0, microsecond=0)

    created = await create_event(
        session,
        DEMO_HOST,
        CreateEventInput(
            title="Arjun & Sara — Reception (demo)",
            type=EventType.WEDDING,
            tradition="interfaith",
            venue=Venue(
                label="Banyan Grove Lawn, Bengaluru",
                lat=12.9716,
                lng=77.5946,
                pincode="560001",
            ),
            budget_inr=60000,
            functions=[
                FunctionInput(
                    name="Lunch",
                    starts_at=starts_lunch,
                    meal=Meal.LUNCH,
                    rules=[],
                    gate_mode=GateMode.OFF,
                ),
                FunctionInput(
                    name="Reception",
                    starts_at=starts_reception,
                    meal=Meal.DINNER,
                    rules=[RuleId.HALAL, RuleId.NO_BEEF],
                    gate_mode=GateMode.SOFT,
                ),
            ],
        ),
        now=now,
        fixed_event_key=DEMO_EVENT_KEY,
        is_demo=True,
    )

    fn_lunch_id = created.functions[0].id
    fn_reception_id = created.functions[1].id

    rng = random.Random(42)  # noqa: S311
    hh_inputs: list[HouseholdInput] = []
    for i in range(60):
        first = _FIRST_NAMES[i % len(_FIRST_NAMES)]
        last = _SURNAMES[(i // 2) % len(_SURNAMES)]
        seat_cap = rng.randint(1, 6)
        phone = f"+9100000{i:05d}" if i % 3 != 0 else None
        lang = Language.HI if i % 5 == 0 else Language.EN
        fn_ids = [fn_lunch_id, fn_reception_id] if i % 4 != 0 else [fn_reception_id]
        hh_inputs.append(
            HouseholdInput(
                display_name=f"{first} {last} Family #{i + 1}",
                phone=phone,
                seat_cap=seat_cap,
                function_ids=fn_ids,
                language=lang,
            )
        )

    created_hhs = await add_households_batch(
        session,
        DEMO_EVENT_KEY,
        DEMO_HOST,
        HouseholdBatch(households=hh_inputs),
        settings,
    )

    async with session.begin():
        rows = (
            await session.scalars(select(Household).where(Household.event_key == DEMO_EVENT_KEY))
        ).all()
        hh_by_id = {h.id: h for h in rows}
        hhs = [hh_by_id[str(c["id"])] for c in created_hhs]
        used_keys: set[str] = set()
        for hh in hhs:
            k = _det_key(rng, 5)
            while k in used_keys:
                k = _det_key(rng, 5)
            used_keys.add(k)
            hh.key = k

        replied_count = 42  # 70% of 60 -> 42 replied, 18 unreplied (≥ 10 unreplied for judges)
        signing_key_hex = settings.pass_signing_key.get_secret_value()
        rec_exp = int((starts_reception + timedelta(hours=12)).timestamp())

        for idx, hh in enumerate(hhs[:replied_count]):
            fn_ids = hh_inputs[idx].function_ids
            rsvp_fns: list[RsvpFunction] = []
            issued_passes: list[IssuedPass] = []

            for fid in fn_ids:
                roll = rng.random()
                if roll < 0.70:
                    attending = Attending.YES
                elif roll < 0.85:
                    attending = Attending.MAYBE
                else:
                    attending = Attending.NO

                if attending is Attending.NO:
                    rsvp_fns.append(
                        RsvpFunction(
                            function_id=fid,
                            attending=Attending.NO,
                            adults=0,
                            children=0,
                            meals=Meals(veg=0, jain=0, non_veg=0),
                        )
                    )
                else:
                    total_seats = rng.randint(1, hh.seat_cap)
                    children = 1 if total_seats >= 3 and rng.random() < 0.35 else 0
                    adults = total_seats - children
                    veg = rng.randint(0, total_seats)
                    rem = total_seats - veg
                    jain = rng.randint(0, rem) if rng.random() < 0.2 else 0
                    non_veg = rem - jain
                    rsvp_fns.append(
                        RsvpFunction(
                            function_id=fid,
                            attending=attending,
                            adults=adults,
                            children=children,
                            meals=Meals(veg=veg, jain=jain, non_veg=non_veg),
                        )
                    )

            for f in rsvp_fns:
                yes = f.attending is not Attending.NO
                seats = (f.adults + f.children) if yes else 0
                session.add(
                    Rsvp(
                        household_id=hh.id,
                        function_id=f.function_id,
                        status=f.attending.value,
                        adults=f.adults if yes else 0,
                        children=f.children if yes else 0,
                        meals=(f.meals if yes else Meals()).model_dump(),
                        dietary_rules=[],
                        allergies=[],
                        arrival_window=None,
                        version=1,
                        first_replied_at=now,
                        updated_at=now,
                    )
                )
                if yes and f.function_id == fn_reception_id:
                    pid = random_key(10)
                    ptok = sign_pass(
                        PassClaims(DEMO_EVENT_KEY, pid, seats, rec_exp),
                        signing_key_hex,
                    )
                    session.add(
                        Pass(
                            id=pid,
                            event_key=DEMO_EVENT_KEY,
                            household_id=hh.id,
                            function_id=fn_reception_id,
                            seats=seats,
                            token=ptok,
                            revoked=False,
                            issued_at=now,
                        )
                    )
                    issued_passes.append(
                        IssuedPass(
                            pass_id=pid,
                            function_id=fn_reception_id,
                            seats=seats,
                            token=ptok,
                        )
                    )

            body_in = RsvpInput(functions=rsvp_fns, language=Language(hh.language))
            req_hash = hashlib.sha256(body_in.model_dump_json(by_alias=True).encode()).hexdigest()
            res_out = RsvpResult(
                ok=True,
                functions=[f.function_id for f in rsvp_fns],
                passes=issued_passes,
            )
            session.add(
                EventLog(
                    event_key=DEMO_EVENT_KEY,
                    kind="rsvp.submitted",
                    payload={
                        "household_id": hh.id,
                        "functions": [
                            {
                                "function_id": f.function_id,
                                "status": f.attending.value,
                                "seats": 0
                                if f.attending is Attending.NO
                                else f.adults + f.children,
                            }
                            for f in rsvp_fns
                        ],
                    },
                )
            )
            session.add(
                Idempotency(
                    scope=f"rsvp:{hh.id}",
                    key=f"demo-seed-{idx:03d}",
                    request_hash=req_hash,
                    response=res_out.model_dump(mode="json", by_alias=True),
                )
            )

    await bcast.publish(DEMO_EVENT_KEY, "summary")

    origin = settings.public_origin.rstrip("/")
    hmac_secret = settings.token_hmac_secret.get_secret_value()
    unreplied = hhs[replied_count:]
    invite_links = [
        f"{origin}/i/{DEMO_EVENT_KEY}/{make_link_token(hmac_secret, DEMO_EVENT_KEY, h.key)}"
        for h in unreplied[:5]
    ]
    return {
        "eventKey": DEMO_EVENT_KEY,
        "dashboardUrl": f"{origin}/app/e/{DEMO_EVENT_KEY}",
        "functions": [{"id": f.id, "name": f.name} for f in created.functions],
        "inviteLinks": invite_links,
    }


async def pick_demo_join_url(
    session: AsyncSession,
    client_ip_addr: str,
    settings: Settings,
    now: datetime,
) -> str:
    if not settings.demo_mode:
        raise ProblemError(404, "Not found")

    hmac_secret = settings.token_hmac_secret.get_secret_value()
    hashed_ip = ip_hash(client_ip_addr, hmac_secret)
    await hit(session, f"demo_join:{hashed_ip}", limit=30, window_s=60, now=now)

    replied_hh_subq = (
        select(Rsvp.household_id)
        .join(Fn, Fn.id == Rsvp.function_id)
        .where(Fn.event_key == DEMO_EVENT_KEY)
    )
    unreplied = (
        await session.scalars(
            select(Household).where(
                Household.event_key == DEMO_EVENT_KEY,
                Household.id.not_in(replied_hh_subq),
            )
        )
    ).all()
    if not unreplied:
        unreplied = (
            await session.scalars(select(Household).where(Household.event_key == DEMO_EVENT_KEY))
        ).all()
    if not unreplied:
        raise ProblemError(404, "Demo event is not seeded")

    chosen = random.SystemRandom().choice(list(unreplied))
    token = make_link_token(hmac_secret, DEMO_EVENT_KEY, chosen.key)
    return f"/i/{DEMO_EVENT_KEY}/{token}"
