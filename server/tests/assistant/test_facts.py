"""The fact value objects (E12 T4; docs/06-diseno-detallado.md §9 "Hechos que
recibe el LLM"; docs/00-glosario.md; D-T12.1, D-T12.6).

These are the five families docs/06 §9 enumerates — sensores, alertas, riego,
clima estacional, riesgo climático — as frozen values, plus the vocabulary that
says one of them is **absent**. Pure: no I/O, no clock read (the instant arrives
as an argument), and no geometry, phone or e-mail anywhere in the shape
(D-T12.6).

Two rules are pinned with negative assertions because both are easy to get
subtly wrong and both fail the producer silently:

* **Absence is never `0`.** A metric with no reading is a missing fact, not a
  `0.0`; a plot with no predictions has no risk, not a `low` (D-T12.1, D-T9.3:
  `drought` has no registered predictor at all).
* **`SENSOR_FRESH_WINDOW` is not an agronomic threshold.** It is the very window
  `query_latest_plot_readings` itself reads (D-T0.4), so "stale" means "at the
  edge of the window the facade reads" and the number is not invented here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from techcamp.assistant.domain.facts import (
    ASSISTANT_METRICS,
    SENSOR_FRESH_WINDOW,
    AlertFact,
    IrrigationFact,
    MissingFactKind,
    PlotFacts,
    RiskFact,
    SeasonalClimateFact,
    SensorFact,
    is_stale,
)

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)

_PLOT_ID = UUID("33333333-3333-3333-3333-333333333333")


def _facts(**overrides: object) -> PlotFacts:
    base: dict[str, object] = {
        "plot_id": _PLOT_ID,
        "plot_name": "Lote del sur",
        "sensors": (
            SensorFact(metric="soil_moisture", value=21.5, reading_time=NOW, is_stale=False),
        ),
        "alerts": (),
        "irrigation": None,
        "seasonal_climate": None,
        "risk": (),
        "missing": (),
    }
    return PlotFacts(**{**base, **overrides})  # type: ignore[arg-type]


def test_the_assistant_reads_the_glossary_six_agronomic_variables() -> None:
    """docs/00-glosario.md, `Variable` → `metric`: `soil_moisture`, `soil_temp`,
    `air_temp`, `air_rh`, `rain`, `water_flow` — plus `battery_v` and `rssi`.

    The last two are deliberately **not** here. They are node health, not
    agronomy: a producer asking "¿por qué no regué?" needs to know the soil, the
    weather and the water that went through the meter. A battery voltage answers
    a technician's question, and D-T12.6 is a minimum-data rule, so a fact the
    agronomic answer cannot use is data that leaves the server for nothing."""
    assert ASSISTANT_METRICS == (
        "soil_moisture",
        "soil_temp",
        "air_temp",
        "air_rh",
        "rain",
        "water_flow",
    )
    assert "battery_v" not in ASSISTANT_METRICS
    assert "rssi" not in ASSISTANT_METRICS


def test_the_fresh_window_is_the_window_the_telemetry_facade_reads() -> None:
    """D-T0.4: `query_latest_plot_readings` returns the newest valid reading per
    metric within `[now - 24h, now]`. So `SENSOR_FRESH_WINDOW` is that window and
    not an agronomic freshness threshold invented by the assistant: a reading the
    facade returned is by definition inside it, and the mark exists to say a
    value is about to leave the window."""
    assert SENSOR_FRESH_WINDOW == timedelta(hours=24)


def test_a_reading_inside_the_window_is_fresh_and_one_at_its_edge_is_not() -> None:
    """One minute inside is fresh; one minute outside is stale. The boundary is
    strict (`>`), so a reading exactly at `now - 24h` is still inside the window
    the facade read."""
    assert not is_stale(NOW - timedelta(hours=1), NOW)
    assert not is_stale(NOW - SENSOR_FRESH_WINDOW, NOW)
    assert is_stale(NOW - timedelta(hours=24, minutes=1), NOW)
    assert is_stale(NOW - timedelta(days=3), NOW)


def test_the_fresh_window_can_be_overridden_by_the_caller() -> None:
    """A rule that takes its number as a keyword is a rule an endpoint can tune
    without editing the domain; the default is the facade's window."""
    assert is_stale(NOW - timedelta(hours=2), NOW, fresh_after=timedelta(hours=1))
    assert not is_stale(NOW - timedelta(hours=2), NOW, fresh_after=timedelta(hours=3))


def test_a_naive_instant_is_read_as_utc_never_as_local_time() -> None:
    """The convention of every other read in the application
    (`techcamp.shared.dates.local_today`, `domain.limits._in_bogota`): a naive
    value is UTC. A naive `now` compared as local time would be wrong by five
    hours, and it would be wrong in the evening — exactly when a producer asks
    why he did not irrigate."""
    assert is_stale(datetime(2026, 10, 6, 10, 0), datetime(2026, 10, 6, 14, 0)) is False
    assert is_stale(datetime(2026, 10, 5, 10, 0), datetime(2026, 10, 6, 14, 0)) is True


def test_a_missing_family_is_named_and_never_faked_as_a_value() -> None:
    """D-T12.1 and the docs/06 §9 rule "si no hay información suficiente,
    decirlo". The five names are the whole vocabulary: a sensor with no reading,
    no alerts, no stored recommendation, no bulletin in the corpus and no risk
    prediction. `drought` has no registered predictor (D-T9.3), so the risk fact
    is routinely absent and must be reported as such."""
    assert {kind.value for kind in MissingFactKind} == {
        "sensors",
        "alerts",
        "irrigation",
        "seasonal_climate",
        "risk",
    }


def test_plot_facts_agree_when_the_same_facts_are_given() -> None:
    """Facts are assembled once, before the single call, and the flow has to be
    deterministic (ADR-0007:26): two runs over the same evidence must produce
    equal facts, or the same question gets two different answers."""
    assert _facts() == _facts()
    assert _facts() == PlotFacts(
        plot_id=_PLOT_ID,
        plot_name="Lote del sur",
        sensors=(SensorFact(metric="soil_moisture", value=21.5, reading_time=NOW, is_stale=False),),
        alerts=(),
        irrigation=None,
        seasonal_climate=None,
        risk=(),
        missing=(),
    )


def test_plot_facts_carry_no_geometry_and_no_contact_field() -> None:
    """D-T12.6: no `farm`/`plot` geometry and no personal data leave the server.
    The guarantee is a shape, so it is asserted on the shape: there is no
    attribute to put a boundary, a location or a phone on, in this module or in
    any of the six facts it holds."""
    forbidden = ("boundary", "location", "municipality_code", "geometry", "phone", "email")
    names = set(PlotFacts.__slots__)
    for family in (SensorFact, AlertFact, IrrigationFact, SeasonalClimateFact, RiskFact):
        names |= set(family.__slots__)

    assert names.isdisjoint(forbidden)


def test_an_alert_fact_carries_its_severity_time_and_sanitized_evidence() -> None:
    """docs/06 §9: "Alertas: alertas abiertas o reconocidas de la parcela con su
    `severity`, `opened_at` y `evidence`." `acknowledged` is a `bool` about the
    alert's own state (docs/00: reconocerla no la cierra), never a severity."""
    alert = AlertFact(
        rule_code="water_stress",
        severity="critical",
        opened_at=datetime(2026, 10, 6, 2, 0, tzinfo=UTC),
        acknowledged=True,
        evidence={"day": "2026-10-05", "source": "balance"},
    )

    assert alert.rule_code == "water_stress"
    assert alert.severity == "critical"
    assert alert.acknowledged is True
    assert alert.evidence == {"day": "2026-10-05", "source": "balance"}


def test_an_irrigation_fact_carries_either_the_lamina_or_the_rainfed_advice() -> None:
    """docs/06 §9: "según el caso, `depth_mm` y `duration_min` o los consejos de
    secano (`advice`)". A rainfed plot (ADR-0023) has a recommendation with advice
    codes and no depth; both fields are optional so neither case is faked."""
    rainfed = IrrigationFact(
        day=date(2026, 10, 6),
        kind="rainfed",
        rationale={"depletion_mm": 41.0},
        depth_mm=None,
        duration_min=None,
        advice=("delay_sowing", "conserve_moisture"),
    )

    assert rainfed.depth_mm is None
    assert rainfed.duration_min is None
    assert rainfed.advice == ("delay_sowing", "conserve_moisture")


def test_a_seasonal_climate_fact_carries_the_enso_state_and_the_publication_date() -> None:
    """docs/06 §9: "el estado ENSO y la perspectiva de lluvia del último boletín
    agroclimático del departamento de la finca, o del nacional si no hay uno
    regional, con su fecha de publicación"."""
    bulletin = SeasonalClimateFact(
        title="Boletín Agroclimático Nacional, octubre 2026",
        department=None,
        published_on=date(2026, 10, 1),
        enso_state="neutral",
        source_url="https://example.org/ban-oct-2026",
    )

    assert bulletin.department is None
    assert bulletin.published_on == date(2026, 10, 1)
    assert bulletin.enso_state == "neutral"


def test_a_risk_fact_carries_its_model_version_and_whether_that_version_is_a_baseline() -> None:
    """docs/06 §9 and docs/04-api.md §Riesgo: the prediction travels with the
    `model_version` that produced it, and the answer says whether that version is
    a registered baseline. The served version is `climatology_month` with
    `thresholds={}`, so **every** prediction it serves is `low` (D-T9.2) — an
    unmarked `low` would present missing calibration as a finding."""
    risk = RiskFact(
        event_type="flood",
        horizon_start=date(2026, 10, 1),
        horizon_days=30,
        probability=0.12,
        severity="low",
        top_factors=({"feature": "month", "value": 10.0, "contribution": 0.12},),
        model_version="climatology_month 2026-10-06",
        metrics={"pr_auc": 0.44, "brier": 0.21},
        is_baseline=True,
    )

    assert risk.model_version == "climatology_month 2026-10-06"
    assert risk.is_baseline is True
    assert risk.metrics == {"pr_auc": 0.44, "brier": 0.21}


def test_a_risk_fact_whose_version_has_no_metrics_keeps_them_absent() -> None:
    """docs/04 §Riesgo: "`model_version.metrics` … `null` solo si la fila no tiene
    ninguna de las dos". Absent, not `{}` and not `0`: an empty object would read
    as "measured and found nothing"."""
    risk = RiskFact(
        event_type="flood",
        horizon_start=date(2026, 10, 1),
        horizon_days=30,
        probability=0.12,
        severity="low",
        top_factors=(),
        model_version="climatology_month 2026-10-06",
        metrics=None,
        is_baseline=True,
    )

    assert risk.metrics is None


def test_a_plot_without_evidence_lists_every_family_it_is_missing() -> None:
    """The negative case that matters most in production: a plot whose sensors
    are silent, whose jobs have not run and whose cell has no prediction yet.
    Nothing here is a zero, an empty finding or an invented severity."""
    facts = _facts(sensors=(), missing=tuple(MissingFactKind))

    assert facts.sensors == ()
    assert facts.risk == ()
    assert facts.irrigation is None
    assert facts.seasonal_climate is None
    assert set(facts.missing) == set(MissingFactKind)


def test_the_facts_are_frozen_so_no_reader_can_rewrite_what_the_call_sends() -> None:
    """The fact block is built once and read by the prompt builder and by the
    degraded renderer. A mutable fact would let the first reader change what the
    second one says, and D-T12.6's send-list would be a claim about a moment
    rather than about the value."""
    facts = _facts()

    with pytest.raises(AttributeError):
        facts.plot_name = "otro lote"  # type: ignore[misc]


def test_a_fact_cannot_be_rewritten_through_the_mapping_it_was_built_from() -> None:
    """R3-MUTABLE-FACT-MAPS, the half `frozen=True` does not cover. Rebinding a
    field is refused by the dataclass, but a `dict` the fact points at is still the
    caller's to write to — including **after** the sanitizer dropped a phone from
    it, which would put that phone back into the block that leaves the server.

    The producer of the `dict` below keeps it, so the test is the real shape: not
    "mutate the fact's field", but "mutate the object the fact was built from"."""
    evidence: dict[str, object] = {"at": "2026-10-06T14:00:00+00:00"}
    alert = AlertFact(
        rule_code="water_stress",
        severity="warning",
        opened_at=datetime(2026, 10, 6, 6, 0, tzinfo=UTC),
        acknowledged=False,
        evidence=evidence,  # type: ignore[arg-type]
    )

    evidence["technician_phone"] = "6041234567"

    assert dict(alert.evidence) == {"at": "2026-10-06T14:00:00+00:00"}
    with pytest.raises(TypeError):
        alert.evidence["contact"] = "camila@cooperative.co"  # type: ignore[index]
