"""Water balance daily and irrigation recommendation persistence tests (docs/03:192-215).

Tests against real Postgres with TDD:
- Upsert and read back (balance and recommendation with advice/rationale JSONB)
- Upsert overwriting the same day, including the value the overwrite itself returns
- Range listing ordered by day
- Org isolation (access across organizations returns nothing), for both repositories'
  single-day `get_for_plot`
- DB CHECK constraints (depth/duration rejected on non-irrigate kind)

The Alembic upgrade/downgrade cycle for this module's migration is not re-tested here:
`tests/conftest.py`'s session-scoped `_migrated_schema` fixture already runs
`command.upgrade(..., "head")` once before the whole suite and `command.downgrade(...,
"base")` after, which exercises every migration's upgrade and downgrade, including this
module's; every test in this file then depends on the resulting schema matching the ORM
(column types, constraints) to pass at all.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.irrigation.adapters.orm import IrrigationRecommendationRow
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
    SqlAlchemyWaterBalanceRepository,
)
from techcamp.irrigation.domain.models import (
    IrrigationRecommendation,
    RainfedAdvice,
    RecommendationKind,
    WaterBalanceDay,
)
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


async def _make_plot(
    db_session: AsyncSession,
    *,
    org_id: UUID | None = None,
    name: str = "Lote 1",
    irrigation_system: str = "drip",
) -> tuple[UUID, UUID]:
    """Create an organization, farm, and plot for testing."""
    if org_id is None:
        org_id = uuid7()
        db_session.add(OrganizationRow(id=org_id, name="Org", kind="individual"))
        await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca",
            municipality_code="47001",
            location=_POINT,
        )
    )
    await db_session.commit()
    plot_id = uuid7()
    efficiency = 0.9 if irrigation_system != "none" else None
    flow = 1000.0 if irrigation_system != "none" else None
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name=name,
            boundary=_BOUNDARY,
            irrigation_system=irrigation_system,
            irrigation_efficiency=Decimal(str(efficiency)) if efficiency else None,
            system_flow_lph=Decimal(str(flow)) if flow else None,
        )
    )
    await db_session.commit()
    return org_id, plot_id


def _sample_water_balance(
    plot_id: UUID,
    day: date,
    *,
    depletion_mm: float = 18.5,
    soil_moisture_obs_pct: float | None = 22.4,
    assimilation_k: float = 0.5,
) -> WaterBalanceDay:
    return WaterBalanceDay(
        plot_id=plot_id,
        day=day,
        etc_mm=4.2,
        effective_rain_mm=0.0,
        irrigation_mm=0.0,
        taw_mm=45.0,
        raw_mm=22.5,
        depletion_model_mm=19.0,
        depletion_mm=depletion_mm,
        soil_moisture_obs_pct=soil_moisture_obs_pct,
        assimilation_k=assimilation_k,
        stress_moisture_pct=17.5,
    )


async def test_upsert_and_read_back_water_balance(db_session: AsyncSession) -> None:
    """Upsert a daily water balance row and read it back with exact float conversions."""
    org_id, plot_id = await _make_plot(db_session)
    repo = SqlAlchemyWaterBalanceRepository(db_session)
    target_day = date(2026, 9, 25)

    balance = _sample_water_balance(plot_id, target_day)
    await repo.upsert(balance)

    # Org-agnostic get for the job
    read_back = await repo.get_for_plot(plot_id, target_day)
    assert read_back is not None
    assert read_back.plot_id == plot_id
    assert read_back.day == target_day
    assert read_back.etc_mm == pytest.approx(4.2)
    assert read_back.effective_rain_mm == pytest.approx(0.0)
    assert read_back.irrigation_mm == pytest.approx(0.0)
    assert read_back.taw_mm == pytest.approx(45.0)
    assert read_back.raw_mm == pytest.approx(22.5)
    assert read_back.depletion_model_mm == pytest.approx(19.0)
    assert read_back.depletion_mm == pytest.approx(18.5)
    assert read_back.soil_moisture_obs_pct == pytest.approx(22.4)
    assert read_back.assimilation_k == pytest.approx(0.5)
    assert read_back.stress_moisture_pct == pytest.approx(17.5)

    # Test with nullable soil_moisture_obs_pct
    day_no_sensor = date(2026, 9, 26)
    balance_no_sensor = _sample_water_balance(
        plot_id, day_no_sensor, soil_moisture_obs_pct=None, assimilation_k=0.0
    )
    await repo.upsert(balance_no_sensor)
    read_no_sensor = await repo.get_for_plot(plot_id, day_no_sensor)
    assert read_no_sensor is not None
    assert read_no_sensor.soil_moisture_obs_pct is None
    assert read_no_sensor.assimilation_k == pytest.approx(0.0)


async def test_upsert_and_read_back_recommendation(db_session: AsyncSession) -> None:
    """Upsert recommendations (irrigate and rainfed) and read back including JSONB fields."""
    org_id, plot_id = await _make_plot(db_session)
    repo = SqlAlchemyIrrigationRecommendationRepository(db_session)
    day1 = date(2026, 9, 25)

    rec_irrigate = IrrigationRecommendation(
        kind=RecommendationKind.IRRIGATE,
        depth_mm=20.5,
        duration_min=45,
        advice=(),
        rationale={"et0_mm": 4.5, "kc": 1.1, "p": 0.5, "dr_mm": 18.5},
    )
    saved1 = await repo.upsert(rec_irrigate, plot_id=plot_id, day=day1)
    assert saved1.id is not None

    read1 = await repo.get_for_plot(plot_id, day1, org_id=org_id)
    assert read1 is not None
    assert read1.kind == RecommendationKind.IRRIGATE
    assert read1.depth_mm == pytest.approx(20.5)
    assert read1.duration_min == 45
    assert read1.advice == ()
    assert read1.rationale == {"et0_mm": 4.5, "kc": 1.1, "p": 0.5, "dr_mm": 18.5}

    # Rainfed recommendation with advice codes
    day2 = date(2026, 9, 26)
    rec_rainfed = IrrigationRecommendation(
        kind=RecommendationKind.RAINFED,
        depth_mm=None,
        duration_min=None,
        advice=(RainfedAdvice.RAIN_EXPECTED, RainfedAdvice.CONSERVE_MOISTURE),
        rationale={"forecast_rain_7d_mm": 35.0, "dr_mm": 20.0},
    )
    await repo.upsert(rec_rainfed, plot_id=plot_id, day=day2)

    read2 = await repo.get_for_plot(plot_id, day2, org_id=org_id)
    assert read2 is not None
    assert read2.kind == RecommendationKind.RAINFED
    assert read2.depth_mm is None
    assert read2.duration_min is None
    assert read2.advice == (RainfedAdvice.RAIN_EXPECTED, RainfedAdvice.CONSERVE_MOISTURE)
    assert read2.rationale == {"forecast_rain_7d_mm": 35.0, "dr_mm": 20.0}


async def test_upsert_overwrites_same_day(db_session: AsyncSession) -> None:
    """Upserting for the same (plot_id, day) overwrites the existing row, not duplicates."""
    org_id, plot_id = await _make_plot(db_session)
    balance_repo = SqlAlchemyWaterBalanceRepository(db_session)
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)
    day = date(2026, 9, 25)

    # 1. Balance update
    b1 = _sample_water_balance(plot_id, day, depletion_mm=10.0)
    await balance_repo.upsert(b1)
    b2 = _sample_water_balance(plot_id, day, depletion_mm=25.0)
    await balance_repo.upsert(b2)

    updated_b = await balance_repo.get_for_plot(plot_id, day)
    assert updated_b is not None
    assert updated_b.depletion_mm == pytest.approx(25.0)

    # 2. Recommendation update
    r1 = IrrigationRecommendation(
        kind=RecommendationKind.NOT_NEEDED,
        depth_mm=None,
        duration_min=None,
        advice=(),
        rationale={"status": "ok"},
    )
    await rec_repo.upsert(r1, plot_id=plot_id, day=day)

    r2 = IrrigationRecommendation(
        kind=RecommendationKind.IRRIGATE,
        depth_mm=15.0,
        duration_min=30,
        advice=(),
        rationale={"status": "irrigate"},
    )
    saved_r2 = await rec_repo.upsert(r2, plot_id=plot_id, day=day)

    # R3-upsert-returning-stale: the overwriting upsert's own return value must be
    # fresh, not the first upsert's row still cached in the session identity map.
    assert saved_r2.kind == RecommendationKind.IRRIGATE
    assert saved_r2.depth_mm == pytest.approx(15.0)
    assert saved_r2.duration_min == 30
    assert saved_r2.rationale == {"status": "irrigate"}

    updated_r = await rec_repo.get_for_plot(plot_id, day, org_id=org_id)
    assert updated_r is not None
    assert updated_r.kind == RecommendationKind.IRRIGATE
    assert updated_r.depth_mm == pytest.approx(15.0)
    assert updated_r.duration_min == 30
    assert updated_r.rationale == {"status": "irrigate"}


async def test_water_balance_range_listing_ordered_by_day(db_session: AsyncSession) -> None:
    """Listing balance days for a plot in a date range returns rows ordered by day."""
    org_id, plot_id = await _make_plot(db_session)
    repo = SqlAlchemyWaterBalanceRepository(db_session)

    # Insert out of order: day 22, day 20, day 21, and day 15 (outside window)
    for day, dep in [
        (date(2026, 9, 22), 22.0),
        (date(2026, 9, 20), 20.0),
        (date(2026, 9, 21), 21.0),
        (date(2026, 9, 15), 15.0),
        (date(2026, 9, 25), 25.0),
    ]:
        await repo.upsert(_sample_water_balance(plot_id, day, depletion_mm=dep))

    rows = await repo.list_for_plot(
        plot_id, org_id, from_day=date(2026, 9, 20), to_day=date(2026, 9, 22)
    )
    assert len(rows) == 3
    assert [r.day for r in rows] == [
        date(2026, 9, 20),
        date(2026, 9, 21),
        date(2026, 9, 22),
    ]
    assert [r.depletion_mm for r in rows] == [
        pytest.approx(20.0),
        pytest.approx(21.0),
        pytest.approx(22.0),
    ]


async def test_org_isolation_for_balance_and_recommendation(db_session: AsyncSession) -> None:
    """Plot data belongs only to its organization; querying from another org returns nothing."""
    org1_id, plot1_id = await _make_plot(db_session, name="Lote Org 1")
    org2_id, _plot2_id = await _make_plot(db_session, name="Lote Org 2")

    balance_repo = SqlAlchemyWaterBalanceRepository(db_session)
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)
    day = date(2026, 9, 25)

    await balance_repo.upsert(_sample_water_balance(plot1_id, day))
    await rec_repo.upsert(
        IrrigationRecommendation(
            kind=RecommendationKind.POSTPONE,
            depth_mm=None,
            duration_min=None,
            advice=(),
            rationale={"rain_48h": 25.0},
        ),
        plot_id=plot1_id,
        day=day,
    )

    # 1. Org 1 sees plot 1 data
    assert len(await balance_repo.list_for_plot(plot1_id, org1_id, day, day)) == 1
    assert await rec_repo.get_for_plot(plot1_id, day, org_id=org1_id) is not None
    assert await balance_repo.get_for_plot(plot1_id, day, org_id=org1_id) is not None

    # 2. Org 2 querying plot 1 gets empty list and None (docs/09 org isolation)
    assert len(await balance_repo.list_for_plot(plot1_id, org2_id, day, day)) == 0
    assert await rec_repo.get_for_plot(plot1_id, day, org_id=org2_id) is None
    # R3-balance-get-org-filter-untested: the single-day balance read's org_id
    # filter path was never exercised cross-org before this test.
    assert await balance_repo.get_for_plot(plot1_id, day, org_id=org2_id) is None


async def test_check_constraints_reject_invalid_recommendation(db_session: AsyncSession) -> None:
    """DB CHECK constraints enforce kind vocabulary and reject depth/duration on non-irrigate."""
    _org_id, plot_id = await _make_plot(db_session)
    day = date(2026, 9, 25)

    # 1. Non-irrigate kind with depth_mm must fail check constraint
    invalid_depth_row = IrrigationRecommendationRow(
        id=uuid7(),
        plot_id=plot_id,
        day=day,
        kind="not_needed",
        depth_mm=Decimal("15.0"),
        duration_min=None,
        advice=[],
        rationale={},
    )
    db_session.add(invalid_depth_row)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # 2. Non-irrigate kind with duration_min must fail check constraint
    invalid_duration_row = IrrigationRecommendationRow(
        id=uuid7(),
        plot_id=plot_id,
        day=day,
        kind="rainfed",
        depth_mm=None,
        duration_min=30,
        advice=[],
        rationale={},
    )
    db_session.add(invalid_duration_row)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # 3. Invalid kind enum must fail check constraint
    invalid_kind_row = IrrigationRecommendationRow(
        id=uuid7(),
        plot_id=plot_id,
        day=day,
        kind="unknown_kind",
        depth_mm=None,
        duration_min=None,
        advice=[],
        rationale={},
    )
    db_session.add(invalid_kind_row)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_get_for_plot_logs_and_drops_unknown_advice_code(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """R3-advice-silent-drop: an advice code that is not a current RainfedAdvice
    member (e.g. a legacy or corrupted value) is dropped but logged, not silently
    lost. Written directly through the ORM row since the domain model only accepts
    real RainfedAdvice members.
    """
    _org_id, plot_id = await _make_plot(db_session)
    day = date(2026, 9, 25)
    row = IrrigationRecommendationRow(
        id=uuid7(),
        plot_id=plot_id,
        day=day,
        kind="rainfed",
        depth_mm=None,
        duration_min=None,
        advice=["rain_expected", "legacy_unknown_code"],
        rationale={},
    )
    db_session.add(row)
    await db_session.commit()

    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)
    with caplog.at_level(logging.WARNING):
        rec = await rec_repo.get_for_plot(plot_id, day)

    assert rec is not None
    assert rec.advice == (RainfedAdvice.RAIN_EXPECTED,)
    assert any("legacy_unknown_code" in message for message in caplog.messages)


async def test_upsert_rejects_recommendation_plot_or_day_mismatch(
    db_session: AsyncSession,
) -> None:
    """R3-upsert-ignores-model-plot-day: when `recommendation` carries its own
    `plot_id`/`day` (e.g. re-saving one just read back), it must agree with the
    keyword arguments; a mismatch is rejected instead of silently persisted under
    the keyword identity.
    """
    _org_id, plot_id = await _make_plot(db_session)
    other_plot_id = uuid7()
    day = date(2026, 9, 25)
    other_day = date(2026, 9, 26)
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)

    mismatched_plot = IrrigationRecommendation(
        kind=RecommendationKind.NOT_NEEDED,
        depth_mm=None,
        duration_min=None,
        advice=(),
        rationale={},
        plot_id=other_plot_id,
    )
    with pytest.raises(ValueError, match="plot_id"):
        await rec_repo.upsert(mismatched_plot, plot_id=plot_id, day=day)

    mismatched_day = IrrigationRecommendation(
        kind=RecommendationKind.NOT_NEEDED,
        depth_mm=None,
        duration_min=None,
        advice=(),
        rationale={},
        plot_id=plot_id,
        day=other_day,
    )
    with pytest.raises(ValueError, match="day"):
        await rec_repo.upsert(mismatched_day, plot_id=plot_id, day=day)
