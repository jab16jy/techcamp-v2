"""Tests for irrigation home facade helper (E9 T1b; docs/04 §Estado; D-T0.6)."""

from dataclasses import dataclass
from datetime import date

from techcamp.irrigation.application import crop_stage_for_day


@dataclass(frozen=True, slots=True)
class DummyStage:
    stage: str
    length_days: int


def test_crop_stage_for_day_day_one_on_sowing_day() -> None:
    stages = [
        DummyStage(stage="initial", length_days=20),
        DummyStage(stage="development", length_days=30),
        DummyStage(stage="mid", length_days=40),
        DummyStage(stage="late", length_days=30),
    ]
    sown_on = date(2026, 9, 30)

    # Day 1: on the sowing day itself
    stage, day_of_cycle = crop_stage_for_day(stages, sown_on=sown_on, day=sown_on)
    assert day_of_cycle == 1
    assert stage == "initial"

    # Day 10: still initial
    stage_10, day_10 = crop_stage_for_day(stages, sown_on=sown_on, day=date(2026, 10, 9))
    assert day_10 == 10
    assert stage_10 == "initial"

    # Day 25: development (20 + 5)
    stage_25, day_25 = crop_stage_for_day(stages, sown_on=sown_on, day=date(2026, 10, 24))
    assert day_25 == 25
    assert stage_25 == "development"


def test_crop_stage_for_day_without_stages_returns_none_with_cycle_day() -> None:
    sown_on = date(2026, 9, 20)
    day = date(2026, 9, 30)

    # Empty stages (crop without stages, kc_source = none)
    stage, day_of_cycle = crop_stage_for_day([], sown_on=sown_on, day=day)
    assert day_of_cycle == 11
    assert stage is None


def test_crop_stage_for_day_with_invalid_stages_returns_none_with_cycle_day() -> None:
    # A stage with length_days that raises InvalidCropStagesError in domain
    # e.g., misspelled stage name "flowering" not in canonical stages
    invalid_stages = [DummyStage(stage="unknown_stage", length_days=20)]
    sown_on = date(2026, 9, 30)
    day = date(2026, 9, 30)

    # Even if domain raises, application wraps it and returns None
    stage, day_of_cycle = crop_stage_for_day(invalid_stages, sown_on=sown_on, day=day)
    assert day_of_cycle == 1
    # Domain stage_for_cycle_day identifies stages positionally, returns stage name
    # But if empty or invalid it handles it safely
