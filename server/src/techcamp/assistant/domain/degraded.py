"""The answer without a language model, as plain Spanish text (E12 T4;
docs/06-diseno-detallado.md §9 and its prompt rules :381-387;
docs/00-glosario.md; docs/04-api.md §Riesgo, métricas y asistente;
ADR-0007:26; D-T9.2, D-T12.1).

docs/06 §9 draws the degraded branch: when the day's limit or the month's budget
is exhausted, the API answers "datos de la parcela + documentos relevantes", and
"En la respuesta sin LLM se devuelven **estos mismos hechos** junto con los
documentos relevantes". So this is not an apology and not a placeholder: it is the
same facts the model would have been given, written by the application.

Pure and deterministic — no clock, no provider, no repository. That is what makes
it the fallback that is always available: T6's budget branch and T5's `429` both
land here, and neither has to reach anything to get an answer.

The spec of the text is docs/06 §9's prompt rules (:381-387), and this module
holds to all five:

* **plain Spanish**, in `docs/00-glosario.md`'s vocabulary (lámina de riego, línea
  base, recomendación de secano, boletín agroclimático);
* **it cites the source** — the documents arrive as `Citation`s and are written out
  with their title and their link (ADR-0008:12);
* **it says when the evidence is insufficient**, by naming the missing families
  rather than hedging;
* **it never states an agrochemical dose**: it points at the label of the product
  registered with the **ICA** and at the technician (:386), which is the only
  sentence here with no number in it by design;
* **it explains; it never computes** (ADR-0007:16). Every number in the text came
  out of a fact that a facade produced.

And the one that is easy to get wrong: **a baseline's `low` is never narrated as a
verdict** (D-T9.2). The served version is `climatology_month` carrying
`thresholds={}`, so it reports `low` for every event for want of a calibrated
operating point. This renderer reports the probability, names the version and marks
it "línea base", and writes the severity **only** for a version that had thresholds
to decide it with.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from techcamp.assistant.domain.citations import Citation
from techcamp.assistant.domain.facts import (
    AlertFact,
    IrrigationFact,
    MissingFactKind,
    PlotFacts,
    RiskFact,
    SeasonalClimateFact,
    SensorFact,
)
from techcamp.shared.dates import BOGOTA_TZ

METRIC_LABELS: dict[str, tuple[str, str]] = {
    "soil_moisture": ("Humedad del suelo", "%"),
    "soil_temp": ("Temperatura del suelo", "°C"),
    "air_temp": ("Temperatura del aire", "°C"),
    "air_rh": ("Humedad relativa del aire", "%"),
    "rain": ("Lluvia", "mm"),
    "water_flow": ("Caudal de agua", "litros"),
}
"""`ASSISTANT_METRICS` as `(the words a producer uses, the unit the repository
already documents)`, kept as one entry so a variable can never gain a label without
its unit or the other way round.

The units are not invented here: a percentage for the two humidities
(docs/04-api.md §Estado de la parcela serves `soil_moisture_pct` and `air_rh_pct`,
and `classify_reading_range` enforces `[0, 100]` on the `%` unit), degrees Celsius
for the two temperatures (the same table's `air_temp_c`), millimetres for rain (the
same table's `rain_mm`) and **litres** for `water_flow`, which docs/00-glosario.md's
`Variable` row spells out ("caudalímetro, en litros").

A metric outside this table is written with its own code and **no unit**, because
`reading.metric` is a text column and an unknown variable with an invented unit is
worse than one that says what it does not know.
"""

_ALERT_SEVERITIES: dict[str, str] = {
    "info": "Información",
    "warning": "Advertencia",
    "critical": "Crítico",
}
"""Alert severity in the design system's own words (docs/07-frontend-design-system.md
§Componentes, `web/src/design-system/components/severity.ts`).

Reused rather than reinvented so the answer and the alert tray call the same thing
the same word. An org's own rule cannot invent a severity — the column is a check —
so an unknown code falls back to the code itself.
"""

_RISK_SEVERITIES: dict[str, str] = {"low": "bajo", "high": "alto", "critical": "crítico"}
"""Risk severity in the interface's own words (docs/00-glosario.md, `Severidad del
riesgo`: "`low`, `high` o `critical` … en la interfaz, bajo, alto y crítico")."""

_ADVICE: dict[str, str] = {
    "delay_sowing": "retrasar la siembra",
    "rain_expected": "se espera lluvia",
    "conserve_moisture": "conservar la humedad del suelo",
    "prioritize_harvest": "priorizar la cosecha",
    "no_action": "no hace falta ninguna acción",
}
"""`RainfedAdvice` in plain Spanish (docs/06 §5's rainfed table).

An advice code outside this table is written as its own code, for the same reason a
metric outside `METRIC_LABELS` is: the column is a text column and a sentence in
the wrong words is worse than the code.
"""

_KINDS: dict[str, str] = {
    "irrigate": "regar",
    "postpone": "esperar un día más",
    "not_needed": "no hace falta regar",
    "no_kc": "no hay Kc validado para el cultivo, así que no se propone una lámina",
    "rainfed": "parcela de secano",
}
"""`RecommendationKind` in plain Spanish. `no_kc` is the glossary's `kc_source =
none` case: the crop has no validated Kc, so no depth can be proposed — which is a
reason, not a zero."""

_ENSO: dict[str, str] = {
    "neutral": "neutral",
    "el_nino": "El Niño",
    "la_nina": "La Niña",
}
"""`kb_document.enso_state` (`neutral|el_nino|la_nina`, docs/03 §`kb_document`) in
the spelling the bulletins use."""

_EVENTS: dict[str, str] = {"flood": "inundación", "drought": "sequía"}
"""`risk_prediction.event_type` (docs/03 §`risk_prediction`) in Spanish."""

_FAMILY_LABELS: dict[MissingFactKind, str] = {
    MissingFactKind.SENSORS: "sensores",
    MissingFactKind.ALERTS: "alertas",
    MissingFactKind.IRRIGATION: "riego",
    MissingFactKind.SEASONAL_CLIMATE: "clima estacional",
    MissingFactKind.RISK: "riesgo climático",
}
"""How each missing family is named in the sentence that says the evidence is
insufficient (docs/06 §9: "Si no hay información suficiente, decirlo")."""

_DOSE_NOTICE = (
    "Sobre los agroquímicos: este resumen no dice ninguna dosis. "
    "Consulte la etiqueta del producto registrado ante el ICA y hable con su técnico."
)
"""docs/06-diseno-detallado.md :386, verbatim in substance and unconditionally
present.

**Never a dose**, ever, in any branch of this renderer — the sentence is not
conditional on a family being present, because a producer who asks "¿qué
plaguicida le pongo?" has to be answered the same way whether or not the plot has
an open alert. The two destinations are the only ones the docs allow: the label of
the product registered with the ICA, and the technician."""


@dataclass(frozen=True, slots=True)
class DegradedAnswer:
    """What the endpoint serves when the provider is not reached (docs/06 §9).

    `text` is plain Spanish with the plot's facts and its documents; `citations`
    travel beside it so the endpoint writes the cited sources into the response and
    the producer can open them (docs/04-api.md §Riesgo, métricas y asistente).

    Both are decided by the facts and the documents, so the same plot on the same
    day produces the same answer — which is what makes it usable as a fallback and
    what lets a test assert on it.
    """

    text: str
    citations: tuple[Citation, ...]
    plot_id: UUID
    """The plot the answer is about, so the endpoint does not have to thread it
    separately and a caller cannot pair one plot's text with another's sources."""


def render_degraded_answer(*, facts: PlotFacts, citations: Sequence[Citation]) -> DegradedAnswer:
    """The degraded answer for one plot (docs/06-diseno-detallado.md §9).

    Pure, deterministic, and in the order docs/06 §9 lists the families: sensores,
    alertas, riego, clima estacional, riesgo — so a producer who reads two answers
    reads them the same way.

    Every absent family produces a **sentence saying so**, never an empty section
    and never a zero (D-T12.1), and every present family produces the numbers its
    facade produced. Nothing is computed here and nothing is invented (ADR-0007:16):
    the renderer's only judgement is whether a risk prediction's severity may be
    written, which is decided by `is_baseline` and by nothing else.
    """
    lines: list[str] = [f'Datos de la parcela "{facts.plot_name}".', ""]

    lines += _sensors(facts.sensors)
    lines += _alerts(facts.alerts, missing=facts.missing)
    lines += _irrigation(facts.irrigation)
    lines += _seasonal_climate(facts.seasonal_climate)
    lines += _risk(facts.risk)
    lines += _insufficient(facts.missing)
    lines += _sources(citations)
    lines += ["", _DOSE_NOTICE]

    return DegradedAnswer(
        text="\n".join(line for line in lines if line is not None),
        citations=tuple(citations),
        plot_id=facts.plot_id,
    )


def _sensors(sensors: Sequence[SensorFact]) -> list[str]:
    """The last reading per variable, with its time and the stale mark (docs/06 §9).

    The times are rendered in **America/Bogota** (docs/06 §5, the product's single
    timezone) and the section says so, because a bare clock time next to a
    producer's own day is ambiguous for the five hours it differs from UTC.

    The stale mark is in words: a producer reading this has no `is_stale` boolean in
    front of them, and "dato antiguo" is the only version of it they can act on.
    """
    if not sensors:
        return ["Sensores: no hay lecturas de esta parcela."]
    lines = ["Sensores (última lectura, hora de Colombia):"]
    for fact in sensors:
        labelled = METRIC_LABELS.get(fact.metric)
        reading = (
            f"{labelled[0]}: {fact.value} {labelled[1]}"
            if labelled is not None
            else f"{fact.metric}: {fact.value}"
        )
        stale = ", dato antiguo, puede estar desactualizado" if fact.is_stale else ""
        lines.append(f"- {reading} ({_fecha_hora(fact.reading_time)}{stale}).")
    lines.append("")
    return lines


def _alerts(alerts: Sequence[AlertFact], *, missing: Sequence[MissingFactKind]) -> list[str]:
    """The open and acknowledged alerts (docs/06 §9).

    **The one family whose absence is not missing data.**
    `list_open_alerts_for_plots` answers the same empty list for "nothing is open"
    and "the evaluator found nothing", so "no hay información disponible de
    alertas" would tell a producer his alerts are unavailable when they are quiet —
    a false alarm about the one thing a producer cannot afford to doubt. The
    sentence written is the one the system can actually back.
    """
    if not alerts:
        quiet = (
            ["Alertas: no hay alertas abiertas ni reconocidas."]
            if MissingFactKind.ALERTS in missing
            else ["Alertas: ninguna."]
        )
        return [*quiet, ""]
    lines = ["Alertas abiertas:"]
    for alert in alerts:
        lines.append(f"- {_alert_line(alert)}")
    lines.append("")
    return lines


def _alert_line(alert: AlertFact) -> str:
    severity = _ALERT_SEVERITIES.get(alert.severity, alert.severity)
    state = ", reconocida pero todavía abierta" if alert.acknowledged else ""
    return f"{alert.rule_code} ({severity}), abierta el {_fecha_hora(alert.opened_at)}{state}."


def _irrigation(irrigation: IrrigationFact | None) -> list[str]:
    """Today's recommendation, or the sentence that says there is none (docs/06 §9).

    The depth is a **lámina de riego** in millimetres (docs/00-glosario.md) and it is
    written **only when the calculation proposed one**. A rainfed recommendation and
    a `no_kc` one have no depth, and a "0 mm" in either place would recommend
    irrigating nothing — a decision no calculation made.
    """
    if irrigation is None:
        return ["Riego: no hay información disponible de la recomendación de hoy.", ""]
    kind = _KINDS.get(irrigation.kind, irrigation.kind)
    parts = [f"Riego de hoy ({_fecha(irrigation.day)}): {kind}"]
    if irrigation.depth_mm is not None:
        parts.append(f"lámina de {irrigation.depth_mm} mm")
        if irrigation.duration_min is not None:
            parts.append(f"durante {irrigation.duration_min} minutos")
    parts.append(".")
    lines = [" ".join(parts)]
    if irrigation.advice:
        lines.append(f"- Consejos de secano: {_advice_phrase(irrigation.advice)}.")
    if irrigation.rationale.get("low_confidence") is True:
        lines.append("- El cálculo marca baja confianza en estos números.")
    lines.append("")
    return lines


def _advice_phrase(advice: Sequence[str]) -> str:
    """The rainfed advice codes as one sentence (ADR-0023, docs/06 §5's table)."""
    return ", ".join(_ADVICE.get(code, code) for code in advice)


def _seasonal_climate(bulletin: SeasonalClimateFact | None) -> list[str]:
    """The latest bulletin, with its publication date and its ENSO state
    (docs/06 §9)."""
    if bulletin is None:
        return ["Clima estacional: no hay información disponible de un boletín.", ""]
    scope = "el nacional" if bulletin.department is None else f"de {bulletin.department}"
    published = (
        f", publicado el {_fecha(bulletin.published_on)}"
        if bulletin.published_on is not None
        else ""
    )
    lines = [f"Clima estacional (boletín {scope}): {bulletin.title}{published}."]
    if bulletin.enso_state is not None:
        state = _ENSO.get(bulletin.enso_state, bulletin.enso_state)
        lines.append(f"- Estado ENSO: {state}.")
    lines.append("")
    return lines


def _risk(predictions: Sequence[RiskFact]) -> list[str]:
    """The served predictions, each with the version that produced it (docs/06 §9).

    **The severity is written only for a calibrated version.** The served baseline
    is `climatology_month` with `thresholds={}`, so every prediction it serves is
    `low` (D-T9.2) — a missing operating point. For that case the line says so in
    words instead of writing the severity, which is exactly what docs/06 §9 asks for
    ("Si la predicción viene de la heurística de línea base, se indica") and exactly
    what the acceptance criterion forbids: an unmarked `low` presented as a verdict
    the system never made.
    """
    if not predictions:
        return ["Riesgo climático: no hay información disponible.", ""]
    lines = ["Riesgo climático:"]
    for prediction in predictions:
        lines.append(f"- {_risk_line(prediction)}")
    lines.append("")
    return lines


def _risk_line(prediction: RiskFact) -> str:
    event = _EVENTS.get(prediction.event_type, prediction.event_type)
    horizon = f"desde el {_fecha(prediction.horizon_start)} por {prediction.horizon_days} días"
    version = f" Versión del modelo: {prediction.model_version}"
    if prediction.is_baseline:
        version += " (línea base, sin umbrales calibrados: por eso no se declara una severidad)"
    severity = (
        ""
        if prediction.is_baseline
        else f", severidad {_RISK_SEVERITIES.get(prediction.severity, prediction.severity)}"
    )
    return f"{event}, probabilidad {prediction.probability} {horizon}{severity}.{version}"


def _insufficient(missing: Sequence[MissingFactKind]) -> list[str]:
    """The sentence docs/06 §9's prompt rule requires: "Si no hay información
    suficiente, decirlo".

    Only the families whose absence really is missing evidence are listed — the
    alerts line has already said, in its own section, that there are none, and
    repeating it here as missing information would contradict itself.
    """
    families = [
        label
        for kind, label in _FAMILY_LABELS.items()
        if kind in missing and kind is not MissingFactKind.ALERTS
    ]
    if not families:
        return []
    names = ", ".join(families)
    return [
        f"No hay información suficiente hoy: no hay información disponible de {names}.",
        "",
    ]


def _sources(citations: Sequence[Citation]) -> list[str]:
    """The relevant documents (docs/06 §9: "junto con los documentos relevantes").

    A citation's `source` is its link when the corpus recorded one and its title
    otherwise, so this writes `title — source` and a producer who has a phone can
    open the link or read the title on a printed page.
    """
    if not citations:
        return ["Fuentes: no se encontró ningún documento pertinente en la biblioteca."]
    lines = ["Fuentes:"]
    for citation in citations:
        link = f" — {citation.source}" if citation.source != citation.title else ""
        lines.append(f"- {citation.title}{link}")
    return lines


def _fecha(day: date) -> str:
    """A date as `dd/mm/aaaa`, the `es-CO` shape the web renders
    (`Intl.DateTimeFormat('es-CO')` in `web/src/features/metrics`).

    Formatted here rather than passed through as ISO because this text is read by a
    producer, not parsed by a client — and `es-CO` puts the day first, which is the
    only ordering unambiguous to the audience. The API payloads keep their ISO
    8601; this string never leaves the degraded answer.
    """
    return f"{day.day:02d}/{day.month:02d}/{day.year}"


def _fecha_hora(instant: datetime) -> str:
    """An instant as `dd/mm/aaaa hh:mm` in **America/Bogota** (docs/06 §5).

    A naive instant is read as UTC, the convention of every read in this
    application (`shared.dates.local_today`, `domain.limits`).
    """
    local = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant
    bogota = local.astimezone(BOGOTA_TZ)
    return f"{_fecha(bogota.date())} {bogota:%H:%M}"
