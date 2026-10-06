"""The cycle-summary repository against the real database
(docs/09-cuellos-de-botella.md#seguridad).

The `org_id` predicate is the security-relevant part of this repository's only
read, and the use case cannot exercise it: it resolves the cycle through `farms`
first, so nothing reaches `get_for_org` with a foreign org id. Seeding a stored
summary and asking for it under another organization's id is the only way to
prove the filter is in the query and not only in the docstring — the same shape
`test_repositories.py` uses for the enrollment survey.

The round trip matters too: a `Numeric` column may hand back a figure the caller
never typed, so the stored row is what the repository returns, never the value it
was handed (same rule as `SqlAlchemyBaselineRepository.put`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import CropCycleRow, FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.metrics.adapters.cycle_summary_repository import SqlAlchemyCycleSummaryRepository
from techcamp.metrics.domain.cycle_summary import CycleSummary
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_CROP_ID = 1
"""`maize`, seeded by `67cf2dd1f13e_add_crop_catalog`; no test writes the catalog."""
COMPUTED_AT = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
"""Frozen: a test clock must not drift (E11 lessons, #12)."""

# A counter, not `uuid7().int % 10**10`: the modulo could collide on the unique
# `phone` column between two `uuid7()`s generated close together (#21).
_phone_seq = count()


async def _org_with_cycle(db_session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    """One organization with a farm, a plot and one crop cycle.

    Returns `(org_id, plot_id, crop_cycle_id)`: the triple every seeded summary
    needs, because `crop_cycle_summary` ties its cycle to its plot
    (`fk_crop_cycle_summary_crop_cycle_id_plot_id`) and its plot to its org
    (`fk_crop_cycle_summary_plot_id_org_id`).
    """
    org_id, user_id = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=f"+5730055{next(_phone_seq):05d}"))
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
    crop_cycle_id = uuid7()
    db_session.add(
        CropCycleRow(
            id=crop_cycle_id,
            plot_id=plot_id,
            crop_id=_CROP_ID,
            sown_on=date(2026, 8, 1),
            expected_harvest_on=date(2026, 12, 15),
            status="harvested",
        )
    )
    await db_session.commit()
    return org_id, plot_id, crop_cycle_id


def _summary(crop_cycle_id: UUID, plot_id: UUID, org_id: UUID, **changes: object) -> CycleSummary:
    """A full row; each test states only the metrics it varies."""
    figures: dict[str, object] = {
        "yield_kg_ha": Decimal("6000"),
        "yield_change_vs_baseline": Decimal("0.2"),
        # Always null in E11 (D-T0.9); kept explicit so the round trip proves it
        # survives storage rather than being filled in by the adapter.
        "relative_yield": None,
        "water_applied_m3_ha": Decimal("5000"),
        "irrigation_wue_kg_m3": Decimal("1.2"),
        "water_stress_days": 7,
        "cost_cop_ha": Decimal("3000000"),
        "cost_cop_kg": Decimal("500"),
        "yield_kg_per_labor_day": Decimal("400"),
        "gross_margin_cop": Decimal("3000000"),
        "loss_kg": Decimal("120"),
        "loss_cop": Decimal("800000"),
        "computed_at": COMPUTED_AT,
    }
    figures.update(changes)
    return CycleSummary(crop_cycle_id=crop_cycle_id, plot_id=plot_id, org_id=org_id, **figures)  # type: ignore[arg-type]


async def test_a_stored_summary_is_read_back_inside_its_own_organization(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id, crop_cycle_id = await _org_with_cycle(db_session)
    repository = SqlAlchemyCycleSummaryRepository(db_session)
    await repository.upsert(_summary(crop_cycle_id, plot_id, org_id))

    found = await repository.get_for_org(crop_cycle_id, org_id)

    assert found == _summary(crop_cycle_id, plot_id, org_id)


async def test_a_summary_is_absent_for_another_organization(db_session: AsyncSession) -> None:
    org_id, plot_id, crop_cycle_id = await _org_with_cycle(db_session)
    other_org_id, _other_plot_id, _other_cycle_id = await _org_with_cycle(db_session)
    assert other_org_id != org_id
    repository = SqlAlchemyCycleSummaryRepository(db_session)
    await repository.upsert(_summary(crop_cycle_id, plot_id, org_id))

    found = await repository.get_for_org(crop_cycle_id, other_org_id)

    assert found is None


async def test_a_cycle_with_no_stored_summary_is_absent(db_session: AsyncSession) -> None:
    """D-T0.8: an active cycle has no row, which is how the read API tells it
    apart from a finished one."""
    org_id, _plot_id, crop_cycle_id = await _org_with_cycle(db_session)
    repository = SqlAlchemyCycleSummaryRepository(db_session)

    assert await repository.get_for_org(crop_cycle_id, org_id) is None


async def test_writing_the_same_cycle_twice_leaves_the_same_figures(
    db_session: AsyncSession,
) -> None:
    """docs/03-modelo-datos.md:442: the job writes with an upsert, so running it
    twice over the same closed cycle is the same result, not a second row."""
    org_id, plot_id, crop_cycle_id = await _org_with_cycle(db_session)
    repository = SqlAlchemyCycleSummaryRepository(db_session)

    first = await repository.upsert(_summary(crop_cycle_id, plot_id, org_id))
    later = await repository.upsert(
        _summary(
            crop_cycle_id,
            plot_id,
            org_id,
            yield_kg_ha=Decimal("7200"),
            water_stress_days=9,
            computed_at=datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
        )
    )
    again = await repository.upsert(
        _summary(crop_cycle_id, plot_id, org_id, yield_kg_ha=Decimal("7200"))
    )

    assert first.yield_kg_ha == Decimal("6000")
    assert later.yield_kg_ha == Decimal("7200")
    assert later.water_stress_days == 9
    assert again.yield_kg_ha == Decimal("7200")


async def test_a_cycle_with_no_evidence_stores_nulls_and_reads_them_back(
    db_session: AsyncSession,
) -> None:
    """docs/03-modelo-datos.md:441: a missing datum is `null` in the row too, so
    the read cannot turn "no harvest" into a harvest of zero."""
    org_id, plot_id, crop_cycle_id = await _org_with_cycle(db_session)
    repository = SqlAlchemyCycleSummaryRepository(db_session)
    empty = _summary(
        crop_cycle_id,
        plot_id,
        org_id,
        yield_kg_ha=None,
        yield_change_vs_baseline=None,
        water_applied_m3_ha=None,
        irrigation_wue_kg_m3=None,
        water_stress_days=None,
        cost_cop_ha=None,
        cost_cop_kg=None,
        yield_kg_per_labor_day=None,
        gross_margin_cop=None,
        loss_kg=None,
        loss_cop=None,
    )

    await repository.upsert(empty)

    assert await repository.get_for_org(crop_cycle_id, org_id) == empty


async def test_a_negative_change_and_a_negative_margin_are_stored(
    db_session: AsyncSession,
) -> None:
    """docs/11 §1: a cycle can yield less than the survey and cost more than it
    earns, so neither figure has a range CHECK (`crop_cycle_summary`'s
    `yield_change_vs_baseline` and `gross_margin_cop`)."""
    org_id, plot_id, crop_cycle_id = await _org_with_cycle(db_session)
    repository = SqlAlchemyCycleSummaryRepository(db_session)

    await repository.upsert(
        _summary(
            crop_cycle_id,
            plot_id,
            org_id,
            yield_change_vs_baseline=Decimal("-0.4"),
            gross_margin_cop=Decimal("-1500000"),
        )
    )
    found = await repository.get_for_org(crop_cycle_id, org_id)

    assert found is not None
    assert found.yield_change_vs_baseline == Decimal("-0.4")
    assert found.gross_margin_cop == Decimal("-1500000")
