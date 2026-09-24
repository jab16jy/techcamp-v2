"""Crop catalog seed integrity (odd/tasks/techcamp-v2-e3-farms.md T3;
docs/03-modelo-datos.md:115-127, 446, 489; migration `67cf2dd1f13e`).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.repositories import SqlAlchemyCropRepository
from techcamp.farms.domain.models import CROP_STAGES, KcSource

pytestmark = pytest.mark.anyio


async def test_every_crop_with_a_verified_kc_has_four_valid_stages(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyCropRepository(db_session)
    crops = await repo.list_all()

    verified = [c for c in crops if c.kc_source is not KcSource.NONE]
    assert verified, "expected at least one seeded crop with a verified Kc"

    for crop in verified:
        stages = [s.stage for s in crop.stages]
        assert stages == list(CROP_STAGES), f"{crop.code}: stages {stages}"
        for stage in crop.stages:
            assert stage.kc > 0, f"{crop.code}/{stage.stage}: kc must be > 0"
            assert 0 < stage.depletion_fraction_p < 1, (
                f"{crop.code}/{stage.stage}: p must be in (0, 1)"
            )
            assert stage.length_days > 0, f"{crop.code}/{stage.stage}: length_days must be > 0"


async def test_yam_has_no_verified_kc_and_no_stages(db_session: AsyncSession) -> None:
    """docs/03-modelo-datos.md:446's own example: Ñame isn't in FAO-56 Table 12."""
    repo = SqlAlchemyCropRepository(db_session)
    crops = await repo.list_all()

    yam = next(c for c in crops if c.code == "yam")

    assert yam.kc_source is KcSource.NONE
    assert yam.stages == ()


async def test_stage_lengths_follow_the_20_30_35_15_split(db_session: AsyncSession) -> None:
    """T3 decision (odd/tasks/techcamp-v2-e3-farms.md): each crop's stage
    lengths are a 20/30/35/15% initial/development/mid/late split of its
    total cycle, `mid` absorbing the rounding remainder (round-half-up on
    the other three) so the four stages always sum exactly to the cycle.
    """
    repo = SqlAlchemyCropRepository(db_session)
    crops = await repo.list_all()

    with_stages = [c for c in crops if c.stages]
    assert with_stages, "expected at least one seeded crop with stages"

    def round_half_up(value: float) -> int:
        return int(value + 0.5)

    for crop in with_stages:
        by_stage = {s.stage: s.length_days for s in crop.stages}
        cycle = sum(by_stage.values())
        expected_initial = round_half_up(0.20 * cycle)
        expected_development = round_half_up(0.30 * cycle)
        expected_late = round_half_up(0.15 * cycle)
        expected_mid = cycle - expected_initial - expected_development - expected_late

        assert by_stage["initial"] == expected_initial, crop.code
        assert by_stage["development"] == expected_development, crop.code
        assert by_stage["late"] == expected_late, crop.code
        assert by_stage["mid"] == expected_mid, crop.code


async def test_maize_kc_mid_matches_fao56_table_12(db_session: AsyncSession) -> None:
    """FAO-56 (Allen et al., 1998) Table 12, Cereals: Maize (grain) Kc mid = 1.20."""
    repo = SqlAlchemyCropRepository(db_session)
    crops = await repo.list_all()

    maize = next(c for c in crops if c.code == "maize")
    mid = next(s for s in maize.stages if s.stage == "mid")

    assert maize.kc_source is KcSource.FAO56
    assert mid.kc == pytest.approx(1.20)
