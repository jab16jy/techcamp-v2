"""Schema behavior of `plot_baseline`, the enrollment survey
(docs/03-modelo-datos.md:239-248, 426-430; docs/11-metricas.md §1). E11 T1.

Upgrade and downgrade are exercised by every test run: `conftest._migrated_schema`
migrates to `head` once per session and downgrades to `base` at teardown, so a
broken downgrade fails the whole suite, not just a dedicated test.

Each CHECK is proven twice: a good row is accepted, a bad one is rejected by name
(docs/03's modeling rules: invariants live in the database, not only in code).
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.metrics.adapters.orm import PlotBaselineRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_CROP_ID = 1
"""`maize`, seeded by `67cf2dd1f13e_add_crop_catalog`; the catalog is global
reference data and no test writes it."""


async def _make_plot(db_session: AsyncSession) -> tuple[Any, Any, int]:
    """One organization with a farm and a plot; returns `(org_id, plot_id, crop_id)`."""
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await db_session.commit()
    return org_id, plot_id, _CROP_ID


async def _insert(db_session: AsyncSession, table: str, **values: Any) -> None:
    """Raw insert: a CHECK test should fail on the constraint, not on the mapping."""
    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    await db_session.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), values
    )
    await db_session.commit()


async def test_plot_baseline_stores_the_enrollment_survey(db_session: AsyncSession) -> None:
    """A complete survey round-trips: it is the reference impact is measured against."""
    org_id, plot_id, crop_id = await _make_plot(db_session)

    db_session.add(
        PlotBaselineRow(
            plot_id=plot_id,
            org_id=org_id,
            enrolled_on=datetime.date(2026, 2, 10),
            crop_id=crop_id,
            last_yield_kg_ha=1200.5,
            last_cost_cop_ha=1_500_000,
            irrigation_practice="gravity",
            recorded_by=None,
        )
    )
    await db_session.commit()

    stored = await db_session.get(PlotBaselineRow, plot_id)
    assert stored is not None
    assert stored.crop_id == crop_id
    assert stored.last_yield_kg_ha == 1200.5
    assert stored.irrigation_practice == "gravity"


async def test_plot_baseline_accepts_a_survey_without_declared_yield_or_cost(
    db_session: AsyncSession,
) -> None:
    """The figures are approximate and the farmer may not know them (docs/03:245): they
    are `null`, not `0` — a zero yield would read as a real, catastrophic harvest."""
    org_id, plot_id, crop_id = await _make_plot(db_session)

    db_session.add(
        PlotBaselineRow(
            plot_id=plot_id,
            org_id=org_id,
            enrolled_on=datetime.date(2026, 2, 10),
            crop_id=crop_id,
            last_yield_kg_ha=None,
            last_cost_cop_ha=None,
            irrigation_practice="none",
            recorded_by=None,
        )
    )
    await db_session.commit()

    stored = await db_session.get(PlotBaselineRow, plot_id)
    assert stored is not None
    assert stored.last_yield_kg_ha is None
    assert stored.last_cost_cop_ha is None


async def test_plot_baseline_rejects_an_undocumented_irrigation_practice(
    db_session: AsyncSession,
) -> None:
    """Same closed vocabulary as `plot.irrigation_system` (docs/03 §`plot_baseline`)."""
    org_id, plot_id, crop_id = await _make_plot(db_session)

    with pytest.raises(IntegrityError) as exc_info:
        await _insert(
            db_session,
            "plot_baseline",
            plot_id=plot_id,
            org_id=org_id,
            enrolled_on=datetime.date(2026, 2, 10),
            crop_id=crop_id,
            irrigation_practice="canal",
            recorded_by=None,
        )

    assert "ck_plot_baseline_irrigation_practice" in str(exc_info.value.orig)


async def test_plot_baseline_rejects_a_negative_declared_value(db_session: AsyncSession) -> None:
    """A negative yield or cost is a bug in the caller, not missing data (`null` is)."""
    org_id, plot_id, crop_id = await _make_plot(db_session)

    with pytest.raises(IntegrityError) as exc_info:
        await _insert(
            db_session,
            "plot_baseline",
            plot_id=plot_id,
            org_id=org_id,
            enrolled_on=datetime.date(2026, 2, 10),
            crop_id=crop_id,
            last_yield_kg_ha=-1,
            irrigation_practice="drip",
            recorded_by=None,
        )

    assert "ck_plot_baseline_last_yield_non_negative" in str(exc_info.value.orig)


async def test_plot_baseline_rejects_a_negative_declared_cost(db_session: AsyncSession) -> None:
    org_id, plot_id, crop_id = await _make_plot(db_session)

    with pytest.raises(IntegrityError) as exc_info:
        await _insert(
            db_session,
            "plot_baseline",
            plot_id=plot_id,
            org_id=org_id,
            enrolled_on=datetime.date(2026, 2, 10),
            crop_id=crop_id,
            last_cost_cop_ha=-1,
            irrigation_practice="drip",
            recorded_by=None,
        )

    assert "ck_plot_baseline_last_cost_non_negative" in str(exc_info.value.orig)


async def test_plot_baseline_rejects_a_plot_of_another_organization(
    db_session: AsyncSession,
) -> None:
    """docs/09 §Seguridad: `org_id` is tied to the plot's own, so a row can never
    name one organization while pointing at another organization's plot."""
    org_id, plot_id, crop_id = await _make_plot(db_session)
    other_org_id = uuid7()

    with pytest.raises(IntegrityError) as exc_info:
        await _insert(
            db_session,
            "plot_baseline",
            plot_id=plot_id,
            org_id=other_org_id,
            enrolled_on=datetime.date(2026, 2, 10),
            crop_id=crop_id,
            irrigation_practice="drip",
            recorded_by=None,
        )

    assert "fk_plot_baseline_plot_id_org_id" in str(exc_info.value.orig)
