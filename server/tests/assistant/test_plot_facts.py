"""The deterministic fact assembler (E12 T4; docs/06-diseno-detallado.md §9
"Hechos que recibe el LLM"; docs/05-arquitectura.md:150-151; D-T12.1, D-T12.6).

docs/06 §9: the assistant builds the plot's facts "de forma determinista desde
las fachadas, **antes de la única llamada al LLM**". This module is that build,
and every test here is one of the ways it can go quietly wrong:

* a cross-org plot that answers something instead of `PlotNotFoundError`;
* a sensor with no reading rendered as `0.0`;
* a resolved alert still listed as open;
* a plot whose recommendation was never calculated rendered as "regar 0 mm";
* a plot with no predictions given an invented `low` (`drought` has no registered
  predictor, D-T9.3);
* a contact detail or a geometry from a free-form JSONB object surviving into the
  block that leaves the server (D-T12.6, ADR-0007:39).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from assistant.doubles import (
    FakeAlertRepository,
    FakeIrrigationRecommendationRepository,
    FakeKnowledgeRepository,
    FakeMembershipRepository,
    FakePlotRepository,
    FakeReadingRepository,
    FakeRiskRepository,
)
from techcamp.alerts.domain.models import Alert, AlertState
from techcamp.alerts.domain.models import Severity as AlertSeverity
from techcamp.assistant.application.plot_facts import gather_plot_facts
from techcamp.assistant.domain.facts import ASSISTANT_METRICS, MissingFactKind
from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.farms.domain.models import IrrigationSystem, Plot
from techcamp.identity.domain.models import Membership, Role
from techcamp.irrigation.domain.models import (
    IrrigationRecommendation,
    RainfedAdvice,
    RecommendationKind,
)
from techcamp.risk.domain.models import (
    EventType,
    ModelVersion,
    RiskPrediction,
)
from techcamp.risk.domain.models import (
    Severity as RiskSeverity,
)
from techcamp.shared.ids import uuid7
from techcamp.telemetry.domain.models import ReadingPoint

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
TODAY = date(2026, 10, 6)

USER = UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER = UUID("22222222-2222-2222-2222-222222222222")
ORG = UUID("33333333-3333-3333-3333-333333333333")
OTHER_ORG = UUID("44444444-4444-4444-4444-444444444444")
FARM = UUID("55555555-5555-5555-5555-555555555555")
PLOT = UUID("66666666-6666-6666-6666-666666666666")
OTHER_PLOT = UUID("77777777-7777-7777-7777-777777777777")
CELL = 42


def _plot(*, plot_id: UUID = PLOT, org_id: UUID = ORG, cell_id: int | None = CELL) -> Plot:
    return Plot(
        id=plot_id,
        org_id=org_id,
        farm_id=FARM,
        name="Lote del sur",
        boundary="POLYGON((-74.1 10.9, -74.0 10.9, -74.0 11.0, -74.1 10.9))",
        area_ha=1.4,
        weather_cell_id=cell_id,
        irrigation_system=IrrigationSystem.DRIP,
        irrigation_efficiency=0.9,
        system_flow_lph=40.0,
    )


def _world(
    *,
    plots: tuple[Plot, ...] = (_plot(),),
    memberships: tuple[Membership, ...] = (Membership(org_id=ORG, user_id=USER, role=Role.OWNER),),
    readings: FakeReadingRepository | None = None,
    alerts: FakeAlertRepository | None = None,
    recommendations: FakeIrrigationRecommendationRepository | None = None,
    risk: FakeRiskRepository | None = None,
    knowledge: FakeKnowledgeRepository | None = None,
):
    """The six keyword-only dependencies `gather_plot_facts` takes, each one a
    double holding nothing unless a test asked for something."""
    return {
        "plots": FakePlotRepository(plots),
        "memberships": FakeMembershipRepository(memberships),
        "readings": readings if readings is not None else FakeReadingRepository(),
        "alerts": alerts if alerts is not None else FakeAlertRepository(),
        "recommendations": (
            recommendations
            if recommendations is not None
            else FakeIrrigationRecommendationRepository(missing=True)
        ),
        "risk": risk if risk is not None else FakeRiskRepository(),
        "knowledge": knowledge if knowledge is not None else FakeKnowledgeRepository(),
    }


def _gather(now: datetime = NOW, **overrides: object):
    world = _world(**overrides)  # type: ignore[arg-type]
    return gather_plot_facts(user_id=USER, plot_id=PLOT, now=now, **world)


# --- the org gate ---------------------------------------------------------


async def test_a_plot_of_another_org_raises_plot_not_found() -> None:
    """docs/09-cuellos-de-botella.md §Seguridad and docs/04-api.md
    §Convenciones HTTP: another organization's resource answers `404`, never `403`
    and never an empty `200`. `resolve_plot_access` makes a cross-org plot and an
    absent plot the same `None`, and the error propagates untouched so T5 maps one
    thing to one status."""
    other_org_plot = _plot(plot_id=OTHER_PLOT, org_id=OTHER_ORG)

    with pytest.raises(PlotNotFoundError):
        await _gather(plots=(other_org_plot,))


async def test_the_org_gate_runs_before_any_repository_is_read() -> None:
    """The gate is the **first** read, not one among five: a question about a plot
    the caller may not see must not reach a repository at all, and it certainly
    must not gather the facts and then refuse them."""
    readings = FakeReadingRepository()
    readings.add("soil_moisture", ReadingPoint(time=NOW - timedelta(hours=1), value=21.5))

    with pytest.raises(PlotNotFoundError):
        await _gather(plots=(), readings=readings)

    assert readings.latest_valid_by_metric_calls == []


async def test_a_user_of_another_org_reaches_no_plot_of_this_one() -> None:
    """The membership is what narrows the orgs the plot is looked up in, so a
    member of a different organization is the same `None`. The caller could not
    tell the two cases apart, which is the point."""
    world = _world(memberships=(Membership(org_id=OTHER_ORG, user_id=OTHER_USER, role=Role.OWNER),))

    with pytest.raises(PlotNotFoundError):
        await gather_plot_facts(user_id=OTHER_USER, plot_id=PLOT, now=NOW, **world)


# --- sensors --------------------------------------------------------------


async def test_the_sensors_are_the_facade_s_answer_for_exactly_the_six_metrics() -> None:
    """docs/06 §9: "última lectura calibrada por variable de la parcela". The
    facade is asked for `ASSISTANT_METRICS` and nothing else — the glossary's
    `battery_v` and `rssi` are node health, and D-T12.6 is a minimum-data rule."""
    readings = FakeReadingRepository()
    readings.add("soil_moisture", ReadingPoint(time=NOW - timedelta(hours=1), value=21.5))
    readings.add("air_temp", ReadingPoint(time=NOW - timedelta(hours=2), value=29.0))
    readings.add("battery_v", ReadingPoint(time=NOW - timedelta(hours=1), value=3.9))

    facts = await _gather(readings=readings)

    assert readings.latest_valid_by_metric_calls[0][0] == list(ASSISTANT_METRICS)
    assert [fact.metric for fact in facts.sensors] == ["soil_moisture", "air_temp"]
    assert [fact.value for fact in facts.sensors] == [21.5, 29.0]
    assert MissingFactKind.SENSORS not in facts.missing


async def test_a_metric_without_a_reading_is_absent_and_never_zero() -> None:
    """`query_latest_plot_readings` maps a metric with no valid reading in the
    window to `None`, and docs/06 §9's rule is "si no hay información suficiente,
    decirlo". A `0.0` there would be a finding: "the soil is bone dry" for a
    sensor that has simply never reported."""
    readings = FakeReadingRepository()
    readings.add("soil_moisture", ReadingPoint(time=NOW - timedelta(hours=1), value=0.0))

    facts = await _gather(readings=readings)

    assert [fact.metric for fact in facts.sensors] == ["soil_moisture"]
    assert facts.sensors[0].value == 0.0


async def test_a_plot_whose_sensors_are_silent_names_the_sensors_family_missing() -> None:
    """The negative case, and the common one: nodes offline for a week. The family
    is **named** absent rather than shown as an empty section that reads as
    "nothing to report"."""
    facts = await _gather()

    assert facts.sensors == ()
    assert MissingFactKind.SENSORS in facts.missing


async def test_a_reading_the_facade_returned_is_fresh_inside_the_facade_window() -> None:
    """docs/06 §9: "se marcan … si el dato no está fresco". With the default
    window the mark is **never** set, and that is the honest result rather than a
    gap: `SENSOR_FRESH_WINDOW` is the very window `query_latest_plot_readings`
    reads (D-T0.4), so it returns nothing older than that and every reading it
    returned is inside it. The mark exists for the reading that is about to leave
    the window, which is why the rule takes `fresh_after` — see the next test.

    A 23-hour-old soil-moisture reading is the near-miss case: it is what a node
    reporting once a day looks like, and calling it stale would be this module
    inventing an agronomic threshold no document states."""
    readings = FakeReadingRepository()
    readings.add("soil_moisture", ReadingPoint(time=NOW - timedelta(hours=23), value=21.5))
    readings.add("air_temp", ReadingPoint(time=NOW - timedelta(hours=5), value=29.0))

    facts = await _gather(readings=readings)

    assert [fact.is_stale for fact in facts.sensors] == [False, False]
    assert [fact.metric for fact in facts.sensors] == ["soil_moisture", "air_temp"]


async def test_the_freshness_window_can_be_tightened_by_the_caller() -> None:
    """The rule takes its number as a keyword (D-T0.4's window is a default, not a
    law), so a caller that wants to mark a six-hour-old reading stale can."""
    readings = FakeReadingRepository()
    readings.add("soil_moisture", ReadingPoint(time=NOW - timedelta(hours=5), value=21.5))

    facts = await gather_plot_facts(
        user_id=USER,
        plot_id=PLOT,
        now=NOW,
        fresh_after=timedelta(hours=1),
        **_world(readings=readings),  # type: ignore[arg-type]
    )

    assert facts.sensors[0].is_stale is True


# --- alerts ---------------------------------------------------------------


def _alert(
    *,
    alert_id: UUID,
    state: AlertState = AlertState.OPEN,
    severity: AlertSeverity = AlertSeverity.CRITICAL,
    opened_at: datetime = datetime(2026, 10, 6, 2, 0, tzinfo=UTC),
    acknowledged_at: datetime | None = None,
    evidence: dict | None = None,
    plot_id: UUID = PLOT,
    org_id: UUID = ORG,
) -> Alert:
    return Alert(
        state=state,
        severity=severity,
        opened_at=opened_at,
        id=alert_id,
        org_id=org_id,
        rule_id=uuid4(),
        rule_code="water_stress",
        plot_id=plot_id,
        node_id=None,
        evidence=evidence if evidence is not None else {},
        acknowledged_at=acknowledged_at,
    )


async def test_an_open_and_an_acknowledged_alert_are_both_facts_and_a_resolved_one_is_not() -> None:
    """docs/06 §9: "alertas **abiertas o reconocidas**", and docs/00's glossary:
    "Alerta abierta | `state <> 'resolved'` … Reconocerla no la cierra". So an
    acknowledged alert is still a fact, marked as acknowledged, and a resolved one
    is gone."""
    alerts = FakeAlertRepository(
        (
            _alert(alert_id=uuid7(), state=AlertState.OPEN),
            _alert(
                alert_id=uuid7(),
                state=AlertState.ACKNOWLEDGED,
                acknowledged_at=datetime(2026, 10, 6, 3, 0, tzinfo=UTC),
            ),
            _alert(alert_id=uuid7(), state=AlertState.RESOLVED),
        )
    )

    facts = await _gather(alerts=alerts)

    assert [fact.acknowledged for fact in facts.alerts] == [False, True]
    assert all(fact.rule_code == "water_stress" for fact in facts.alerts)
    assert all(fact.severity == "critical" for fact in facts.alerts)
    assert MissingFactKind.ALERTS not in facts.missing


async def test_a_plot_with_no_open_alert_names_the_alerts_family_missing() -> None:
    """Same empty list for "nothing is open" and "the evaluator found nothing":
    both are reported by name rather than as a section with nothing in it."""
    facts = await _gather()

    assert facts.alerts == ()
    assert MissingFactKind.ALERTS in facts.missing


async def test_the_alert_evidence_is_sanitized_before_it_becomes_a_fact() -> None:
    """D-T12.6, ADR-0007:39: no personal data leaves the server. `alert.evidence`
    is free-form JSONB written by four evaluators in another module, so this is
    where a phone number, an e-mail and a WKT geometry are stopped — not at render
    time, because the fact block is what is sent."""
    alerts = FakeAlertRepository(
        (
            _alert(
                alert_id=uuid7(),
                evidence={
                    "day": "2026-10-05",
                    "at": "2026-10-06T02:00:00+00:00",
                    "source": "balance",
                    "technician_phone": "+57 300 123 4567",
                    "farm_email": "camila@cooperative.co",
                    "boundary": "POLYGON((-74.1 10.9, -74.0 10.9, -74.0 11.0))",
                    "unknown_key": "invented",
                },
            ),
        )
    )

    facts = await _gather(alerts=alerts)

    assert facts.alerts[0].evidence == {
        "day": "2026-10-05",
        "at": "2026-10-06T02:00:00+00:00",
        "source": "balance",
    }


# --- irrigation -----------------------------------------------------------


def _recommendation(
    *,
    kind: RecommendationKind = RecommendationKind.IRRIGATE,
    advice: tuple[RainfedAdvice, ...] = (),
    rationale: dict | None = None,
    depth_mm: float | None = 12.5,
    duration_min: int | None = 35,
) -> IrrigationRecommendation:
    return IrrigationRecommendation(
        kind=kind,
        depth_mm=depth_mm,
        duration_min=duration_min,
        advice=advice,
        rationale=rationale if rationale is not None else {"et0_mm": 4.5, "kc": 0.9},
        plot_id=PLOT,
        day=TODAY,
    )


async def test_todays_recommendation_becomes_a_fact_with_its_own_rationale() -> None:
    """docs/06 §9: the day's `irrigation_recommendation` with its `kind`, its
    `rationale` and the depth and duration."""
    recommendations = FakeIrrigationRecommendationRepository((_recommendation(),))

    facts = await _gather(recommendations=recommendations)

    assert facts.irrigation is not None
    assert facts.irrigation.kind == "irrigate"
    assert facts.irrigation.depth_mm == 12.5
    assert facts.irrigation.duration_min == 35
    assert facts.irrigation.day == TODAY
    assert facts.irrigation.rationale == {"et0_mm": 4.5, "kc": 0.9}
    assert MissingFactKind.IRRIGATION not in facts.missing


async def test_a_rainfed_recommendation_keeps_its_advice_codes_and_has_no_depth() -> None:
    """ADR-0023: a parcel of secano gets a recommendation with advice codes and no
    depth or duration. Both are absent, which is a **rainfed recommendation**, not
    a missing one — the difference the fact family depends on."""
    recommendations = FakeIrrigationRecommendationRepository(
        (
            _recommendation(
                kind=RecommendationKind.RAINFED,
                advice=(RainfedAdvice.DELAY_SOWING, RainfedAdvice.CONSERVE_MOISTURE),
                depth_mm=None,
                duration_min=None,
            ),
        )
    )

    facts = await _gather(recommendations=recommendations)

    assert facts.irrigation is not None
    assert facts.irrigation.kind == "rainfed"
    assert facts.irrigation.depth_mm is None
    assert facts.irrigation.duration_min is None
    assert facts.irrigation.advice == ("delay_sowing", "conserve_moisture")
    assert MissingFactKind.IRRIGATION not in facts.missing


async def test_a_plot_whose_recommendation_was_never_calculated_names_the_family_missing() -> None:
    """`query_plot_recommendation` raises `RecommendationNotFoundError` and the
    fact builder catches it. A missing recommendation is the **normal** state of a
    plot whose 04:30 job has not run, and the alternative — `depth_mm = 0.0` — is a
    decision to irrigate nothing that no calculation ever made."""
    facts = await _gather(recommendations=FakeIrrigationRecommendationRepository(missing=True))

    assert facts.irrigation is None
    assert MissingFactKind.IRRIGATION in facts.missing


# --- risk -----------------------------------------------------------------


def _version(*, is_baseline: bool = True) -> ModelVersion:
    return ModelVersion(
        id=uuid7(),
        name="risk_flood",
        version="2026-10-06",
        artifact_uri="s3://models/flood/climatology.json",
        is_baseline=is_baseline,
        thresholds={},
        promoted=False,
        created_at=datetime(2026, 10, 6, 6, 0, tzinfo=UTC),
        baseline_metrics={"pr_auc": 0.44, "brier": 0.21},
    )


def _prediction(
    version: ModelVersion, *, cell_id: int = CELL, top_factors: list[dict] | None = None
) -> RiskPrediction:
    return RiskPrediction(
        id=uuid7(),
        cell_id=cell_id,
        model_version_id=version.id,
        event_type=EventType.FLOOD,
        horizon_start=date(2026, 10, 1),
        horizon_days=30,
        probability=0.12,
        severity=RiskSeverity.LOW,
        top_factors=top_factors
        if top_factors is not None
        else [{"feature": "month", "value": 10.0, "contribution": 0.12}],
        created_at=datetime(2026, 10, 6, 6, 0, tzinfo=UTC),
    )


async def test_the_risk_fact_reuses_the_risk_facade_and_names_the_served_baseline() -> None:
    """docs/06 §9: the prediction travels with "el modelo que la produjo", and
    docs/04 §Riesgo serves it with `is_baseline`. The served version is
    `climatology_month` with `thresholds={}`, so the `low` it reports is a missing
    operating point (D-T9.2) — which is why the fact carries `is_baseline` and the
    renderer may not narrate the severity.

    The read is the **one** `query_plot_risk` does: a second read of the risk
    tables for the same plot would be a second answer to a question the module
    already answered."""
    version = _version()
    risk = FakeRiskRepository(versions=(version,), predictions=(_prediction(version),))

    facts = await _gather(risk=risk)

    assert len(facts.risk) == 1
    flood = facts.risk[0]
    assert flood.event_type == "flood"
    assert flood.probability == 0.12
    assert flood.model_version == "risk_flood 2026-10-06"
    assert flood.is_baseline is True
    assert flood.metrics == {"pr_auc": 0.44, "brier": 0.21}
    assert flood.top_factors == ({"feature": "month", "value": 10.0, "contribution": 0.12},)
    assert MissingFactKind.RISK not in facts.missing


async def test_the_risk_factors_and_metrics_are_sanitized_like_every_other_object() -> None:
    """`top_factors` and `model_version.metrics` are JSONB written by the ML
    harness, so they get the same allowlist and the same ban-list as the alert's
    evidence."""
    version = _version()
    risk = FakeRiskRepository(
        versions=(version,),
        predictions=(
            _prediction(
                version,
                top_factors=[
                    {
                        "feature": "month",
                        "value": 10.0,
                        "contribution": 0.12,
                        "artifact_uri": "s3://models/secret.json",
                        "api_key": "sk-live-123",
                    }
                ],
            ),
        ),
    )

    facts = await _gather(risk=risk)

    assert facts.risk[0].top_factors == ({"feature": "month", "value": 10.0, "contribution": 0.12},)


async def test_a_plot_with_no_predictions_names_the_risk_family_missing() -> None:
    """D-T9.3: `drought` has no registered predictor, and a plot without a climate
    cell has no predictions at all. `query_plot_risk` answers `[]` for both, and
    the fact is **absent** — never a `low` severity for an event nobody predicted
    (docs/04 §Riesgo: "una parcela sin celda responde `[]`")."""
    facts = await _gather()

    assert facts.risk == ()
    assert MissingFactKind.RISK in facts.missing


async def test_a_plot_without_a_climate_cell_has_no_risk_either() -> None:
    """`query_plot_risk` short-circuits on `weather_cell_id is None`, so the fact
    builder sees the same `[]` and the same missing family."""
    facts = await _gather(plots=(_plot(cell_id=None),))

    assert facts.risk == ()
    assert MissingFactKind.RISK in facts.missing


async def test_an_event_with_no_registered_version_does_not_appear() -> None:
    """D-T9.3 again, from the other side: only `risk_flood` has a registered
    version, so only `flood` is a prediction. The family is present (one
    prediction exists) and does not claim anything about `drought`."""
    version = _version()
    risk = FakeRiskRepository(versions=(version,), predictions=(_prediction(version),))

    facts = await _gather(risk=risk)

    assert [item.event_type for item in facts.risk] == ["flood"]
    assert MissingFactKind.RISK not in facts.missing


# --- seasonal climate -----------------------------------------------------


async def test_the_departments_own_bulletin_is_the_seasonal_fact() -> None:
    """docs/06 §9: "el último boletín agroclimático del departamento de la finca …
    con su fecha de publicación", and `kb_document.enso_state` read from it."""
    knowledge = FakeKnowledgeRepository()
    knowledge.add_document(
        document_id=uuid7(),
        title="Mesa Técnica Agroclimática de Córdoba, octubre 2026",
        kind="agroclimatic_bulletin",
        source_url="https://example.org/cordoba-oct-2026",
        license="CC-BY-4.0",
        published_on=date(2026, 9, 15),
        department="Córdoba",
        enso_state="el_nino",
    )

    facts = await gather_plot_facts(
        user_id=USER, plot_id=PLOT, department="Córdoba", now=NOW, **_world(knowledge=knowledge)
    )

    assert facts.seasonal_climate is not None
    assert facts.seasonal_climate.department == "Córdoba"
    assert facts.seasonal_climate.published_on == date(2026, 9, 15)
    assert facts.seasonal_climate.enso_state == "el_nino"
    assert facts.seasonal_climate.source_url == "https://example.org/cordoba-oct-2026"
    assert MissingFactKind.SEASONAL_CLIMATE not in facts.missing


async def test_the_national_bulletin_answers_when_the_department_has_none() -> None:
    """docs/06 §9 says it in one clause: "o del nacional si no hay uno regional".
    Nothing in the repository maps a plot to a department — `Farm.municipality_code`
    is free-text DIVIPOLA and there is no `municipality` table — so the national
    bulletin is the honest answer for a question that names no department, and it
    is a **fact**, not a gap."""
    knowledge = FakeKnowledgeRepository()
    knowledge.add_document(
        document_id=uuid7(),
        title="Boletín Agroclimático Nacional, octubre 2026",
        kind="agroclimatic_bulletin",
        source_url="https://example.org/ban-oct-2026",
        published_on=date(2026, 10, 1),
        department=None,
        enso_state="neutral",
    )
    knowledge.add_document(
        document_id=uuid7(),
        title="Guía de manejo del ají",
        kind="guide",
        published_on=date(2026, 10, 5),
    )

    facts = await gather_plot_facts(
        user_id=USER, plot_id=PLOT, department="Sucre", now=NOW, **_world(knowledge=knowledge)
    )

    assert facts.seasonal_climate is not None
    assert facts.seasonal_climate.title == "Boletín Agroclimático Nacional, octubre 2026"
    assert facts.seasonal_climate.department is None
    assert MissingFactKind.SEASONAL_CLIMATE not in facts.missing


async def test_an_empty_corpus_names_the_seasonal_climate_family_missing() -> None:
    """No bulletin in the corpus is missing evidence, not a neutral ENSO state."""
    facts = await _gather()

    assert facts.seasonal_climate is None
    assert MissingFactKind.SEASONAL_CLIMATE in facts.missing


# --- the block as a whole -------------------------------------------------


async def test_the_same_evidence_produces_equal_facts_every_time() -> None:
    """docs/06 §9: "de forma determinista", and ADR-0007:26's one call over a
    deterministic flow. Two runs over the same doubles must be equal, or the same
    question gets two different answers and no test anywhere can say which is
    right."""
    version = _version()
    knowledge = FakeKnowledgeRepository()
    knowledge.add_document(
        document_id=uuid7(),
        title="Boletín Agroclimático Nacional, octubre 2026",
        kind="agroclimatic_bulletin",
        published_on=date(2026, 10, 1),
        department=None,
        enso_state="neutral",
    )
    readings = FakeReadingRepository()
    readings.add("soil_moisture", ReadingPoint(time=NOW - timedelta(hours=1), value=21.5))
    readings.add("air_rh", ReadingPoint(time=NOW - timedelta(hours=2), value=78.0))

    world = _world(
        readings=readings,
        alerts=FakeAlertRepository((_alert(alert_id=uuid7()),)),
        recommendations=FakeIrrigationRecommendationRepository((_recommendation(),)),
        risk=FakeRiskRepository(versions=(version,), predictions=(_prediction(version),)),
        knowledge=knowledge,
    )

    first = await gather_plot_facts(user_id=USER, plot_id=PLOT, now=NOW, **world)
    second = await gather_plot_facts(user_id=USER, plot_id=PLOT, now=NOW, **world)

    assert first == second
    assert first.missing == ()
    assert first.plot_name == "Lote del sur"


async def test_a_plot_with_nothing_at_all_names_all_five_families_in_a_fixed_order() -> None:
    """The worst case in production: silent nodes, jobs that never ran, a plot
    without a cell and a corpus with no bulletin. Every family is named, in
    `MissingFactKind`'s own order, so the sentence the answer owes the producer
    reads the same way every time."""
    facts = await _gather(plots=(_plot(cell_id=None),))

    assert facts.missing == (
        MissingFactKind.SENSORS,
        MissingFactKind.ALERTS,
        MissingFactKind.IRRIGATION,
        MissingFactKind.SEASONAL_CLIMATE,
        MissingFactKind.RISK,
    )


async def test_no_contact_detail_and_no_geometry_survives_into_the_whole_block() -> None:
    """The D-T12.6 send-list test at the assembler, over the three free-form
    objects a plot's facts are built from at once: the alert's `evidence`, the
    recommendation's `rationale` and the prediction's `top_factors`.

    Each leak arrives the way it would in production — a **denied key** nobody
    allowlists, a key the allowlist does not know, and an allowlisted key whose
    *value* is a bare coordinate pair — because those are three different filters
    and a test that only used the first would leave the other two unproven."""
    version = _version()
    alerts = FakeAlertRepository(
        (
            _alert(
                alert_id=uuid7(),
                evidence={
                    "at": "2026-10-06T02:00:00+00:00",
                    "phone": "+57 300 123 4567",
                    "farm_email": "camila@cooperative.co",
                    "source": "-74.1,10.9",
                    "invented_key": "+573001234567",
                },
            ),
        )
    )
    recommendations = FakeIrrigationRecommendationRepository(
        (
            _recommendation(
                rationale={
                    "et0_mm": 4.5,
                    "correo": "camila@cooperative.co",
                    "location": "POINT(-74.1 10.9)",
                    "kc_source": "-74.1,10.9",
                }
            ),
        )
    )
    risk = FakeRiskRepository(
        versions=(version,),
        predictions=(
            _prediction(
                version,
                top_factors=[
                    {
                        "feature": "month",
                        "value": 10.0,
                        "contribution": 0.12,
                        "artifact_uri": "s3://models/flood/climatology.json",
                        "api_key": "sk-live-123",
                    }
                ],
            ),
        ),
    )

    facts = await _gather(alerts=alerts, recommendations=recommendations, risk=risk)

    block = repr(facts)
    for leaked in (
        "300 123 4567",
        "camila@cooperative.co",
        "POINT(-74.1 10.9)",
        "-74.1,10.9",
        "sk-live-123",
        "artifact_uri",
        "invented_key",
    ):
        assert leaked not in block, leaked
    assert facts.alerts[0].evidence == {"at": "2026-10-06T02:00:00+00:00"}
    assert facts.irrigation is not None
    assert facts.irrigation.rationale == {"et0_mm": 4.5}
    assert facts.risk[0].top_factors == ({"feature": "month", "value": 10.0, "contribution": 0.12},)


async def test_the_plot_geometry_is_not_part_of_the_facts() -> None:
    """D-T12.6: no `farm`/`plot` geometry leaves the server. `Plot.boundary` is a
    WKT polygon, and the facts carry `plot_id` and `plot_name` and nothing else
    about where the plot is."""
    facts = await _gather()

    assert facts.plot_id == PLOT
    assert facts.plot_name == "Lote del sur"
    assert "POLYGON" not in repr(facts)
