"""Logbook and extension visit schema behavior (docs/03:216-271; docs/06 §7; D1, D6, D10)."""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.logbook.adapters.orm import AttachmentRow, ExtensionVisitRow, LogbookEntryRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


async def _create_test_context(
    db_session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, Callable[..., LogbookEntryRow]]:
    """Create test organization, farm, plot, user, and logbook entry factory."""
    org_id, user_id, farm_id, plot_id = uuid7(), uuid7(), uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Org Test Logbook", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone="+573001234567", full_name="Technician Test"))
    await db_session.flush()
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Logbook",
            municipality_code="47001",
            location=_POINT,
            technician_id=user_id,
        )
    )
    await db_session.flush()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote Logbook",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await db_session.commit()

    def make_entry(**overrides: Any) -> LogbookEntryRow:
        return LogbookEntryRow(
            id=uuid7(),
            org_id=org_id,
            plot_id=plot_id,
            crop_cycle_id=None,
            occurred_on=datetime.date(2026, 9, 28),
            created_by=user_id,
            created_offline=False,
            client_updated_at=datetime.datetime.now(datetime.UTC),
            **overrides,
        )

    return org_id, farm_id, plot_id, user_id, make_entry


def _constraint_name(error: IntegrityError) -> str | None:
    """The constraint Postgres refused (#142).

    SQLAlchemy wraps the asyncpg exception in the DBAPI error; the driver's
    own object carries `constraint_name`, asyncpg's equivalent of psycopg's
    `exc.orig.diag.constraint_name`.
    """
    cause = error.orig.__cause__ if error.orig is not None else None
    return getattr(cause, "constraint_name", None)


async def _assert_fails(db_session: AsyncSession, row: Any, *, constraint: str) -> None:
    """The insert must fail on THIS constraint, not merely on some CHECK.

    A bare `IntegrityError` would also pass if the row tripped a different
    constraint, so a renamed or dropped rule would go unnoticed (#142).
    """
    db_session.add(row)
    with pytest.raises(IntegrityError) as excinfo:
        await db_session.commit()
    await db_session.rollback()
    assert _constraint_name(excinfo.value) == constraint


async def test_valid_row_of_each_kind_inserts(db_session: AsyncSession) -> None:
    """A valid row of each kind inserts with its required and optional fields."""
    _org, _farm, _plot, _user, make_entry = await _create_test_context(db_session)

    db_session.add_all(
        [
            make_entry(
                kind="harvest",
                yield_kg=decimal.Decimal("120.5"),
                sold_kg=decimal.Decimal("100"),
                sale_price_cop_per_kg=decimal.Decimal("3500"),
            ),
            make_entry(
                kind="task",
                labor_days=decimal.Decimal("2.5"),
                cost_cop=decimal.Decimal("100000"),
            ),
            make_entry(kind="irrigation", irrigation_mm=decimal.Decimal("15.0")),
            make_entry(
                kind="input",
                cost_cop=decimal.Decimal("45000"),
                quantity=decimal.Decimal("2"),
                unit="bulto",
            ),
            make_entry(kind="cost", cost_cop=decimal.Decimal("25000")),
            make_entry(kind="observation", notes="Plaga visible en hojas basales"),
        ]
    )
    await db_session.commit()


@pytest.mark.parametrize(
    ("desc", "overrides", "constraint"),
    [
        (
            "harvest without yield",
            {"kind": "harvest", "yield_kg": None},
            "ck_logbook_entry_harvest_yield",
        ),
        (
            "task without labor",
            {"kind": "task", "labor_days": None},
            "ck_logbook_entry_task_labor",
        ),
        (
            "labor on non-task",
            {"kind": "observation", "labor_days": decimal.Decimal("1")},
            "ck_logbook_entry_task_exclusive",
        ),
        (
            "irrigation without mm",
            {"kind": "irrigation", "irrigation_mm": None},
            "ck_logbook_entry_irrigation_depth",
        ),
        (
            "mm on non-irrigation",
            {
                "kind": "task",
                "labor_days": decimal.Decimal("1"),
                "irrigation_mm": decimal.Decimal("5"),
            },
            "ck_logbook_entry_irrigation_exclusive",
        ),
        (
            "input without cost",
            {"kind": "input", "cost_cop": None},
            "ck_logbook_entry_cost_required",
        ),
        ("cost without cost", {"kind": "cost", "cost_cop": None}, "ck_logbook_entry_cost_required"),
        (
            "sold without price",
            {
                "kind": "harvest",
                "yield_kg": decimal.Decimal("100"),
                "sold_kg": decimal.Decimal("80"),
            },
            "ck_logbook_entry_sold_and_price",
        ),
        (
            "price without sold",
            {
                "kind": "harvest",
                "yield_kg": decimal.Decimal("100"),
                "sale_price_cop_per_kg": decimal.Decimal("3000"),
            },
            "ck_logbook_entry_sold_and_price",
        ),
        (
            "sold > yield",
            {
                "kind": "harvest",
                "yield_kg": decimal.Decimal("100"),
                "sold_kg": decimal.Decimal("120"),
                "sale_price_cop_per_kg": decimal.Decimal("3000"),
            },
            "ck_logbook_entry_sold_le_yield",
        ),
        (
            "negative yield",
            {"kind": "harvest", "yield_kg": decimal.Decimal("-10")},
            "ck_logbook_entry_non_negative",
        ),
        (
            "negative cost",
            {"kind": "cost", "cost_cop": decimal.Decimal("-500")},
            "ck_logbook_entry_non_negative",
        ),
        ("invalid kind", {"kind": "pruning"}, "ck_logbook_entry_kind"),
        (
            "yield_kg on non-harvest fails",
            {"kind": "task", "labor_days": decimal.Decimal("1"), "yield_kg": decimal.Decimal("50")},
            "ck_logbook_entry_harvest_exclusive",
        ),
        (
            "sold_kg and price on non-harvest fails",
            {
                "kind": "task",
                "labor_days": decimal.Decimal("1"),
                "sold_kg": decimal.Decimal("50"),
                "sale_price_cop_per_kg": decimal.Decimal("2000"),
            },
            "ck_logbook_entry_harvest_exclusive",
        ),
    ],
)
async def test_logbook_entry_checks_enforce_invariants(
    db_session: AsyncSession, desc: str, overrides: dict[str, Any], constraint: str
) -> None:
    """CHECKs enforce required, exclusive, sold<=yield, non-negative and kind domain."""
    _org, _farm, _plot, _user, make_entry = await _create_test_context(db_session)
    await _assert_fails(db_session, make_entry(**overrides), constraint=constraint)


async def test_shared_sequence_strictly_increases_across_logbook_entry_and_extension_visit(
    db_session: AsyncSession,
) -> None:
    """Inserts into logbook_entry and extension_visit share the sequence and strictly increase."""
    org_id, farm_id, plot_id, user_id, make_entry = await _create_test_context(db_session)

    entry1 = make_entry(kind="observation", notes="Entry 1")
    db_session.add(entry1)
    await db_session.commit()

    visit = ExtensionVisitRow(
        id=uuid7(),
        org_id=org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        technician_id=user_id,
        visited_on=datetime.date(2026, 9, 28),
        topics=["human_capacities", "natural_resources"],
        recommendations="Mejorar cobertura",
        commitments="Aplicar compost",
        notes="Visita 1",
        client_updated_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(visit)
    await db_session.commit()

    entry2 = make_entry(kind="observation", notes="Entry 2")
    db_session.add(entry2)
    await db_session.commit()

    assert visit.server_version > entry1.server_version
    assert entry2.server_version > visit.server_version


async def test_extension_visit_topics_invariants(db_session: AsyncSession) -> None:
    """Topics must be a subset of the 5 Ley 1876 topics; topics outside fail."""
    org_id, farm_id, plot_id, user_id, _make_entry = await _create_test_context(db_session)

    valid_visit = ExtensionVisitRow(
        id=uuid7(),
        org_id=org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        technician_id=user_id,
        visited_on=datetime.date(2026, 9, 28),
        topics=[
            "human_capacities",
            "social_capacities",
            "information_access",
            "natural_resources",
            "participation",
        ],
        client_updated_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(valid_visit)
    await db_session.commit()

    # Negative: topic outside the five fails
    bad_visit = ExtensionVisitRow(
        id=uuid7(),
        org_id=org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        technician_id=user_id,
        visited_on=datetime.date(2026, 9, 28),
        topics=["human_capacities", "invalid_topic"],
        client_updated_at=datetime.datetime.now(datetime.UTC),
    )
    await _assert_fails(db_session, bad_visit, constraint="ck_extension_visit_topics")


async def test_attachment_parent_exactly_one_and_positive_bytes(
    db_session: AsyncSession,
) -> None:
    """Attachment must have num_nonnulls(logbook_entry_id, extension_visit_id) = 1 and bytes > 0."""
    _org, farm_id, plot_id, user_id, make_entry = await _create_test_context(db_session)

    entry = make_entry(kind="observation")
    db_session.add(entry)
    await db_session.commit()

    visit = ExtensionVisitRow(
        id=uuid7(),
        org_id=entry.org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        technician_id=user_id,
        visited_on=datetime.date(2026, 9, 28),
        topics=["natural_resources"],
        client_updated_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(visit)
    await db_session.commit()

    entry_id, visit_id = entry.id, visit.id

    # 1. Valid attachment with logbook_entry_id only
    db_session.add(
        AttachmentRow(
            id=uuid7(),
            logbook_entry_id=entry_id,
            extension_visit_id=None,
            object_key="photos/entry1.jpg",
            content_type="image/jpeg",
            bytes=150000,
        )
    )
    # 2. Valid attachment with extension_visit_id only
    db_session.add(
        AttachmentRow(
            id=uuid7(),
            logbook_entry_id=None,
            extension_visit_id=visit_id,
            object_key="photos/visit1.jpg",
            content_type="image/jpeg",
            bytes=180000,
        )
    )
    await db_session.commit()

    # Negative 1: both parents set fails
    await _assert_fails(
        db_session,
        AttachmentRow(
            id=uuid7(),
            logbook_entry_id=entry_id,
            extension_visit_id=visit_id,
            object_key="photos/both.jpg",
            content_type="image/jpeg",
            bytes=100000,
        ),
        constraint="ck_attachment_parent_exactly_one",
    )

    # Negative 2: neither parent set fails
    await _assert_fails(
        db_session,
        AttachmentRow(
            id=uuid7(),
            logbook_entry_id=None,
            extension_visit_id=None,
            object_key="photos/neither.jpg",
            content_type="image/jpeg",
            bytes=100000,
        ),
        constraint="ck_attachment_parent_exactly_one",
    )

    # Negative 3: bytes <= 0 fails
    await _assert_fails(
        db_session,
        AttachmentRow(
            id=uuid7(),
            logbook_entry_id=entry_id,
            extension_visit_id=None,
            object_key="photos/zero.jpg",
            content_type="image/jpeg",
            bytes=0,
        ),
        constraint="ck_attachment_bytes_positive",
    )
