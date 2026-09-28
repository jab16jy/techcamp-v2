"""Pure domain models, vocabularies, and rules for offline logbook and extension visits.

Docs: docs/03 §logbook_entry & §extension_visit; docs/04 §Bitácora; docs/06 §7; ADR-0013.
Feature decisions D3, D4, D5, D10.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from techcamp.identity.domain.models import Role
from techcamp.logbook.domain.errors import (
    InsufficientRoleError,
    InvalidEntryError,
    NaiveDatetimeError,
)


class LogbookKind(StrEnum):
    """Kinds of field logbook entries (docs/03:221)."""

    TASK = "task"
    INPUT = "input"
    IRRIGATION = "irrigation"
    HARVEST = "harvest"
    OBSERVATION = "observation"
    COST = "cost"


class VisitTopic(StrEnum):
    """The five extension visit topics defined by Ley 1876 art. 25 (docs/03:234-241)."""

    HUMAN_CAPACITIES = "human_capacities"
    SOCIAL_CAPACITIES = "social_capacities"
    INFORMATION_ACCESS = "information_access"
    NATURAL_RESOURCES = "natural_resources"
    PARTICIPATION = "participation"


class SyncEntity(StrEnum):
    """Entities synchronized through offline sync push/pull (docs/04 §Bitácora, ADR-0013)."""

    LOGBOOK_ENTRY = "logbook_entry"
    EXTENSION_VISIT = "extension_visit"


class SyncOp(StrEnum):
    """Sync operations (docs/04 §Bitácora)."""

    UPSERT = "upsert"
    DELETE = "delete"


class SyncStatus(StrEnum):
    """Per-change synchronization outcomes (docs/04 §Bitácora, docs/06 §7, D4)."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    CONFLICT_OVERWRITTEN = "conflict_overwritten"
    REJECTED = "rejected"


class RejectReason(StrEnum):
    """Stable error codes returned when a change is rejected (docs/04 §Bitácora, D5)."""

    CLOCK_SKEW = "clock_skew"
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    ALERT_PLOT_MISMATCH = "alert_plot_mismatch"
    FORBIDDEN = "forbidden"


MAX_CLOCK_SKEW: timedelta = timedelta(hours=24)
"""D5: client timestamp strictly > 24 h in the future is rejected as clock_skew."""

_WRITE_ROLES: dict[SyncEntity, frozenset[Role]] = {
    SyncEntity.LOGBOOK_ENTRY: frozenset(
        {
            Role.OWNER,
            Role.TECHNICIAN,
            Role.PRODUCER,
        }
    ),
    SyncEntity.EXTENSION_VISIT: frozenset(
        {
            Role.TECHNICIAN,
        }
    ),
}
"""D3: roles permitted to push per synchronized entity."""

_VALID_TOPICS: frozenset[str] = frozenset(t.value for t in VisitTopic)

_NUMERIC_AMOUNT_FIELDS: tuple[str, ...] = (
    "quantity",
    "cost_cop",
    "yield_kg",
    "sold_kg",
    "sale_price_cop_per_kg",
    "labor_days",
    "irrigation_mm",
)


@dataclass(frozen=True, slots=True)
class LogbookEntryFields:
    """Typed and numeric fields of a logbook entry (docs/03 §logbook_entry, D10)."""

    kind: LogbookKind
    quantity: Decimal | None = None
    unit: str | None = None
    cost_cop: Decimal | None = None
    yield_kg: Decimal | None = None
    sold_kg: Decimal | None = None
    sale_price_cop_per_kg: Decimal | None = None
    labor_days: Decimal | None = None
    irrigation_mm: Decimal | None = None


def ensure_valid_entry(fields: LogbookEntryFields) -> None:
    """Validate logbook entry fields against D10 and docs/03 §logbook_entry rules.

    Enforces:
    - Non-negative numeric amounts.
    - Required fields per kind:
        harvest => yield_kg
        irrigation => irrigation_mm
        task => labor_days
        input/cost => cost_cop
    - Exclusive fields:
        yield_kg, sold_kg, sale_price_cop_per_kg are harvest-only
        labor_days is task-only
        irrigation_mm is irrigation-only
    - Sold kg and sale price together: both present or both None.
    - Sold kg <= yield kg for harvest entries.
    """
    # 1. Non-negative amounts
    for name in _NUMERIC_AMOUNT_FIELDS:
        val: Decimal | None = getattr(fields, name)
        if val is not None:
            if not val.is_finite():
                raise InvalidEntryError(f"{name} must be a finite number (got {val})")
            if val < 0:
                raise InvalidEntryError(f"{name} must be non-negative (got {val})")

    # 2. Exclusive fields
    if fields.kind != LogbookKind.HARVEST:
        if (
            fields.yield_kg is not None
            or fields.sold_kg is not None
            or fields.sale_price_cop_per_kg is not None
        ):
            raise InvalidEntryError(
                "yield_kg, sold_kg, and sale_price_cop_per_kg are harvest-only, "
                f"not allowed for '{fields.kind.value}'"
            )

    if fields.kind != LogbookKind.TASK:
        if fields.labor_days is not None:
            raise InvalidEntryError(
                f"labor_days is task-only, not allowed for '{fields.kind.value}'"
            )

    if fields.kind != LogbookKind.IRRIGATION:
        if fields.irrigation_mm is not None:
            raise InvalidEntryError(
                f"irrigation_mm is irrigation-only, not allowed for '{fields.kind.value}'"
            )

    # 3. Required fields per kind
    if fields.kind == LogbookKind.HARVEST:
        if fields.yield_kg is None:
            raise InvalidEntryError("harvest entries require yield_kg")
    elif fields.kind == LogbookKind.IRRIGATION:
        if fields.irrigation_mm is None:
            raise InvalidEntryError("irrigation entries require irrigation_mm")
    elif fields.kind == LogbookKind.TASK:
        if fields.labor_days is None:
            raise InvalidEntryError("task entries require labor_days")
    elif fields.kind in (LogbookKind.INPUT, LogbookKind.COST):
        if fields.cost_cop is None:
            raise InvalidEntryError(f"{fields.kind.value} entries require cost_cop")

    # 4. Sold + price together (in harvest)
    if fields.kind == LogbookKind.HARVEST:
        if (fields.sold_kg is None) != (fields.sale_price_cop_per_kg is None):
            raise InvalidEntryError("sold_kg and sale_price_cop_per_kg must be provided together")

        # 5. Sold <= yield
        if (
            fields.sold_kg is not None
            and fields.yield_kg is not None
            and fields.sold_kg > fields.yield_kg
        ):
            raise InvalidEntryError(
                f"sold_kg ({fields.sold_kg}) cannot exceed yield_kg ({fields.yield_kg})"
            )


def ensure_valid_topics(topics: Sequence[str]) -> None:
    """Validate that every topic in the sequence belongs to VisitTopic (docs/03:245, D5)."""
    for topic in topics:
        if topic not in _VALID_TOPICS:
            raise InvalidEntryError(f"Invalid visit topic: '{topic}'")


def _require_tz_aware(dt: datetime, name: str) -> None:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise NaiveDatetimeError(f"{name} must be timezone-aware (got naive datetime: {dt})")


def decide_push(
    stored_client_updated_at: datetime | None,
    incoming_client_updated_at: datetime,
) -> SyncStatus:
    """Decide sync outcome for an incoming change (D4, ADR-0013).

    None stored -> applied;
    equal -> duplicate (idempotent);
    incoming newer -> applied (last-write-wins);
    incoming older -> conflict_overwritten.
    """
    _require_tz_aware(incoming_client_updated_at, "incoming_client_updated_at")
    if stored_client_updated_at is None:
        return SyncStatus.APPLIED

    _require_tz_aware(stored_client_updated_at, "stored_client_updated_at")

    if incoming_client_updated_at == stored_client_updated_at:
        return SyncStatus.DUPLICATE
    if incoming_client_updated_at > stored_client_updated_at:
        return SyncStatus.APPLIED
    return SyncStatus.CONFLICT_OVERWRITTEN


def is_clock_skewed(client_updated_at: datetime, now: datetime) -> bool:
    """Return True if client_updated_at is strictly more than 24 h ahead of now (D5)."""
    _require_tz_aware(client_updated_at, "client_updated_at")
    _require_tz_aware(now, "now")
    return client_updated_at > now + MAX_CLOCK_SKEW


def ensure_can_sync(entity: SyncEntity, role: Role) -> None:
    """Validate that the member role is authorized to sync the entity (D3, docs/04 §Bitácora)."""
    if role not in _WRITE_ROLES.get(entity, frozenset()):
        raise InsufficientRoleError(role, entity)
