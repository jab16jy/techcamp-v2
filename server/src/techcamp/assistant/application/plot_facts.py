"""The plot's facts, assembled from the five facades, before the call (E12 T4;
docs/06-diseno-detallado.md §9 "Hechos que recibe el LLM"; docs/05-arquitectura.md
§Módulos:150-151; ADR-0007:16,26; ADR-0023; D-T9.2, D-T9.3, D-T12.1, D-T12.6).

**Facts before the single LLM call.** docs/06 §9 draws the flow as: check the
limit, then gather the facts from the facades, then retrieve the fragments, then
**one** call. ADR-0007:26 records that v1's graph was linear with no tool loop and
no checkpoints, and that v2's is the same shape without the framework — so this
module is a straight-line read of five facades and a `PlotFacts` value, and there
is no iteration, no agent and no second look at the data.

**This module holds no LLM port at all.** That is structural, not a promise: the
same argument `application.usage` makes for "budget exhausted → zero provider
calls" (this function's signature has no `LLMClient`), which is why no test here
needs a provider double and why the degraded path's zero-calls guarantee is a
property of the shapes rather than of a review.

Five facades, each reached through its **public `application` face** and never
through another module's `domain` or `adapters` (docs/05:150-151), which is why
`Plot`, `Alert`, `ReadingPoint`, `StoredIrrigationRecommendation` and
`PlotRiskPrediction` are *projected* into `assistant`'s own facts rather than
re-exported — the same thing `home/application/plot_status.py` does, and its
module docstring is the precedent this one follows:

* **`telemetry`** — `query_latest_plot_readings` over `ASSISTANT_METRICS`. A
  metric that maps to `None` is **absent evidence**, never `0.0` (D-T12.1), and
  every reading kept carries `is_stale`.
* **`alerts`** — `list_open_alerts_for_plots`: open **and** acknowledged
  (docs/06 §9; docs/00: "alerta abierta" is `state <> 'resolved'`), with the
  evidence sanitized against the evaluators' own keys (D-T12.6).
* **`irrigation`** — `query_plot_recommendation`, catching
  `RecommendationNotFoundError` into a **missing family** instead of a zero depth.
* **`risk`** — **`query_plot_risk`, reused**, never a second read of
  `risk_prediction` and `model_version`. That call already answers the org check,
  already flattens `version_name` / `version_version` / `is_baseline` / `metrics`
  and already returns `[]` for a plot without a cell, so a direct read would be a
  second answer to a question this epic does not need to ask twice.
* **`assistant` itself** — `KnowledgeRepository.latest_climate_bulletin`, which is
  the one read of this module's own port.

**Deterministic.** No clock is read here: `now` arrives as an argument, so the
facts under test are decided by the values under test. Every order is a
documented one — sensors in `ASSISTANT_METRICS`, alerts in the facade's own
critical-first order, `missing` in `MissingFactKind`'s — and none of them comes
from iterating a `set`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from techcamp.alerts.application import list_open_alerts_for_plots
from techcamp.alerts.application.ports import AlertRepository
from techcamp.assistant.application.ports import KnowledgeDocument, KnowledgeRepository
from techcamp.assistant.domain.fact_safety import (
    ALERT_EVIDENCE_KEYS,
    IRRIGATION_RATIONALE_KEYS,
    RISK_FACTOR_KEYS,
    RISK_METRICS_KEYS,
    sanitize_fact_values,
)
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
from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import PlotRepository
from techcamp.identity.application.ports import MembershipRepository
from techcamp.irrigation.application import RecommendationNotFoundError, query_plot_recommendation
from techcamp.irrigation.application.ports import IrrigationRecommendationRepository
from techcamp.risk.application.ports import RiskRepository
from techcamp.risk.application.query_plot_risk import PlotRiskPrediction, query_plot_risk
from techcamp.telemetry.application import query_latest_plot_readings
from techcamp.telemetry.application.ports import ReadingRepository


async def gather_plot_facts(
    *,
    user_id: UUID,
    plot_id: UUID,
    now: datetime,
    readings: ReadingRepository,
    alerts: AlertRepository,
    recommendations: IrrigationRecommendationRepository,
    risk: RiskRepository,
    knowledge: KnowledgeRepository,
    plots: PlotRepository,
    memberships: MembershipRepository,
    department: str | None = None,
    fresh_after: timedelta | None = None,
) -> PlotFacts:
    """Everything the answer may say about one plot (docs/06-diseno-detallado.md §9).

    **The access gate is the first read**, and `PlotNotFoundError` is allowed to
    propagate: a plot that does not exist and a plot of another organization are
    the same absence through `resolve_plot_access`, and T5 maps that one error to
    `404` (docs/04-api.md §Convenciones HTTP, docs/09-cuellos-de-botella.md
    §Seguridad). Nothing else runs before it, so a question about a plot the
    caller may not see never reaches a repository.

    `department` is `None` for any question that does not name one — and that is
    the normal case, because nothing in this repository maps a farm to a
    department (`Farm.municipality_code` is free-text DIVIPOLA and there is no
    `municipality` table). The national bulletin answers, which docs/06 §9 allows
    by name; see `KnowledgeRepository.latest_climate_bulletin` for the decision.

    `fresh_after` narrows `SENSOR_FRESH_WINDOW` (D-T0.4's window, the one the
    telemetry facade itself reads) and is not an agronomic threshold this module
    gets to choose.

    Raises `farms`' `PlotNotFoundError` and nothing else: a missing
    recommendation is a **missing family**, not an error, and so are absent risk
    predictions and an empty corpus.
    """
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    window = SENSOR_FRESH_WINDOW if fresh_after is None else fresh_after

    sensors = await _sensors(
        plot_id=plot.id, org_id=plot.org_id, now=now, window=window, readings=readings
    )
    open_alerts = await list_open_alerts_for_plots(
        plot_ids=[plot.id], org_ids=[plot.org_id], alerts=alerts
    )
    irrigation = await _irrigation(
        user_id=user_id,
        plot_id=plot.id,
        now=now,
        plots=plots,
        recommendations=recommendations,
        memberships=memberships,
    )
    predictions = await query_plot_risk(
        user_id=user_id, plot_id=plot.id, plots=plots, memberships=memberships, risk=risk
    )
    bulletin = await knowledge.latest_climate_bulletin(department=department)

    # The alert projection is inline and not a named helper because annotating its
    # parameter would mean importing `alerts.domain.models.Alert`, and
    # docs/05-arquitectura.md:150-151 says a cross-module read goes through that
    # module's `application` face and never through its `domain`. `home`'s
    # `build_plot_status` projects its alerts the same way, for the same reason.
    alert_facts = tuple(
        AlertFact(
            rule_code=alert.rule_code,
            severity=alert.severity.value,
            opened_at=alert.opened_at,
            # docs/00: "Reconocerla no la cierra", so an acknowledged alert is a
            # fact and this says which of the two it is.
            acknowledged=alert.acknowledged_at is not None,
            evidence=sanitize_fact_values(alert.evidence, allowed_keys=ALERT_EVIDENCE_KEYS),
        )
        for alert in open_alerts
    )

    return PlotFacts(
        plot_id=plot.id,
        plot_name=plot.name,
        sensors=sensors,
        alerts=alert_facts,
        irrigation=irrigation,
        seasonal_climate=None if bulletin is None else _seasonal_climate(bulletin),
        risk=tuple(_risk_fact(prediction) for prediction in predictions),
        missing=_missing(
            has_sensors=bool(sensors),
            has_alerts=bool(alert_facts),
            has_irrigation=irrigation is not None,
            has_climate=bulletin is not None,
            has_risk=bool(predictions),
        ),
    )


def _risk_fact(prediction: PlotRiskPrediction) -> RiskFact:
    """One served prediction projected into this module's own `RiskFact`.

    Every field is projected rather than re-exported: the fact travels as the fact
    block that leaves the server (D-T12.6), and a re-exported foreign object would
    make that block depend on another module's dataclass — including whatever it
    carries that the send-list does not allow.

    `query_plot_risk` already flattened the nested `model_version`
    (docs/04-api.md §Riesgo); the fact joins the two halves into the label an
    answer reads and keeps `is_baseline` beside it, because a baseline's `low` is a
    missing operating point (D-T9.2) and a fact that hid the flag would let an
    unmarked `low` be narrated as a verdict.
    """
    return RiskFact(
        event_type=prediction.event_type.value,
        horizon_start=prediction.horizon_start,
        horizon_days=prediction.horizon_days,
        probability=prediction.probability,
        severity=prediction.severity.value,
        top_factors=tuple(
            sanitize_fact_values(factor, allowed_keys=RISK_FACTOR_KEYS)
            for factor in prediction.top_factors
        ),
        model_version=f"{prediction.version_name} {prediction.version_version}",
        # `None` and not `{}`: docs/04 §Riesgo allows exactly this `null` when the
        # version's row has neither `metrics` nor `baseline_metrics`.
        metrics=(
            None
            if prediction.metrics is None
            else sanitize_fact_values(prediction.metrics, allowed_keys=RISK_METRICS_KEYS)
        ),
        is_baseline=prediction.is_baseline,
    )


def _seasonal_climate(bulletin: KnowledgeDocument) -> SeasonalClimateFact:
    """The corpus bulletin as the seasonal fact (docs/06-diseno-detallado.md §9).

    The `kind` is not re-checked here: the port that answered already promised an
    `agroclimatic_bulletin`, and a second check would be the same rule in two
    places, free to disagree.
    """
    return SeasonalClimateFact(
        title=bulletin.title,
        department=bulletin.department,
        published_on=bulletin.published_on,
        enso_state=bulletin.enso_state,
        source_url=bulletin.source_url,
    )


async def _sensors(
    *,
    plot_id: UUID,
    org_id: UUID,
    now: datetime,
    window: timedelta,
    readings: ReadingRepository,
) -> tuple[SensorFact, ...]:
    """The plot's last reading per agronomic variable (docs/06 §9: "última lectura
    calibrada por variable de la parcela").

    Asked of the facade in `ASSISTANT_METRICS`' own order and kept in that order,
    so the block lists the variables the same way on every question.

    **A `None` is dropped, not zeroed.** The facade maps a metric with no valid
    reading in its window to `None`; `0.0` there would claim the soil is bone dry
    for a sensor that has simply never reported, and D-T12.1's rule — absence is
    never `0` — is the same rule in the assistant's own vocabulary.

    The calibration and the out-of-range filtering are the facade's work, and the
    calibration version docs/06 §9 also asks for is not here: see the module's
    note in the task report — the telemetry facade exposes neither it nor
    `received_at`, so a fact that claimed one would be inventing it.
    """
    by_metric = await query_latest_plot_readings(
        plot_id=plot_id, org_id=org_id, metrics=ASSISTANT_METRICS, now=now, readings=readings
    )
    return tuple(
        SensorFact(
            metric=metric,
            value=point.value,
            reading_time=point.time,
            is_stale=is_stale(point.time, now, fresh_after=window),
        )
        for metric in ASSISTANT_METRICS
        if (point := by_metric[metric]) is not None
    )


async def _irrigation(
    *,
    user_id: UUID,
    plot_id: UUID,
    now: datetime,
    plots: PlotRepository,
    recommendations: IrrigationRecommendationRepository,
    memberships: MembershipRepository,
) -> IrrigationFact | None:
    """Today's stored recommendation, or `None` when it was never calculated.

    The day arrives as `None` so the facade resolves it in America/Bogota through
    `shared.dates.local_today` — the same day the 04:30 job wrote the row, which
    is not the same day a UTC `date()` would name for five hours every evening.

    `RecommendationNotFoundError` is caught, not propagated: a plot whose daily job
    has not run is the normal state of a plot, not a failed question, and the only
    honest fact is that this family is missing (D-T12.1). An answer that said
    "regar 0 mm" would be recommending a decision no calculation made.
    """
    try:
        stored = await query_plot_recommendation(
            user_id=user_id,
            plot_id=plot_id,
            now=now,
            plots=plots,
            recommendations=recommendations,
            memberships=memberships,
        )
    except RecommendationNotFoundError:
        return None
    return IrrigationFact(
        day=stored.day,
        kind=stored.kind.value,
        rationale=sanitize_fact_values(stored.rationale, allowed_keys=IRRIGATION_RATIONALE_KEYS),
        depth_mm=stored.depth_mm,
        duration_min=stored.duration_min,
        # `RainfedAdvice` is a `StrEnum`: the fact carries the code, and the
        # Spanish sentence the answer writes is the renderer's business
        # (docs/00 vocabulary), not the fact's.
        advice=tuple(str(code) for code in stored.advice),
    )


def _missing(
    *,
    has_sensors: bool,
    has_alerts: bool,
    has_irrigation: bool,
    has_climate: bool,
    has_risk: bool,
) -> tuple[MissingFactKind, ...]:
    """The families with no evidence, in `MissingFactKind`'s own order.

    docs/06 §9's prompt rule: "Si no hay información suficiente, decirlo". The
    order is the enum's and not a set's, so the sentence the answer owes the
    producer reads the same way on every question — and the two-run equality a
    test asserts is only true because nothing here iterates a `set`.
    """
    present: dict[MissingFactKind, bool] = {
        MissingFactKind.SENSORS: has_sensors,
        MissingFactKind.ALERTS: has_alerts,
        MissingFactKind.IRRIGATION: has_irrigation,
        MissingFactKind.SEASONAL_CLIMATE: has_climate,
        MissingFactKind.RISK: has_risk,
    }
    return tuple(kind for kind, found in present.items() if not found)
