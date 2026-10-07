"""The plot facts the LLM is given, as pure values (E12 T4;
docs/06-diseno-detallado.md §9 "Hechos que recibe el LLM"; docs/00-glosario.md;
ADR-0007:16; D-T12.1, D-T12.6).

docs/06 §9 enumerates five families — sensores, alertas, riego, clima estacional,
riesgo climático — and says the assistant assembles them **deterministically from
the facades, before the single call to the LLM**. This module is those five
families as frozen values and nothing else: no I/O, no clock read (the instant
arrives as an argument, so the facts under test are decided by the values under
test), no provider.

Three rules the whole file exists to hold:

* **Absence is never `0`, `0.0`, `[]` or `False` pretending to be a finding.**
  D-T12.1 states it for the stored evidence and it is the same rule here: a
  metric with no reading is a *missing family*, not a `0.0`, and a plot with no
  predictions has no risk rather than a `low`. `drought` has no registered
  predictor (D-T9.3), so an absent risk fact is the normal case, not an edge one.
* **The freshness mark is not an agronomic threshold.** `SENSOR_FRESH_WINDOW` is
  the very window `query_latest_plot_readings` itself reads (D-T0.4), so "stale"
  means "at the edge of the window the facade read" and the number was not
  invented by the assistant. Every rule that needs it takes a `fresh_after`
  override.
* **Nothing here is a location or a person** (D-T12.6): no boundary, no
  location, no `municipality_code`, no phone, no e-mail. The guarantee is the
  shape, and `test_facts` asserts it on `__slots__`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from techcamp.assistant.domain.fact_safety import FactValue
from techcamp.assistant.domain.limits import _in_bogota

ASSISTANT_METRICS: tuple[str, ...] = (
    "soil_moisture",
    "soil_temp",
    "air_temp",
    "air_rh",
    "rain",
    "water_flow",
)
"""The agronomic variables of `docs/00-glosario.md`, `Variable` → `metric`.

The glossary lists eight: those six plus `battery_v` and `rssi`. **The two node
health variables are deliberately left out.** They are not agronomy — they say
whether a battery is low and how good the radio link is — and an answer to "¿por
qué no regué ayer?" is decided by soil, weather and water, never by a voltage.
D-T12.6 is a *minimum data* rule (ADR-0007:39: "se envía lo mínimo necesario, sin
datos personales"), so a metric the agronomic answer cannot use is data that
leaves the server for nothing. The node's own health is what `alerts`' node-health
rules are for, and those arrive as an alert fact.

Ordered, so a fact block built from it lists the variables in the same order on
every question: the answer a producer reads twice for two plots reads the same
way both times.
"""

SENSOR_FRESH_WINDOW = timedelta(hours=24)
"""The window a sensor fact is fresh within — **the very window
`query_latest_plot_readings` itself reads** (D-T0.4: the newest valid reading per
metric within `[now - 24h, now]`).

So this number is not an agronomic threshold and nothing here decides that 24
hours is how long soil moisture is meaningful: that is a question no document
answers, and this module does not answer it by accident. The facade already
returns nothing older than its window, which makes every fact it returns fresh;
the mark exists for the value that is about to *leave* the window, and it means
exactly that — "this is at the edge of what the facade reads" — which is why the
rule takes a `fresh_after` override rather than hard-coding an agronomy nobody
wrote down.
"""


def is_stale(
    reading_time: datetime, now: datetime, *, fresh_after: timedelta = SENSOR_FRESH_WINDOW
) -> bool:
    """Whether a reading is older than the window it may be read in.

    Strictly older (`>`): a reading exactly at the edge of the window is still
    inside the window the facade read, and calling it stale would report a value
    the system is still serving as missing. A reading in the future (a node whose
    clock ran ahead) is not stale.

    Both instants go through `_in_bogota`, the helper `domain.limits` already owns,
    so a naive value is read as UTC — the convention of every read in this
    application (`shared.dates.local_today`) — and this module does not become a
    second place that decides what a naive instant means. The helper lives in
    `limits.py`, which is outside this task's edit surface, so it is imported
    rather than duplicated; a rule that re-implemented the convention here would
    be the second owner the repo forbids.
    """
    return _in_bogota(now) - _in_bogota(reading_time) > fresh_after


class MissingFactKind(StrEnum):
    """One family of facts with **no evidence**, named so the answer can say so
    (docs/06-diseno-detallado.md §9, regla del prompt: "Si no hay información
    suficiente, decirlo").

    The five names are docs/06 §9's own five families. A member is not a warning
    and not an error: it is the sentence the answer owes the producer instead of
    the number it does not have. D-T12.1's rule — absence is `null`, never `0` —
    applies here in its own vocabulary: `MissingFactKind.IRRIGATION` and not
    `depth_mm=0.0`, and `MissingFactKind.RISK` and not a `low` severity.
    """

    SENSORS = "sensors"
    ALERTS = "alerts"
    IRRIGATION = "irrigation"
    SEASONAL_CLIMATE = "seasonal_climate"
    RISK = "risk"


@dataclass(frozen=True, slots=True)
class SensorFact:
    """One variable's last reading, with the time it was taken and its freshness.

    docs/06 §9: "última lectura calibrada por variable de la parcela". Calibrated
    and out-of-range filtering are the facade's work — `query_latest_plot_readings`
    already excludes quality bit 2 and uncalibrated values — so this value carries
    what survived it, plus `is_stale` because "si el dato no está fresco" is
    docs/06 §9's own requirement and the facade does not report it.

    `is_stale` is a computed fact of the reading, decided by
    `is_stale(reading_time, now)` at assembly time: it is stored rather than
    derived so that the prompt builder and the degraded renderer cannot disagree
    about the same reading.
    """

    metric: str
    """One of `ASSISTANT_METRICS`; the code, never a label. The Spanish name is
    the answer's business (`docs/00-glosario.md` vocabulary), not the fact's."""
    value: float
    reading_time: datetime
    is_stale: bool


@dataclass(frozen=True, slots=True)
class AlertFact:
    """One open or acknowledged alert of the plot (docs/06-diseno-detallado.md §9:
    "alertas abiertas o reconocidas de la parcela con su `severity`, `opened_at`
    y `evidence`").

    "Abierta" in docs/00's glossary means `state <> 'resolved'`, so an
    acknowledged alert is in this fact too: reconocerla no la cierra.
    `acknowledged` carries which of the two it is, because the answer says
    "reconocida" and a producer who acknowledged an alert yesterday needs to
    know it is still asking.

    `evidence` is already sanitized (`domain.fact_safety`): allowlisted against
    the four evaluators that write it, with the denied keys and the contact and
    geometry patterns dropped. Nothing may sanitize it later, at render time,
    because the fact block is what leaves the server.

    `frozen=True` stops a field being rebound, but a `dict` it points at stays
    writable by whoever still holds it — and a fact that a caller can append a
    phone number to, after the sanitization that removed it, is a fact whose
    sanitization was only ever a moment (R3-MUTABLE-FACT-MAPS). `__post_init__`
    therefore copies every mapping field into a read-only view, so the guarantee
    belongs to the fact's type instead of to the caller's good behaviour.
    """

    rule_code: str
    """The rule that fired (docs/03-modelo-datos.md §`alert`), as its code. An
    org's own rules carry codes this module has never seen, which is why the code
    is data and not an enum."""
    severity: str
    """`info`, `warning` or `critical` as the stored code. The Spanish label
    belongs to the design system (docs/07's `severity`), not to the fact."""
    opened_at: datetime
    acknowledged: bool
    evidence: Mapping[str, FactValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", _read_only(self.evidence))


@dataclass(frozen=True, slots=True)
class IrrigationFact:
    """Today's stored recommendation for the plot (docs/06-diseno-detallado.md
    §9: "la `irrigation_recommendation` del día con su `kind`, su `rationale` y,
    según el caso, `depth_mm` y `duration_min` o los consejos de secano").

    `depth_mm` and `duration_min` are optional because the rainfed case has
    neither (ADR-0023: a parcel of secano gets advice codes, not a depth), and
    both absent is a **rainfed recommendation**, not a missing one. The family is
    missing only when no recommendation row exists at all, and then the fact is
    absent rather than a zero depth.

    `advice` holds the `RainfedAdvice` codes as strings, in the stored order
    (docs/06 §5 gives the table's order).
    """

    day: date
    kind: str
    rationale: Mapping[str, FactValue]
    """The stored calculation's own evidence, sanitized against
    `IRRIGATION_RATIONALE_KEYS` — including `without_sensor` and
    `low_confidence`, the two flags that say the number is weaker than it looks."""
    depth_mm: float | None
    duration_min: int | None
    advice: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rationale", _read_only(self.rationale))


@dataclass(frozen=True, slots=True)
class SeasonalClimateFact:
    """The latest agroclimatic bulletin (docs/06-diseno-detallado.md §9: "el
    estado ENSO y la perspectiva de lluvia del último boletín agroclimático del
    departamento de la finca, o del nacional si no hay uno regional, con su fecha
    de publicación").

    `department = None` is the **national** bulletin, which is not a missing
    department: `kb_document.department` is `"null = nacional"`
    (docs/03-modelo-datos.md §`kb_document`) and docs/06 §9 asks for it by
    name, so it is a fact with its own field and not a fallback hidden in the
    title.

    `enso_state` comes from `kb_document.enso_state`
    (`neutral|el_nino|la_nina; solo boletines`, docs/03) and is read only from a
    bulletin. Both it and `source_url` are optional because the corpus has no
    column that guarantees either (ADR-0008:32 makes licence review a gate before
    a document is migrated, and a document with no URL is a real one).
    """

    title: str
    department: str | None
    published_on: date | None
    enso_state: str | None
    source_url: str | None


@dataclass(frozen=True, slots=True)
class RiskFact:
    """The cell's current prediction for one event, with the version that
    produced it (docs/06-diseno-detallado.md §9: "la `risk_prediction` vigente de
    la celda de la parcela con `probability`, `severity` y `top_factors`, más el
    modelo que la produjo: `model_version.version` y su métrica de validación").

    `model_version` is the flattened label `name version`, because
    `query_plot_risk` already flattens the nested `model_version` object into
    `version_name` / `version_version` and a second nesting here would be a type
    the adapter does not need.

    **`is_baseline` is the field the answer cannot do without.** The served
    version is the `climatology_month` baseline carrying `thresholds={}`, so every
    prediction it serves is `low` (D-T9.2) — which is a missing operating point,
    not a finding. A fact that carried `severity` without this flag would let an
    unmarked `low` be narrated as a verdict the system never made.

    `metrics` is `None` when the version's row has neither `metrics` nor
    `baseline_metrics` (docs/04 §Riesgo allows exactly that `null`), and never
    `{}`: an empty object would read as "measured and found nothing".
    """

    event_type: str
    horizon_start: date
    horizon_days: int
    probability: float
    severity: str
    top_factors: tuple[Mapping[str, FactValue], ...]
    model_version: str
    is_baseline: bool
    metrics: Mapping[str, FactValue] | None

    def __post_init__(self) -> None:
        # `top_factors` is a tuple of mappings: the tuple is immutable already, so
        # each mapping inside it is the part that could still be written to.
        object.__setattr__(
            self, "top_factors", tuple(_read_only(factor) for factor in self.top_factors)
        )
        if self.metrics is not None:
            object.__setattr__(self, "metrics", _read_only(self.metrics))


@dataclass(frozen=True, slots=True)
class PlotFacts:
    """Everything the answer may say about one plot, assembled before the call.

    docs/06-diseno-detallado.md §9: the facts of a single plot, read from the five
    facades and handed to the LLM in **one** block; and, for the degraded answer,
    "estos mismos hechos junto con los documentos relevantes" (docs/06 §9).

    `missing` is a **name list, not a value list**: every family above can be
    absent, and each absent one is named so the answer can say "no hay información
    disponible" instead of showing an empty section that reads as "nothing to
    report". A family that is present is never also in `missing`.

    **No geometry, no municipality, no contact field** (D-T12.6): not
    `boundary`, not `location`, not `municipality_code`. `plot_name` is the
    producer's own name for their plot, which is what makes an answer legible;
    where the plot is does not make it better.
    """

    plot_id: UUID
    plot_name: str
    sensors: tuple[SensorFact, ...]
    alerts: tuple[AlertFact, ...]
    irrigation: IrrigationFact | None
    seasonal_climate: SeasonalClimateFact | None
    risk: tuple[RiskFact, ...]
    missing: tuple[MissingFactKind, ...]


def _read_only(values: Mapping[str, FactValue]) -> Mapping[str, FactValue]:
    """`values` as a mapping no caller can write through.

    `MappingProxyType` alone is not enough: it is a *view*, so a caller that kept
    the `dict` it passed in could still mutate the fact through it. Copying first
    is what makes the fact a snapshot, which is the property the sanitization and
    the deterministic rendering both rely on.
    """
    return MappingProxyType(dict(values))
