from __future__ import annotations

from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Host(Base):
    __tablename__ = "host"

    uid: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Event(Base):
    __tablename__ = "event"

    event_key: Mapped[str] = mapped_column(CHAR(5), primary_key=True)
    host_uid: Mapped[str] = mapped_column(
        Text, ForeignKey("host.uid", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    tradition: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'none'"))
    venue_label: Mapped[str] = mapped_column(Text, nullable=False)
    venue_lat: Mapped[float] = mapped_column(Double, nullable=False)
    venue_lng: Mapped[float] = mapped_column(Double, nullable=False)
    pincode: Mapped[str | None] = mapped_column(CHAR(6), nullable=True)
    budget_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    short_waste_slider: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("budget_inr >= 0", name="event_budget_inr_check"),
        CheckConstraint(
            "short_waste_slider >= 1 AND short_waste_slider <= 10",
            name="event_short_waste_slider_check",
        ),
        Index("event_host_idx", host_uid, created_at.desc()),
        Index("event_purge_idx", purge_after, postgresql_where=text("purged_at IS NULL")),
    )


class Cohost(Base):
    __tablename__ = "cohost"
    __table_args__ = (CheckConstraint("role IN ('edit','view')", name="cohost_role_check"),)

    event_key: Mapped[str] = mapped_column(
        CHAR(5), ForeignKey("event.event_key", ondelete="CASCADE"), primary_key=True
    )
    uid: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)


class Fn(Base):
    __tablename__ = "fn"
    __table_args__ = (
        CheckConstraint("meal IN ('lunch','dinner','snacks')", name="fn_meal_check"),
        CheckConstraint("gate_mode IN ('off','soft','strict')", name="fn_gate_mode_check"),
        Index("fn_event_idx", "event_key", "position"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_key: Mapped[str] = mapped_column(
        CHAR(5), ForeignKey("event.event_key", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meal: Mapped[str] = mapped_column(Text, nullable=False)
    rules: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    gate_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'off'"))
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class Household(Base):
    __tablename__ = "household"
    __table_args__ = (
        CheckConstraint("seat_cap BETWEEN 1 AND 20", name="household_seat_cap_check"),
        UniqueConstraint("event_key", "key", name="household_event_key_key_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_key: Mapped[str] = mapped_column(
        CHAR(5), ForeignKey("event.event_key", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(CHAR(5), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    seat_cap: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'en'"))
    invite_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Invitation(Base):
    __tablename__ = "invitation"
    __table_args__ = (Index("invitation_fn_idx", "function_id"),)

    household_id: Mapped[str] = mapped_column(
        Text, ForeignKey("household.id", ondelete="CASCADE"), primary_key=True
    )
    function_id: Mapped[str] = mapped_column(
        Text, ForeignKey("fn.id", ondelete="CASCADE"), primary_key=True
    )


class Rsvp(Base):
    __tablename__ = "rsvp"
    __table_args__ = (
        CheckConstraint("status IN ('yes','no','maybe')", name="rsvp_status_check"),
        CheckConstraint("adults >= 0", name="rsvp_adults_check"),
        CheckConstraint("children >= 0", name="rsvp_children_check"),
        Index("rsvp_fn_idx", "function_id"),
    )

    household_id: Mapped[str] = mapped_column(
        Text, ForeignKey("household.id", ondelete="CASCADE"), primary_key=True
    )
    function_id: Mapped[str] = mapped_column(
        Text, ForeignKey("fn.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    adults: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    children: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    meals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # Any: JSONB dict
    dietary_rules: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    allergies: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    arrival_window: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_replied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class Idempotency(Base):
    __tablename__ = "idempotency"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # Any: JSONB payload
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Pass(Base):
    __tablename__ = "pass"
    __table_args__ = (
        CheckConstraint("seats >= 1", name="pass_seats_check"),
        UniqueConstraint("household_id", "function_id", name="pass_household_id_function_id_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_key: Mapped[str] = mapped_column(
        CHAR(5), ForeignKey("event.event_key", ondelete="CASCADE"), nullable=False
    )
    household_id: Mapped[str] = mapped_column(
        Text, ForeignKey("household.id", ondelete="CASCADE"), nullable=False
    )
    function_id: Mapped[str] = mapped_column(
        Text, ForeignKey("fn.id", ondelete="CASCADE"), nullable=False
    )
    seats: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Scan(Base):
    __tablename__ = "scan"
    __table_args__ = (
        CheckConstraint("seats >= 1", name="scan_seats_check"),
        Index("scan_fn_pass_idx", "function_id", "pass_id"),
    )

    event_key: Mapped[str] = mapped_column(
        CHAR(5), ForeignKey("event.event_key", ondelete="CASCADE"), primary_key=True
    )
    device_id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    pass_id: Mapped[str] = mapped_column(Text, nullable=False)
    function_id: Mapped[str] = mapped_column(
        Text, ForeignKey("fn.id", ondelete="CASCADE"), nullable=False
    )
    seats: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlanModel(Base):
    __tablename__ = "plan"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    function_id: Mapped[str] = mapped_column(
        Text, ForeignKey("fn.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # Any: JSONB
    body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # Any: JSONB
    route: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_version: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('running','done','failed')", name="plan_status_check"),
        Index("plan_fn_idx", function_id, created_at.desc()),
    )


class Purchase(Base):
    __tablename__ = "purchase"

    plan_id: Mapped[str] = mapped_column(
        Text, ForeignKey("plan.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    bought: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    actual_inr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Outcome(Base):
    __tablename__ = "outcome"
    __table_args__ = (CheckConstraint("actual_count >= 0", name="outcome_actual_count_check"),)

    function_id: Mapped[str] = mapped_column(
        Text, ForeignKey("fn.id", ondelete="CASCADE"), primary_key=True
    )
    actual_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ran_short: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    leftover: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventLog(Base):
    __tablename__ = "event_log"
    __table_args__ = (Index("event_log_event_idx", "event_key", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_key: Mapped[str] = mapped_column(CHAR(5), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # Any: JSONB
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Attempt(Base):
    __tablename__ = "attempt"

    bucket: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class LlmCache(Base):
    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeConcept(Base):
    __tablename__ = "knowledge_concept"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    parents: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeForm(Base):
    __tablename__ = "knowledge_form"

    form_norm: Mapped[str] = mapped_column(Text, primary_key=True)
    concept_id: Mapped[str] = mapped_column(
        Text, ForeignKey("knowledge_concept.id", ondelete="CASCADE"), primary_key=True
    )
    lang: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeFact(Base):
    __tablename__ = "knowledge_fact"
    __table_args__ = (Index("knowledge_fact_subject_idx", "subject_norm", "kind"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    subject_norm: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    citation: Mapped[str] = mapped_column(Text, nullable=False)
    host_uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServingKind(Base):
    __tablename__ = "serving_kind"
    __table_args__ = (
        CheckConstraint("unit IN ('g','ml','piece')", name="serving_kind_unit_check"),
        CheckConstraint(
            "created_by IN ('model','host','system')", name="serving_kind_created_by_check"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    course: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    pack_sizes: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    per_guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meal_factor: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)


class PortionPrior(Base):
    __tablename__ = "portion_prior"

    serving_kind: Mapped[str] = mapped_column(Text, ForeignKey("serving_kind.id"), primary_key=True)
    region: Mapped[str] = mapped_column(Text, primary_key=True)
    per_adult: Mapped[float] = mapped_column(Double, nullable=False)
    per_child: Mapped[float] = mapped_column(Double, nullable=False)
    weight: Mapped[float] = mapped_column(Double, nullable=False, server_default=text("5"))
    citations: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PortionOutcome(Base):
    __tablename__ = "portion_outcome"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('ran_short','about_right','leftover')",
            name="portion_outcome_outcome_check",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    function_id: Mapped[str] = mapped_column(Text, nullable=False)
    serving_kind: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    planned_per_head: Mapped[float] = mapped_column(Double, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    leftover_fraction: Mapped[float] = mapped_column(
        Double, nullable=False, server_default=text("0")
    )
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PriceObservation(Base):
    __tablename__ = "price_observation"
    __table_args__ = (
        Index("price_observation_idx", "item_norm", "region", text("observed_on DESC")),
        CheckConstraint("inr_per_unit >= 0", name="price_observation_inr_nonneg"),
        CheckConstraint(
            "source IN ('host_reported','caterer_quote','live')",
            name="price_observation_source_check",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    item_norm: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    inr_per_unit: Mapped[float] = mapped_column(Double, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)


class GroundingPolicy(Base):
    __tablename__ = "grounding_policy"
    __table_args__ = (CheckConstraint("id = 1", name="grounding_policy_singleton_check"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text("1"))
    quorum: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    min_agreement: Mapped[float] = mapped_column(Double, nullable=False)
    alpha: Mapped[float] = mapped_column(Double, nullable=False)
    n_calibration: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_bound: Mapped[float] = mapped_column(Double, nullable=False)
    calibrated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TurnoutObservation(Base):
    __tablename__ = "turnout_observation"
    __table_args__ = (
        Index("turnout_obs_segment_idx", "state", "event_type", "function_kind", "reply_bucket"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    function_id: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    function_kind: Mapped[str] = mapped_column(Text, nullable=False)
    reply_bucket: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    invited_households: Mapped[int] = mapped_column(Integer, nullable=False)
    arrived_households: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Double, nullable=False, server_default=text("1.0"))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TurnoutModel(Base):
    __tablename__ = "turnout_model"
    __table_args__ = (
        UniqueConstraint("segment_key", "status", name="uq_turnout_model_segment_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    segment_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    a: Mapped[float] = mapped_column(Double, nullable=False)
    b: Mapped[float] = mapped_column(Double, nullable=False)
    strength: Mapped[float] = mapped_column(Double, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PortionModel(Base):
    __tablename__ = "portion_model"
    __table_args__ = (
        UniqueConstraint("serving_kind", "region", name="uq_portion_model_kind_region"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    serving_kind: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    median: Mapped[float] = mapped_column(Double, nullable=False)
    p10: Mapped[float] = mapped_column(Double, nullable=False)
    p90: Mapped[float] = mapped_column(Double, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ElasticityModel(Base):
    __tablename__ = "elasticity_model"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    course: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    mean: Mapped[float] = mapped_column(Double, nullable=False)
    sd: Mapped[float] = mapped_column(Double, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChannelStat(Base):
    __tablename__ = "channel_stat"
    __table_args__ = (
        UniqueConstraint("context_key", "channel", name="uq_channel_stat_context_channel"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    context_key: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    successes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChannelHealth(Base):
    __tablename__ = "channel_health"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    channel: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PreferenceComparison(Base):
    __tablename__ = "preference_comparison"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_key: Mapped[str | None] = mapped_column(CHAR(5), nullable=True)
    winner: Mapped[str] = mapped_column(Text, nullable=False)
    loser: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FunctionPattern(Base):
    __tablename__ = "function_pattern"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    traditions: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    structure: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EventRule(Base):
    __tablename__ = "event_rule"
    __table_args__ = (Index("event_rule_event_fn_idx", "event_key", "function_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_key: Mapped[str] = mapped_column(
        CHAR(5), ForeignKey("event.event_key", ondelete="CASCADE"), nullable=False
    )
    function_id: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    forbid: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    confirm: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UiTranslation(Base):
    __tablename__ = "ui_translation"
    __table_args__ = (UniqueConstraint("lang", "key", name="uq_ui_translation_lang_key"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    lang: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    agreement: Mapped[float] = mapped_column(Double, nullable=False, server_default=sa.text("1.0"))
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TuningValue(Base):
    __tablename__ = "tuning_value"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    value: Mapped[float] = mapped_column(Double, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OsmCache(Base):
    __tablename__ = "osm_cache"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    cache_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
