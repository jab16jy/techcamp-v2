"""Tests for pure logbook and extension visit domain models and rules.

Docs: docs/03, docs/04, ADR-0013, D3-D5, D10.
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from techcamp.identity.domain.models import Role
from techcamp.logbook.domain.errors import (
    ForbiddenRoleError,
    InsufficientRoleError,
    InvalidEntryError,
    NaiveDatetimeError,
)
from techcamp.logbook.domain.models import (
    MAX_CLOCK_SKEW,
    LogbookEntryFields,
    LogbookKind,
    RejectReason,
    SyncEntity,
    SyncOp,
    SyncStatus,
    VisitTopic,
    decide_push,
    ensure_can_sync,
    ensure_valid_entry,
    ensure_valid_topics,
    is_clock_skewed,
)

_BOGOTA = timezone(timedelta(hours=-5))


def test_vocabularies_match_documented_values() -> None:
    """Vocabularies match docs/03, docs/04, and ADR-0013 exact string values."""
    assert {k.value for k in LogbookKind} == {
        "task",
        "input",
        "irrigation",
        "harvest",
        "observation",
        "cost",
    }
    assert "unknown_kind" not in [k.value for k in LogbookKind]

    assert {t.value for t in VisitTopic} == {
        "human_capacities",
        "social_capacities",
        "information_access",
        "natural_resources",
        "participation",
    }
    assert "other_topic" not in [t.value for t in VisitTopic]

    assert {e.value for e in SyncEntity} == {"logbook_entry", "extension_visit"}
    assert {o.value for o in SyncOp} == {"upsert", "delete"}
    assert {s.value for s in SyncStatus} == {
        "applied",
        "duplicate",
        "conflict_overwritten",
        "rejected",
    }
    assert {r.value for r in RejectReason} == {
        "clock_skew",
        "invalid",
        "not_found",
        "alert_plot_mismatch",
        "forbidden",
    }


def test_harvest_requires_yield_kg() -> None:
    """D10: harvest entries require yield_kg (docs/03 metric-fields table)."""
    valid = LogbookEntryFields(kind=LogbookKind.HARVEST, yield_kg=Decimal("120.5"))
    ensure_valid_entry(valid)
    assert valid.yield_kg == Decimal("120.5")

    invalid = LogbookEntryFields(kind=LogbookKind.HARVEST, yield_kg=None)
    with pytest.raises(InvalidEntryError, match="yield_kg"):
        ensure_valid_entry(invalid)


def test_irrigation_requires_irrigation_mm() -> None:
    """D10: irrigation entries require irrigation_mm."""
    valid = LogbookEntryFields(kind=LogbookKind.IRRIGATION, irrigation_mm=Decimal("15.0"))
    ensure_valid_entry(valid)
    assert valid.irrigation_mm == Decimal("15.0")

    invalid = LogbookEntryFields(kind=LogbookKind.IRRIGATION, irrigation_mm=None)
    with pytest.raises(InvalidEntryError, match="irrigation_mm"):
        ensure_valid_entry(invalid)


def test_task_requires_labor_days() -> None:
    """D10: task entries require labor_days."""
    valid = LogbookEntryFields(kind=LogbookKind.TASK, labor_days=Decimal("3.0"))
    ensure_valid_entry(valid)
    assert valid.labor_days == Decimal("3.0")

    invalid = LogbookEntryFields(kind=LogbookKind.TASK, labor_days=None)
    with pytest.raises(InvalidEntryError, match="labor_days"):
        ensure_valid_entry(invalid)


@pytest.mark.parametrize("kind", [LogbookKind.INPUT, LogbookKind.COST])
def test_input_and_cost_require_cost_cop(kind: LogbookKind) -> None:
    """D10: input and cost entries require cost_cop."""
    valid = LogbookEntryFields(kind=kind, cost_cop=Decimal("75000.00"))
    ensure_valid_entry(valid)
    assert valid.cost_cop == Decimal("75000.00")

    invalid = LogbookEntryFields(kind=kind, cost_cop=None)
    with pytest.raises(InvalidEntryError, match="cost_cop"):
        ensure_valid_entry(invalid)


def test_observation_requires_no_numeric_fields_and_allows_optional_losses() -> None:
    """D10, docs/03: observation has no mandatory fields; quantity/unit/cost_cop are optional."""
    minimal = LogbookEntryFields(kind=LogbookKind.OBSERVATION)
    ensure_valid_entry(minimal)
    assert minimal.cost_cop is None

    with_losses = LogbookEntryFields(
        kind=LogbookKind.OBSERVATION,
        quantity=Decimal("50.0"),
        unit="kg",
        cost_cop=Decimal("120000.00"),
    )
    ensure_valid_entry(with_losses)
    assert with_losses.cost_cop == Decimal("120000.00")

    # Observation cannot have harvest/task/irrigation exclusive fields
    with pytest.raises(InvalidEntryError, match="yield_kg"):
        ensure_valid_entry(
            LogbookEntryFields(kind=LogbookKind.OBSERVATION, yield_kg=Decimal("50.0"))
        )


@pytest.mark.parametrize(
    "other_kind",
    [
        LogbookKind.TASK,
        LogbookKind.INPUT,
        LogbookKind.IRRIGATION,
        LogbookKind.OBSERVATION,
        LogbookKind.COST,
    ],
)
def test_harvest_exclusive_fields_rejected_on_other_kinds(other_kind: LogbookKind) -> None:
    """D10: yield_kg, sold_kg, sale_price_cop_per_kg are harvest-only."""
    # Build baseline valid fields for each kind
    cost = Decimal("1000") if other_kind in (LogbookKind.INPUT, LogbookKind.COST) else None
    labor = Decimal("1") if other_kind == LogbookKind.TASK else None
    irrig = Decimal("10") if other_kind == LogbookKind.IRRIGATION else None

    # yield_kg forbidden
    with pytest.raises(InvalidEntryError, match="harvest-only|yield_kg"):
        ensure_valid_entry(
            LogbookEntryFields(
                kind=other_kind,
                yield_kg=Decimal("100.0"),
                cost_cop=cost,
                labor_days=labor,
                irrigation_mm=irrig,
            )
        )

    # sold_kg forbidden
    with pytest.raises(InvalidEntryError, match="harvest-only|sold_kg"):
        ensure_valid_entry(
            LogbookEntryFields(
                kind=other_kind,
                sold_kg=Decimal("50.0"),
                cost_cop=cost,
                labor_days=labor,
                irrigation_mm=irrig,
            )
        )

    # sale_price_cop_per_kg forbidden
    with pytest.raises(InvalidEntryError, match="harvest-only|sale_price_cop_per_kg"):
        ensure_valid_entry(
            LogbookEntryFields(
                kind=other_kind,
                sale_price_cop_per_kg=Decimal("2500.0"),
                cost_cop=cost,
                labor_days=labor,
                irrigation_mm=irrig,
            )
        )


@pytest.mark.parametrize(
    "other_kind",
    [
        LogbookKind.HARVEST,
        LogbookKind.INPUT,
        LogbookKind.IRRIGATION,
        LogbookKind.OBSERVATION,
        LogbookKind.COST,
    ],
)
def test_labor_days_rejected_on_non_task_kinds(other_kind: LogbookKind) -> None:
    """D10: labor_days is task-only."""
    cost = Decimal("1000") if other_kind in (LogbookKind.INPUT, LogbookKind.COST) else None
    yield_val = Decimal("100") if other_kind == LogbookKind.HARVEST else None
    irrig = Decimal("10") if other_kind == LogbookKind.IRRIGATION else None

    with pytest.raises(InvalidEntryError, match="task-only|labor_days"):
        ensure_valid_entry(
            LogbookEntryFields(
                kind=other_kind,
                labor_days=Decimal("2.0"),
                cost_cop=cost,
                yield_kg=yield_val,
                irrigation_mm=irrig,
            )
        )


@pytest.mark.parametrize(
    "other_kind",
    [
        LogbookKind.HARVEST,
        LogbookKind.TASK,
        LogbookKind.INPUT,
        LogbookKind.OBSERVATION,
        LogbookKind.COST,
    ],
)
def test_irrigation_mm_rejected_on_non_irrigation_kinds(other_kind: LogbookKind) -> None:
    """D10: irrigation_mm is irrigation-only."""
    cost = Decimal("1000") if other_kind in (LogbookKind.INPUT, LogbookKind.COST) else None
    yield_val = Decimal("100") if other_kind == LogbookKind.HARVEST else None
    labor = Decimal("1") if other_kind == LogbookKind.TASK else None

    with pytest.raises(InvalidEntryError, match="irrigation-only|irrigation_mm"):
        ensure_valid_entry(
            LogbookEntryFields(
                kind=other_kind,
                irrigation_mm=Decimal("15.0"),
                cost_cop=cost,
                yield_kg=yield_val,
                labor_days=labor,
            )
        )


def test_harvest_sold_kg_and_sale_price_must_be_provided_together() -> None:
    """D10: sold_kg and sale_price_cop_per_kg must be both present or both None."""
    # Both None -> valid
    valid_unsold = LogbookEntryFields(
        kind=LogbookKind.HARVEST,
        yield_kg=Decimal("100.0"),
        sold_kg=None,
        sale_price_cop_per_kg=None,
    )
    ensure_valid_entry(valid_unsold)
    assert valid_unsold.sold_kg is None

    # Both present -> valid
    valid_sold = LogbookEntryFields(
        kind=LogbookKind.HARVEST,
        yield_kg=Decimal("100.0"),
        sold_kg=Decimal("80.0"),
        sale_price_cop_per_kg=Decimal("3500.0"),
    )
    ensure_valid_entry(valid_sold)
    assert valid_sold.sold_kg == Decimal("80.0")

    # sold_kg without sale_price -> invalid
    with pytest.raises(InvalidEntryError, match="together"):
        ensure_valid_entry(
            LogbookEntryFields(
                kind=LogbookKind.HARVEST,
                yield_kg=Decimal("100.0"),
                sold_kg=Decimal("80.0"),
                sale_price_cop_per_kg=None,
            )
        )

    # sale_price without sold_kg -> invalid
    with pytest.raises(InvalidEntryError, match="together"):
        ensure_valid_entry(
            LogbookEntryFields(
                kind=LogbookKind.HARVEST,
                yield_kg=Decimal("100.0"),
                sold_kg=None,
                sale_price_cop_per_kg=Decimal("3500.0"),
            )
        )


def test_harvest_sold_kg_cannot_exceed_yield_kg() -> None:
    """D10: sold_kg <= yield_kg."""
    # sold == yield -> valid
    valid_equal = LogbookEntryFields(
        kind=LogbookKind.HARVEST,
        yield_kg=Decimal("100.0"),
        sold_kg=Decimal("100.0"),
        sale_price_cop_per_kg=Decimal("3000.0"),
    )
    ensure_valid_entry(valid_equal)
    assert valid_equal.sold_kg == valid_equal.yield_kg

    # sold < yield -> valid
    valid_less = LogbookEntryFields(
        kind=LogbookKind.HARVEST,
        yield_kg=Decimal("100.0"),
        sold_kg=Decimal("99.9"),
        sale_price_cop_per_kg=Decimal("3000.0"),
    )
    ensure_valid_entry(valid_less)
    assert valid_less.sold_kg < valid_less.yield_kg

    # sold > yield -> invalid
    invalid_excess = LogbookEntryFields(
        kind=LogbookKind.HARVEST,
        yield_kg=Decimal("100.0"),
        sold_kg=Decimal("100.1"),
        sale_price_cop_per_kg=Decimal("3000.0"),
    )
    with pytest.raises(InvalidEntryError, match="exceed"):
        ensure_valid_entry(invalid_excess)


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        ("quantity", {"kind": LogbookKind.OBSERVATION, "quantity": Decimal("-1.0")}),
        ("cost_cop", {"kind": LogbookKind.COST, "cost_cop": Decimal("-0.01")}),
        ("yield_kg", {"kind": LogbookKind.HARVEST, "yield_kg": Decimal("-10.0")}),
        (
            "sold_kg",
            {
                "kind": LogbookKind.HARVEST,
                "yield_kg": Decimal("100.0"),
                "sold_kg": Decimal("-5.0"),
                "sale_price_cop_per_kg": Decimal("2000.0"),
            },
        ),
        (
            "sale_price_cop_per_kg",
            {
                "kind": LogbookKind.HARVEST,
                "yield_kg": Decimal("100.0"),
                "sold_kg": Decimal("50.0"),
                "sale_price_cop_per_kg": Decimal("-1.0"),
            },
        ),
        ("labor_days", {"kind": LogbookKind.TASK, "labor_days": Decimal("-0.5")}),
        ("irrigation_mm", {"kind": LogbookKind.IRRIGATION, "irrigation_mm": Decimal("-5.0")}),
    ],
)
def test_numeric_amounts_must_be_non_negative(field_name: str, kwargs: dict) -> None:
    """D10: all numeric amount fields must be non-negative (>= 0)."""
    invalid = LogbookEntryFields(**kwargs)
    with pytest.raises(InvalidEntryError, match=f"{field_name}.*non-negative"):
        ensure_valid_entry(invalid)

    # Positive and zero are allowed
    zero_kwargs = dict(kwargs)
    zero_kwargs[field_name] = Decimal("0")
    valid_zero = LogbookEntryFields(**zero_kwargs)
    ensure_valid_entry(valid_zero)


def test_ensure_valid_topics_validates_vocabulary() -> None:
    """D5, docs/03 §extension_visit: all topics must be in the VisitTopic vocabulary."""
    valid_topics = [
        VisitTopic.HUMAN_CAPACITIES,
        "social_capacities",
        "information_access",
        "natural_resources",
        "participation",
    ]
    ensure_valid_topics(valid_topics)

    # Empty list is accepted
    ensure_valid_topics([])

    # Unknown string raises InvalidEntryError
    with pytest.raises(InvalidEntryError, match="Unknown visit topic|Invalid visit topic"):
        ensure_valid_topics(["human_capacities", "invalid_topic"])


def test_decide_push_when_stored_is_none() -> None:
    """D4: new entity (stored_client_updated_at is None) -> APPLIED."""
    now = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    status = decide_push(stored_client_updated_at=None, incoming_client_updated_at=now)
    assert status == SyncStatus.APPLIED
    assert status != SyncStatus.DUPLICATE


def test_decide_push_when_timestamps_are_equal() -> None:
    """D4: same client_updated_at -> DUPLICATE (idempotent retry)."""
    ts = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    status = decide_push(stored_client_updated_at=ts, incoming_client_updated_at=ts)
    assert status == SyncStatus.DUPLICATE
    assert status != SyncStatus.APPLIED


def test_decide_push_when_incoming_is_newer() -> None:
    """D4: incoming newer than stored -> APPLIED (LWW)."""
    stored_ts = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    incoming_ts = stored_ts + timedelta(seconds=1)
    status = decide_push(
        stored_client_updated_at=stored_ts,
        incoming_client_updated_at=incoming_ts,
    )
    assert status == SyncStatus.APPLIED
    assert status != SyncStatus.CONFLICT_OVERWRITTEN


def test_decide_push_when_incoming_is_older() -> None:
    """D4: incoming older than stored -> CONFLICT_OVERWRITTEN."""
    stored_ts = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    incoming_ts = stored_ts - timedelta(seconds=1)
    status = decide_push(
        stored_client_updated_at=stored_ts,
        incoming_client_updated_at=incoming_ts,
    )
    assert status == SyncStatus.CONFLICT_OVERWRITTEN
    assert status != SyncStatus.APPLIED


def test_decide_push_compares_different_timezone_offsets_accurately() -> None:
    """D4: timezone-aware timestamps with different offsets are compared accurately."""
    # 14:00 UTC == 09:00 Bogota (UTC-5)
    ts_utc = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    ts_bogota = datetime(2026, 9, 28, 9, 0, tzinfo=_BOGOTA)
    status = decide_push(
        stored_client_updated_at=ts_utc,
        incoming_client_updated_at=ts_bogota,
    )
    assert status == SyncStatus.DUPLICATE


def test_decide_push_raises_on_naive_datetimes() -> None:
    """D4: datetimes must be timezone-aware (naive -> raise NaiveDatetimeError/ValueError)."""
    aware = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    naive = datetime(2026, 9, 28, 14, 0)

    with pytest.raises((NaiveDatetimeError, ValueError), match="timezone-aware"):
        decide_push(stored_client_updated_at=aware, incoming_client_updated_at=naive)

    with pytest.raises((NaiveDatetimeError, ValueError), match="timezone-aware"):
        decide_push(stored_client_updated_at=naive, incoming_client_updated_at=aware)


def test_is_clock_skewed_24h_boundary() -> None:
    """D5: strictly > 24 h ahead is skewed; exactly 24 h is NOT skewed."""
    now = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)

    # Exactly 24 h ahead -> not skewed
    at_24h = now + MAX_CLOCK_SKEW
    assert is_clock_skewed(at_24h, now) is False

    # 24 h + 1 second ahead -> skewed
    past_24h = now + MAX_CLOCK_SKEW + timedelta(seconds=1)
    assert is_clock_skewed(past_24h, now) is True

    # Less than 24 h ahead -> not skewed
    under_24h = now + timedelta(hours=12)
    assert is_clock_skewed(under_24h, now) is False

    # In the past -> not skewed
    in_past = now - timedelta(hours=2)
    assert is_clock_skewed(in_past, now) is False


def test_is_clock_skewed_raises_on_naive_datetimes() -> None:
    """D5: naive datetimes in is_clock_skewed raise NaiveDatetimeError / ValueError."""
    aware = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    naive = datetime(2026, 9, 28, 12, 0)

    with pytest.raises((NaiveDatetimeError, ValueError), match="timezone-aware"):
        is_clock_skewed(naive, aware)

    with pytest.raises((NaiveDatetimeError, ValueError), match="timezone-aware"):
        is_clock_skewed(aware, naive)


def test_ensure_can_sync_logbook_entry_roles() -> None:
    """D3: logbook_entry allows owner, technician, producer; rejects viewer."""
    # Allowed roles
    for role in [Role.OWNER, Role.TECHNICIAN, Role.PRODUCER]:
        ensure_can_sync(SyncEntity.LOGBOOK_ENTRY, role)

    # Viewer rejected
    with pytest.raises((InsufficientRoleError, ForbiddenRoleError)):
        ensure_can_sync(SyncEntity.LOGBOOK_ENTRY, Role.VIEWER)


def test_ensure_can_sync_extension_visit_roles() -> None:
    """D3: extension_visit allows technician only; rejects owner, producer, viewer."""
    # Technician allowed
    ensure_can_sync(SyncEntity.EXTENSION_VISIT, Role.TECHNICIAN)

    # Other roles rejected
    for role in [Role.OWNER, Role.PRODUCER, Role.VIEWER]:
        with pytest.raises((InsufficientRoleError, ForbiddenRoleError)):
            ensure_can_sync(SyncEntity.EXTENSION_VISIT, role)


def test_numeric_amounts_reject_non_finite_values() -> None:
    """R3 (issue #143): non-finite Decimal values (NaN, Infinity) raise InvalidEntryError."""
    # NaN rejected
    nan_fields = LogbookEntryFields(kind=LogbookKind.HARVEST, yield_kg=Decimal("NaN"))
    with pytest.raises(InvalidEntryError, match="finite|NaN"):
        ensure_valid_entry(nan_fields)

    # Infinity rejected
    inf_fields = LogbookEntryFields(kind=LogbookKind.HARVEST, yield_kg=Decimal("Infinity"))
    with pytest.raises(InvalidEntryError, match="finite|Infinity"):
        ensure_valid_entry(inf_fields)

    # -Infinity rejected
    neg_inf_fields = LogbookEntryFields(kind=LogbookKind.HARVEST, yield_kg=Decimal("-Infinity"))
    with pytest.raises(InvalidEntryError, match="finite|Infinity"):
        ensure_valid_entry(neg_inf_fields)

    # Negative assertion: finite value is accepted
    finite_fields = LogbookEntryFields(kind=LogbookKind.HARVEST, yield_kg=Decimal("100.0"))
    ensure_valid_entry(finite_fields)
    assert finite_fields.yield_kg is not None and finite_fields.yield_kg.is_finite()


def test_ensure_can_sync_refuses_unknown_entity() -> None:
    """R3 (issue #143): an entity outside the mapping fails closed via KeyError, never allowed."""
    with pytest.raises(KeyError):
        ensure_can_sync("unknown_entity", Role.OWNER)  # type: ignore[arg-type]
