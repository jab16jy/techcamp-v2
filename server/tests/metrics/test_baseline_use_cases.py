"""Enrollment-survey use cases with pure doubles at the port (docs/04-api.md:51-52,
233; docs/03-modelo-datos.md:426-430; D-T0.10, D-T0.11).

Every behavior test carries its negative assertion: a rejected write stores
nothing, and a cross-org plot is never found (docs/09-cuellos-de-botella.md
#seguridad). Plot access is resolved through the `farms` facade, exactly as
`irrigation` and `home` do, so a foreign plot is a 404 and not a leak.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from uuid import UUID

import pytest

from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.farms.domain.models import Crop, IrrigationSystem, KcSource, Plot
from techcamp.identity.domain.models import Membership, Role
from techcamp.metrics.application.baseline import get_plot_baseline, put_plot_baseline
from techcamp.metrics.domain.errors import (
    InsufficientRoleError,
    PlotBaselineNotFoundError,
    UnknownCropError,
)
from techcamp.metrics.domain.models import IrrigationPractice, PlotBaseline
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_CROP_ID = 1
"""`maize`, from the seeded catalog (`67cf2dd1f13e_add_crop_catalog`)."""
_ENROLLED_ON = datetime.date(2026, 2, 10)
"""Frozen: a test clock must not drift (E11 lessons, #12)."""


def _plot(org_id: UUID) -> Plot:
    return Plot(
        id=uuid7(),
        org_id=org_id,
        farm_id=uuid7(),
        name="Lote 1",
        boundary="POLYGON((-74.1 10.9, -74.1 10.91, -74.09 10.91, -74.09 10.9, -74.1 10.9))",
        area_ha=1.5,
        weather_cell_id=None,
        irrigation_system=IrrigationSystem.DRIP,
        irrigation_efficiency=0.9,
        system_flow_lph=None,
    )


def _crop(crop_id: int) -> Crop:
    return Crop(id=crop_id, code="maize", name_es="Maíz", kc_source=KcSource.FAO56, stages=())


class _FakeMembershipRepository:
    def __init__(self, memberships: list[Membership]) -> None:
        self._memberships = memberships

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        return [m for m in self._memberships if m.user_id == user_id]

    async def list_for_org(self, org_id: UUID) -> list[Membership]:
        return [m for m in self._memberships if m.org_id == org_id]

    async def get(self, user_id: UUID, org_id: UUID) -> Membership | None:
        for m in self._memberships:
            if m.user_id == user_id and m.org_id == org_id:
                return m
        return None


class _FakePlotRepository:
    def __init__(self, plots: list[Plot]) -> None:
        self._plots = {p.id: p for p in plots}

    async def get_for_orgs(self, plot_id: UUID, org_ids: Sequence[UUID]) -> Plot | None:
        plot = self._plots.get(plot_id)
        if plot is None or plot.org_id not in set(org_ids):
            return None
        return plot


class _FakeCropRepository:
    def __init__(self, crop_ids: list[int]) -> None:
        self._crop_ids = set(crop_ids)

    async def get(self, crop_id: int) -> Crop | None:
        return _crop(crop_id) if crop_id in self._crop_ids else None


class _FakeBaselineRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, PlotBaseline] = {}
        self.puts: int = 0

    async def get_for_org(self, plot_id: UUID, org_id: UUID) -> PlotBaseline | None:
        row = self.rows.get(plot_id)
        return row if row is not None and row.org_id == org_id else None

    async def put(self, baseline: PlotBaseline) -> PlotBaseline:
        self.puts += 1
        self.rows[baseline.plot_id] = baseline
        return baseline


def _member(org_id: UUID, user_id: UUID, role: Role) -> _FakeMembershipRepository:
    return _FakeMembershipRepository([Membership(org_id=org_id, user_id=user_id, role=role)])


async def test_owner_records_the_survey_as_itself() -> None:
    org_id, user_id = uuid7(), uuid7()
    plot = _plot(org_id)
    baselines = _FakeBaselineRepository()

    recorded = await put_plot_baseline(
        user_id=user_id,
        plot_id=plot.id,
        enrolled_on=_ENROLLED_ON,
        crop_id=_CROP_ID,
        last_yield_kg_ha=3200.0,
        last_cost_cop_ha=None,
        irrigation_practice=IrrigationPractice.DRIP,
        plots=_FakePlotRepository([plot]),
        crops=_FakeCropRepository([_CROP_ID]),
        baselines=baselines,
        memberships=_member(org_id, user_id, Role.OWNER),
    )

    assert recorded.recorded_by == user_id
    assert recorded.org_id == org_id
    assert recorded.plot_id == plot.id
    assert baselines.rows[plot.id] == recorded


async def test_an_approximate_cost_stays_missing_evidence() -> None:
    org_id, user_id = uuid7(), uuid7()
    plot = _plot(org_id)

    recorded = await put_plot_baseline(
        user_id=user_id,
        plot_id=plot.id,
        enrolled_on=_ENROLLED_ON,
        crop_id=_CROP_ID,
        last_yield_kg_ha=3200.0,
        last_cost_cop_ha=None,
        irrigation_practice=IrrigationPractice.NONE,
        plots=_FakePlotRepository([plot]),
        crops=_FakeCropRepository([_CROP_ID]),
        baselines=_FakeBaselineRepository(),
        memberships=_member(org_id, user_id, Role.TECHNICIAN),
    )

    assert recorded.last_cost_cop_ha is None
    assert recorded.irrigation_practice is IrrigationPractice.NONE


@pytest.mark.parametrize("role", [Role.PRODUCER, Role.VIEWER])
async def test_a_read_only_role_cannot_record_the_survey(role: Role) -> None:
    org_id, user_id = uuid7(), uuid7()
    plot = _plot(org_id)
    baselines = _FakeBaselineRepository()

    with pytest.raises(InsufficientRoleError):
        await put_plot_baseline(
            user_id=user_id,
            plot_id=plot.id,
            enrolled_on=_ENROLLED_ON,
            crop_id=_CROP_ID,
            last_yield_kg_ha=3200.0,
            last_cost_cop_ha=None,
            irrigation_practice=IrrigationPractice.DRIP,
            plots=_FakePlotRepository([plot]),
            crops=_FakeCropRepository([_CROP_ID]),
            baselines=baselines,
            memberships=_member(org_id, user_id, role),
        )

    assert baselines.puts == 0


async def test_a_crop_outside_the_catalog_is_rejected() -> None:
    org_id, user_id = uuid7(), uuid7()
    plot = _plot(org_id)
    baselines = _FakeBaselineRepository()

    with pytest.raises(UnknownCropError):
        await put_plot_baseline(
            user_id=user_id,
            plot_id=plot.id,
            enrolled_on=_ENROLLED_ON,
            crop_id=9999,
            last_yield_kg_ha=3200.0,
            last_cost_cop_ha=None,
            irrigation_practice=IrrigationPractice.DRIP,
            plots=_FakePlotRepository([plot]),
            crops=_FakeCropRepository([_CROP_ID]),
            baselines=baselines,
            memberships=_member(org_id, user_id, Role.OWNER),
        )

    assert baselines.puts == 0


async def test_a_plot_in_another_org_is_not_found() -> None:
    org_id, user_id = uuid7(), uuid7()
    foreign_plot = _plot(uuid7())
    baselines = _FakeBaselineRepository()

    with pytest.raises(PlotNotFoundError):
        await put_plot_baseline(
            user_id=user_id,
            plot_id=foreign_plot.id,
            enrolled_on=_ENROLLED_ON,
            crop_id=_CROP_ID,
            last_yield_kg_ha=3200.0,
            last_cost_cop_ha=None,
            irrigation_practice=IrrigationPractice.DRIP,
            plots=_FakePlotRepository([foreign_plot]),
            crops=_FakeCropRepository([_CROP_ID]),
            baselines=baselines,
            memberships=_member(org_id, user_id, Role.OWNER),
        )

    assert baselines.puts == 0


async def test_any_member_reads_the_survey() -> None:
    org_id = uuid7()
    plot = _plot(org_id)
    viewer_id = uuid7()
    baselines = _FakeBaselineRepository()
    baselines.rows[plot.id] = PlotBaseline(
        plot_id=plot.id,
        org_id=org_id,
        enrolled_on=_ENROLLED_ON,
        crop_id=_CROP_ID,
        last_yield_kg_ha=3200.0,
        last_cost_cop_ha=None,
        irrigation_practice=IrrigationPractice.GRAVITY,
        recorded_by=uuid7(),
    )

    found = await get_plot_baseline(
        user_id=viewer_id,
        plot_id=plot.id,
        plots=_FakePlotRepository([plot]),
        baselines=baselines,
        memberships=_member(org_id, viewer_id, Role.VIEWER),
    )

    assert found.irrigation_practice is IrrigationPractice.GRAVITY
    assert found.recorded_by != viewer_id


async def test_a_plot_without_a_survey_is_not_found() -> None:
    org_id, user_id = uuid7(), uuid7()
    plot = _plot(org_id)

    with pytest.raises(PlotBaselineNotFoundError):
        await get_plot_baseline(
            user_id=user_id,
            plot_id=plot.id,
            plots=_FakePlotRepository([plot]),
            baselines=_FakeBaselineRepository(),
            memberships=_member(org_id, user_id, Role.VIEWER),
        )


async def test_a_survey_of_a_foreign_plot_is_not_found() -> None:
    org_id, user_id = uuid7(), uuid7()
    foreign_plot = _plot(uuid7())

    with pytest.raises(PlotNotFoundError):
        await get_plot_baseline(
            user_id=user_id,
            plot_id=foreign_plot.id,
            plots=_FakePlotRepository([foreign_plot]),
            baselines=_FakeBaselineRepository(),
            memberships=_member(org_id, user_id, Role.VIEWER),
        )
