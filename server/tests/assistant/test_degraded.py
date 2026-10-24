"""The degraded answer as a pure renderer (E12 T4; docs/06-diseno-detallado.md §9 and
its prompt rules :381-387; docs/00-glosario.md; ADR-0007:26; D-T9.2, D-T12.1).

docs/06 §9 draws the degraded branch: when the day's limit or the month's budget
is exhausted, "respuesta sin LLM: datos de la parcela + documentos relevantes",
and "En la respuesta sin LLM se devuelven **estos mismos hechos** junto con los
documentos relevantes". So this answer is not an apology and not a placeholder:
it is the same facts, in plain Spanish, with the documents, written by the
application rather than by a model.

The prompt rules of docs/06 :381-387 are the spec of the text, and each is a test
here:

* plain Spanish, `docs/00-glosario.md` vocabulary;
* the source is cited;
* **when the evidence is insufficient, it says so** — and "insufficient" means a
  named missing family, not a hedge;
* **never an agrochemical dose**: it points at the ICA-registered product label and
  at the technician (docs/06 :386);
* a baseline's `low` is **never narrated as a verdict** (D-T9.2), because the
  served `climatology_month` carries `thresholds={}` and reports `low` for want of
  a calibrated operating point.

**Why this module is separate.** `render_degraded_answer` is domain; the use
case that wraps it (`answer_without_llm`) is application. They were first written
together with one test module, so the tests of the pure renderer dragged an
application import in with them and the domain slice of the work could not stand
on its own. Everything below therefore imports from
`techcamp.assistant.domain` and **nothing** from
`techcamp.assistant.application`, needs no fixture and no test double: what a
plot's facts become as text is decided by a function with no clock, no provider
and no repository, so the tests say so with plain assertions over values they
build themselves. (The session's autouse schema migration in `tests/conftest.py`
still provisions the database for the whole `tests/` tree, which is why a bare
run here needs the same `DATABASE_URL` as every other module; that is a property
of the session, not of this path.) The signature and provider-call guarantees
live with the use case, in `test_degraded_answer.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from techcamp.assistant.domain.citations import Citation, CitationKind
from techcamp.assistant.domain.degraded import render_degraded_answer
from techcamp.assistant.domain.facts import (
    AlertFact,
    IrrigationFact,
    MissingFactKind,
    PlotFacts,
    RiskFact,
    SeasonalClimateFact,
    SensorFact,
)

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
PLOT = UUID("33333333-3333-3333-3333-333333333333")

BULLETIN_CITATION = Citation(
    source="https://example.org/ban-oct-2026",
    title="Boletín Agroclimático Nacional, octubre 2026",
    kind=CitationKind.AGROCLIMATIC_BULLETIN,
    source_url="https://example.org/ban-oct-2026",
    published_on=date(2026, 10, 1),
    similarity=0.86,
)
GUIDE_CITATION = Citation(
    source="https://example.org/aji",
    title="Guía de manejo del ají",
    kind=CitationKind.GUIDE,
    source_url="https://example.org/aji",
    similarity=0.81,
)


def _facts(**overrides: object) -> PlotFacts:
    base: dict[str, object] = {
        "plot_id": PLOT,
        "plot_name": "Lote del sur",
        "sensors": (
            SensorFact(metric="soil_moisture", value=21.5, reading_time=NOW, is_stale=False),
            SensorFact(metric="air_temp", value=29.0, reading_time=NOW, is_stale=False),
        ),
        "alerts": (
            AlertFact(
                rule_code="water_stress",
                severity="critical",
                opened_at=datetime(2026, 10, 6, 2, 0, tzinfo=UTC),
                acknowledged=False,
                evidence={"day": "2026-10-05", "source": "balance"},
            ),
        ),
        "irrigation": IrrigationFact(
            day=date(2026, 10, 6),
            kind="irrigate",
            rationale={"et0_mm": 4.5, "kc": 0.9, "low_confidence": True},
            depth_mm=12.5,
            duration_min=35,
            advice=(),
        ),
        "seasonal_climate": SeasonalClimateFact(
            title="Boletín Agroclimático Nacional, octubre 2026",
            department=None,
            published_on=date(2026, 10, 1),
            enso_state="neutral",
            source_url="https://example.org/ban-oct-2026",
        ),
        "risk": (
            RiskFact(
                event_type="flood",
                horizon_start=date(2026, 10, 1),
                horizon_days=30,
                probability=0.12,
                severity="low",
                top_factors=({"feature": "month", "value": 10.0, "contribution": 0.12},),
                model_version="climatology_month 2026-10-06",
                metrics={"pr_auc": 0.44, "brier": 0.21},
                is_baseline=True,
            ),
        ),
        "missing": (),
    }
    return PlotFacts(**{**base, **overrides})  # type: ignore[arg-type]


def _text(**overrides: object) -> str:
    return render_degraded_answer(
        facts=_facts(**overrides), citations=(BULLETIN_CITATION, GUIDE_CITATION)
    ).text


# --- the whole answer -----------------------------------------------------


def test_the_answer_is_the_plot_name_the_facts_and_the_documents() -> None:
    """docs/06 §9: "estos mismos hechos junto con los documentos relevantes". Each
    of the five families appears, so the producer reads the same evidence the model
    would have been given."""
    answer = render_degraded_answer(facts=_facts(), citations=(BULLETIN_CITATION, GUIDE_CITATION))

    assert answer.plot_id == PLOT
    assert isinstance(answer.citations, tuple)
    assert [citation.title for citation in answer.citations] == [
        "Boletín Agroclimático Nacional, octubre 2026",
        "Guía de manejo del ají",
    ]
    for expected in (
        "Lote del sur",
        "21.5",
        "water_stress",
        "12.5",
        "neutral",
        "inundación",
    ):
        assert expected in answer.text, expected


def test_the_same_facts_render_the_same_text_every_time() -> None:
    """The degraded answer is the fallback, so it is the answer a producer reads
    when the provider is unreachable. Two runs over the same facts must be equal:
    an answer that changed between two identical requests would be reported as a
    bug nobody could reproduce."""
    assert _text() == _text()


# --- sensors --------------------------------------------------------------


def test_a_sensor_reading_is_named_with_its_time_in_the_producers_own_day() -> None:
    """docs/06 §9: "última lectura calibrada por variable". The time matters: a
    humidity reading is a statement about a moment, and a producer comparing it
    with yesterday's needs to know which moment. Rendered in **America/Bogota**
    (docs/06 §5, the product's one timezone), which is why the section says so."""
    text = _text()

    assert "Humedad del suelo: 21.5 %" in text
    assert "Temperatura del aire: 29.0 °C" in text
    assert "06/10/2026 09:00" in text  # 14:00 UTC is 09:00 in Colombia
    assert "09:00" in text and "14:00" not in text


def test_a_stale_reading_is_marked_and_a_fresh_one_is_not() -> None:
    """docs/06 §9: "se marcan … si el dato no está fresco". The mark is in words,
    because a producer reading this has no way to interpret a boolean."""
    facts = _facts(
        sensors=(
            SensorFact(metric="soil_moisture", value=21.5, reading_time=NOW, is_stale=False),
            SensorFact(
                metric="soil_temp",
                value=28.4,
                reading_time=datetime(2026, 10, 6, 6, 0, tzinfo=UTC),
                is_stale=True,
            ),
        )
    )

    text = render_degraded_answer(facts=facts, citations=()).text

    stale_line = next(line for line in text.splitlines() if "Temperatura del suelo" in line)
    fresh_line = next(line for line in text.splitlines() if "Humedad del suelo" in line)
    assert "dato antiguo" in stale_line
    assert "dato antiguo" not in fresh_line


def test_a_metric_with_no_reading_is_absent_from_the_list_not_zero() -> None:
    """D-T12.1: absence is never `0`. Only the variables that have a reading are
    written, so the list cannot be read as a table of six zeroes."""
    text = _text(sensors=())

    assert "Humedad del suelo" not in text
    assert "Sensores: no hay lecturas" in text


# --- alerts ---------------------------------------------------------------


def test_an_open_alert_is_written_with_its_severity_and_its_time() -> None:
    """docs/06 §9: "alertas abiertas o reconocidas … con su `severity`,
    `opened_at`". The severity is the design system's own Spanish label (docs/07's
    `severity`: `Información`, `Advertencia`, `Crítico`) so the tray and the answer
    use one word for the same thing."""
    text = _text()

    assert "water_stress" in text
    assert "Crítico" in text
    assert "06/10/2026" in text


def test_an_acknowledged_alert_says_it_was_acknowledged_and_is_still_open() -> None:
    """docs/00's glossary: "Alerta abierta | `state <> 'resolved'` … Reconocerla no
    la cierra". An acknowledged alert is a fact, and saying so is what tells the
    producer this one is still asking even though somebody already saw it."""
    facts = _facts(
        alerts=(
            AlertFact(
                rule_code="water_stress",
                severity="critical",
                opened_at=datetime(2026, 10, 6, 2, 0, tzinfo=UTC),
                acknowledged=True,
                evidence={},
            ),
        )
    )

    text = render_degraded_answer(facts=facts, citations=()).text

    assert "reconocida" in text
    assert "Crítico" in text


# --- irrigation -----------------------------------------------------------


def test_the_recommendation_is_written_with_its_depth_and_its_duration() -> None:
    """docs/06 §9: "según el caso, `depth_mm` y `duration_min` … o los consejos de
    secano". In the glossary's vocabulary: the water to apply is a **lámina de
    riego**, in mm."""
    text = _text()

    assert "12.5 mm" in text
    assert "35 minutos" in text


def test_a_rainfed_recommendation_is_written_as_advice_and_no_depth() -> None:
    """ADR-0023: a parcel of secano has no depth and no duration. Writing "0 mm"
    there would recommend irrigating nothing, which is a decision the calculation
    never made."""
    facts = _facts(
        irrigation=IrrigationFact(
            day=date(2026, 10, 6),
            kind="rainfed",
            rationale={"depletion_mm": 41.0},
            depth_mm=None,
            duration_min=None,
            advice=("delay_sowing", "conserve_moisture"),
        )
    )

    text = render_degraded_answer(facts=facts, citations=()).text

    assert "0 mm" not in text
    assert "0.0 mm" not in text
    assert "retrasar la siembra" in text
    assert "conservar la humedad del suelo" in text
    assert "secano" in text


# --- seasonal climate -----------------------------------------------------


def test_the_bulletin_is_written_with_its_publication_date_and_its_enso_state() -> None:
    """docs/06 §9: "el estado ENSO … con su fecha de publicación"."""
    text = _text()

    assert "Boletín Agroclimático Nacional, octubre 2026" in text
    assert "01/10/2026" in text
    assert "neutral" in text


# --- risk, the two ways it can go wrong -----------------------------------


def test_a_baseline_prediction_marks_the_line_base_and_names_no_severity() -> None:
    """D-T9.2, and the acceptance criterion: the served version is
    `climatology_month` with `thresholds={}`, so **every** prediction it serves is
    `low`. That `low` is a missing operating point, not a finding — so the answer
    reports the version, marks it as a línea base, and does **not** write the
    severity the system never decided. The glossary's word for it is "línea base"
    and it is the same word docs/06 §9 uses ("Si la predicción viene de la
    heurística de línea base, se indica")."""
    text = _text()

    assert "línea base" in text
    assert "climatology_month 2026-10-06" in text
    assert "0.12" in text
    # The negative assertion: no severity verdict for an uncalibrated version.
    assert "severidad baja" not in text
    assert "riesgo bajo" not in text
    assert "riesgo es bajo" not in text


def test_a_calibrated_version_reports_its_own_severity() -> None:
    """The other side of the same rule: a version **with** thresholds decided a
    severity, so the answer reports it in the interface's own words (docs/00:
    "Severidad del riesgo … en la interfaz, bajo, alto y crítico"). Suppressing it
    everywhere would make the risk unreadable for the day a model is promoted."""
    facts = _facts(
        risk=(
            RiskFact(
                event_type="flood",
                horizon_start=date(2026, 10, 1),
                horizon_days=30,
                probability=0.71,
                severity="high",
                top_factors=(),
                model_version="risk_flood 2026-10-06",
                metrics={"pr_auc": 0.71},
                is_baseline=False,
            ),
        )
    )

    text = render_degraded_answer(facts=facts, citations=()).text

    assert "alto" in text
    assert "línea base" not in text
    assert "risk_flood 2026-10-06" in text


def test_a_missing_risk_is_named_and_no_severity_is_invented() -> None:
    """D-T9.3: `drought` has no registered predictor, and a plot without a climate
    cell has no predictions at all. `query_plot_risk` answers `[]` for both, and the
    answer must say the risk is unavailable — never name a severity nobody decided."""
    text = _text(risk=(), missing=(MissingFactKind.RISK,))

    assert "no hay información disponible" in text
    assert "inundación" not in text
    assert "alto" not in text
    assert "línea base" not in text


# --- what is missing ------------------------------------------------------


def test_a_plot_with_nothing_at_all_says_the_evidence_is_insufficient() -> None:
    """docs/06 §9's prompt rule, "Si no hay información suficiente, decirlo",
    turned into a sentence. Every missing family is named, so the producer knows
    what did not arrive instead of reading five empty sections."""
    facts = PlotFacts(
        plot_id=PLOT,
        plot_name="Lote del sur",
        sensors=(),
        alerts=(),
        irrigation=None,
        seasonal_climate=None,
        risk=(),
        missing=(
            MissingFactKind.SENSORS,
            MissingFactKind.ALERTS,
            MissingFactKind.IRRIGATION,
            MissingFactKind.SEASONAL_CLIMATE,
            MissingFactKind.RISK,
        ),
    )

    text = render_degraded_answer(facts=facts, citations=()).text

    assert "No hay información suficiente" in text
    for heading in (
        "Sensores: no hay lecturas",
        "Alertas: no hay alertas abiertas",
        "Riego: no hay información disponible",
        "Clima estacional: no hay información disponible",
        "Riesgo climático: no hay información disponible",
    ):
        assert heading in text, heading


def test_the_alerts_family_says_there_are_none_rather_than_that_data_is_missing() -> None:
    """The one family whose absence is **not** missing data:
    `list_open_alerts_for_plots` returns the same empty list for "nothing is open"
    and "the evaluator found nothing", and "no hay información disponible de
    alertas" would tell a producer his alerts are missing when they are quiet. The
    honest sentence is the one the system can actually back."""
    facts = _facts(alerts=(), missing=(MissingFactKind.ALERTS,))

    text = render_degraded_answer(facts=facts, citations=()).text

    assert "no hay alertas abiertas" in text
    assert "no hay información disponible de alertas" not in text


def test_one_missing_family_does_not_hide_the_families_that_are_present() -> None:
    """Missing evidence is reported, not used as a reason to withhold everything:
    the producer still gets the sensors they do have."""
    text = _text(missing=(MissingFactKind.RISK,))

    assert "Humedad del suelo: 21.5 %" in text
    assert "12.5 mm" in text
    assert "No hay información suficiente" in text
    assert "no hay información disponible" in text


# --- the documents --------------------------------------------------------


def test_every_document_is_cited_with_its_title_and_its_link() -> None:
    """docs/06 §9: the degraded answer returns "los documentos relevantes" too, and
    docs/04 §Riesgo: an answer cites the source. A source a producer cannot open is
    not cited."""
    text = _text()

    assert "Fuentes" in text
    assert "Boletín Agroclimático Nacional, octubre 2026" in text
    assert "https://example.org/ban-oct-2026" in text
    assert "Guía de manejo del ají" in text
    assert "https://example.org/aji" in text


def test_an_answer_with_no_documents_still_gives_the_facts() -> None:
    """An empty corpus is a legitimate retrieval (nothing close enough), and the
    facts do not depend on it. The answer says so rather than showing an empty
    "Fuentes" section."""
    text = _text_with_no_citations()

    assert "Humedad del suelo: 21.5 %" in text
    assert "no se encontró ningún documento" in text


def _text_with_no_citations() -> str:
    return render_degraded_answer(facts=_facts(), citations=()).text


# --- the rule that is never broken ----------------------------------------


def test_the_answer_never_states_an_agrochemical_dose() -> None:
    """docs/06-diseno-detallado.md :386: "**Nunca inventar dosis de
    agroquímicos:** remitir a la etiqueta del producto registrado ante el ICA y al
    técnico." The negative assertion is the point: none of the units a dose is
    written in appears anywhere in the text — the five facts this answer renders
    carry millimetres of **water**, degrees, percentages and litres, and a reader
    must not be able to mistake one for a dose of a product."""
    text = _text()

    for dose_unit in ("L/ha", "cm³", "cm3", "g/L", "kg/ha", "ml/L", "L por litro"):
        assert dose_unit not in text, dose_unit
    assert "ICA" in text
    assert "etiqueta" in text
    assert "técnico" in text


def test_the_answer_points_at_the_ica_label_and_the_technician() -> None:
    """The positive half of docs/06 :386: the answer names where the label is and
    who to ask, so a producer reading "¿qué agroquímico aplico?" is told how to
    find out instead of being given a number."""
    text = _text()

    assert "etiqueta del producto registrado ante el ICA" in text
    assert "técnico" in text
