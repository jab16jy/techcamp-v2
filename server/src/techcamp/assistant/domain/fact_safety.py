"""What a fact may carry, and what it may never carry (E12 T4; D-T12.6;
docs/09-cuellos-de-botella.md §Seguridad, filas "Prompt injection en el
asistente" y "Datos personales (Ley 1581)"; ADR-0007:39).

Two of the documents' sentences are the whole rule: the fragments RAG and the
user's text travel as **data** and not as instructions, and *only the minimum
necessary leaves the server, with no personal data* (ADR-0007:39). A sentence
like that is not checkable in review, so this module makes it mechanical:

* **A `FactValue` is a scalar.** `str | int | float | bool`. Everything else is
  dropped — a nested dict, a list, a `None`, a `datetime` — because a nested
  free-form object is exactly where personal data hides: an allowlist that kept
  one would pass the key it recognises and smuggle the value it does not.
* **Key allowlists derived from the real producers.** Each set below names the
  files that write the object it describes, so a producer that adds a key breaks
  a test here instead of quietly widening what leaves the server.
* **A denylist and a value ban-list as defence in depth.** The allowlists
  describe *today's* producers; the denylist and the compiled patterns are what
  survive the day one of them starts writing a phone number.

Nothing here reads a clock, a database or a provider: pure, deterministic, and
it never raises. A sanitizer that raised on one unexpected JSONB value would
fail the producer's whole question, which is a worse failure than a figure the
answer does not carry.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

type FactValue = str | int | float | bool
"""The only four things a fact may hold.

Scalars and nothing else. A `None` is not a value here: absence is carried by the
fact being absent (`MissingFactKind`), never by an empty value inside a present
one (D-T12.1, "la ausencia de evidencia es `null`, nunca `0`").
"""

ALERT_EVIDENCE_KEYS: frozenset[str] = frozenset(
    {
        # `alerts/application/evaluate_readings.py` (sensor-threshold rules).
        "samples",
        "at",
        # `alerts/application/evaluate_water_stress.py` (the stored balance).
        "day",
        "source",
        # `alerts/application/evaluate_weather_rules.py` (forecast rules).
        # `alerts/application/evaluate_node_health.py` (node health).
        "last_seen_at",
        "interval_s",
        # `alerts/domain/models.prediction_evidence`, which adds the identity of
        # the prediction an alert was decided from (docs/06 §8: "toda alerta se
        # puede rastrear hasta el modelo exacto").
        "event",
        "severity",
        "issued_at",
        "horizon_start",
        "model_version_id",
    }
)
"""The keys `alert.evidence` may hold (docs/06-diseno-detallado.md §3 y §8).

Derived from the four evaluators that open an alert and from
`alerts/domain/models.prediction_evidence`, which is why `source` (the string
`"balance"`, naming the stored daily balance a `water_stress` alert came from)
and `day` are in the set while no other producer's field is.
"""

IRRIGATION_RATIONALE_KEYS: frozenset[str] = frozenset(
    {
        # `irrigation/domain/models.py`, the `rationale` of
        # `decide_recommendation` (docs/06 §5, FAO-56).
        "et0_mm",
        "kc",
        "kc_source",
        "kc_approximate",
        "p",
        "raw_mm",
        "taw_mm",
        "depletion_model_mm",
        "depletion_mm",
        "k",
        "without_sensor",
        "forecast_rain_48h_mm",
        "forecast_rain_7d_mm",
        "forecast_et0_7d_mm",
        # The three confidence flags docs/06 §5 asks for instead of presenting a
        # recommendation as if the forecast were complete.
        "low_confidence",
        "forecast_missing",
        "missing_observed_weather",
    }
)
"""The keys `irrigation_recommendation.rationale` may hold (docs/06 §5).

`without_sensor` and `kc_approximate` are the two flags that say the number is
weaker than it looks — the assimilation weight `K` is zero without a
representative sensor, and the Kc was taken from a similar crop — and an answer
that hides them explains a recommendation the field would contradict.
"""

RISK_METRICS_KEYS: frozenset[str] = frozenset(
    {"pr_auc", "pr_auc_ci_low", "pr_auc_ci_high", "brier"}
)
"""The keys `model_version.metrics` / `baseline_metrics` may hold.

The four validation figures of the ML register (ADR-0020 paso 5,
`ml/src/techcamp_ml/models/flood_m2/registration.py`'s `METRIC_COLUMNS`), which
is where docs/04-api.md §Riesgo, métricas y asistente says the answer's risk
citation takes its metric from.
"""

RISK_FACTOR_KEYS: frozenset[str] = frozenset({"feature", "value", "contribution"})
"""The keys one `risk_prediction.top_factors` entry may hold.

Exactly the shape docs/04-api.md §Riesgo, métricas y asistente declares and
`risk/adapters/api/router.py`'s `TopFactorView` validates: the feature, its
value and its contribution (docs/08-ml.md §M2 "Factores").
"""

DENIED_KEYS: frozenset[str] = frozenset(
    {
        # Personal data, in the spellings the repo and the wire use. ADR-0007:39
        # allows none of it to reach the provider, and both Spanish and English
        # spellings are here because a `jsonb` object is written by hand.
        "phone",
        "email",
        "telefono",
        "correo",
        "contact",
        # Geometry: no `farm` / `plot` boundary or location leaves the server
        # (D-T12.6; docs/09 §Seguridad).
        "boundary",
        "location",
        "geometry",
        # Secrets. Not personal data and not evidence either: a key named like
        # one never belongs in a fact.
        "token",
        "password",
        "secret",
        "api_key",
    }
)
"""Keys refused **whatever** the allowlist says (D-T12.6).

Defence in depth, and the reason `sanitize_fact_values` checks this set after
the allowlist rather than relying on the two never overlapping: the allowlists
are derived from today's producers, and this set is what holds when one of them
starts writing a key from here.
"""

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
"""An e-mail shape: a local part, an `@` and a dotted domain. The domain needs at
least one dot, so an `at`-bearing fragment of agronomic text is not caught."""

_PHONE_INTERNATIONAL = re.compile(r"\+\d[\d\s().-]{5,}\d")
"""An international number written from its `+`: `+57 300 123 4567`,
`+573001234567`.

A `+` followed by digits is deliberately narrow, because a `+` also appears in
every ISO instant (`T14:00:00+00:00`) — and that string is the value of the
`at` key of most of `alert.evidence`, so a looser pattern would empty the fact
block of every alert."""

_PHONE_COLOMBIAN = re.compile(r"(?<!\d)3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
"""A Colombian mobile written the way a person writes it: `300 123 4567`,
`301-555-7788`, `3001234567`. Ten digits starting with `3` and no country code
is still a phone number here, and it is the shape most likely to arrive in a
free-text field."""

_PHONE_COLOMBIAN_LANDLINE = re.compile(r"(?<!\d)60[1-8][\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
"""A Colombian landline: the ten-digit area-code form, `60[1-8]` for Bogotá,
Cali, Medellín, Bucaramanga, Pereira, Cartagena and Ibagué (R3-PHONE-DETECTOR-GAP).

The mobile pattern above only recognizes numbers beginning with `3`, so a farm's
fixed line — the number a technician and a seed house are reached at — passed
through a ten-digit run unnoticed, and a fact block that promised to carry no
contact data was carrying one. Ten digits from `60` and a digit `1`-`8` is
specific: no ISO instant, UUID or agronomic figure today's producers write has
that shape, which is the test the mobile pattern above also has to pass.
"""

_PHONE_LABELLED = re.compile(
    r"(?i)\b(?:tel|telefono|tel[eé]fono|cel|celular|whatsapp|contacto)\b\s*[:.\-]?\s*\d[\d\s().-]{5,}\d"
)
"""A number the label names: `Tel: 2345678`, `celular 3001234567`, `contacto
604 123 4567`.

A bare seven-digit local number is **not** matched on its own, and that is
deliberate: seven digits appear inside ISO instants, UUID fragments and the
figures of `rationale`, and a filter that ate them would empty the fact block of
the very evidence it is meant to protect. The written form of a short number
almost always carries its label, so the label is what this pattern anchors on —
the cost of the decision is one unlabelled seven-digit number, and it is stated
here rather than left as a gap somebody has to rediscover."""

_WKT_KEYWORD = re.compile(r"\b(?:POINT|LINESTRING|POLYGON|MULTIPOINT|MULTIPOLYGON)\s*\(|SRID\s*=")
"""A geometry in its labelled shape: a WKT function call or a `SRID=4326;`
prefix, which is how `Farm.location` and `Plot.boundary` are stored
(docs/03-modelo-datos.md §`farm`, `plot`)."""

_COORDINATE_PAIR = re.compile(r"-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+")
"""A bare coordinate pair: `-74.1,10.9`.

The unlabelled form, because the labelled one is only the way the database
happens to store it today. Two decimals on both sides and a comma between them
is not a shape any of today's producers writes for anything else, and a false
positive costs one figure instead of a leaked location."""

BANNED_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _EMAIL,
    _PHONE_INTERNATIONAL,
    _PHONE_COLOMBIAN,
    _PHONE_COLOMBIAN_LANDLINE,
    _PHONE_LABELLED,
    _WKT_KEYWORD,
    _COORDINATE_PAIR,
)
"""Compiled once, at import: the value-level ban-list.

Compiled at import because a `re` cache lookup per value is a lookup per value
on the hot path of every question, and because a pattern that is compiled at
import time cannot be a pattern somebody edits without noticing the test that
pins it.
"""


def contains_contact_data(text: str) -> bool:
    """Whether a string looks like a contact detail or a geometry.

    True means the string is **dropped**, not that the document is refused: the
    sibling values around it are still real evidence, and an answer missing one
    figure is a better failure than one that carried a phone number (ADR-0007:39).

    Every pattern is deliberately narrow, and each one has a value it must NOT
    catch — the ISO instants in `at`, `issued_at`, `day` and `last_seen_at` above
    all — because a ban-list that is too eager empties the fact block of exactly
    the alerts the producer needs to read.

    A **labelled** short number counts: a farm's own fixed line is written as
    "Tel: 2345678" at least as often as it is written as a bare run of digits,
    and the label is the one part of that string no piece of evidence carries.
    """
    return any(pattern.search(text) for pattern in BANNED_VALUE_PATTERNS)


def sanitize_fact_values(
    values: Mapping[str, Any], *, allowed_keys: frozenset[str]
) -> Mapping[str, FactValue]:
    """The values of `values` a fact may carry, and nothing else.

    Three filters, in this order: a key outside `allowed_keys` is dropped, a key
    inside `DENIED_KEYS` is dropped whatever the allowlist says, and a value
    that is not a `FactValue` — or that matches `BANNED_VALUE_PATTERNS` — is
    dropped.

    The result is a **read-only** mapping over a private copy, not the `dict` it
    filled: the sanitization is only a guarantee while nothing can write to what
    it produced, and a caller holding the returned mapping could otherwise add a
    key the allowlist never approved (R3-MUTABLE-FACT-MAPS). The fact dataclasses
    copy it again on construction, so neither the caller's object nor the
    sanitizer's is the fact's own storage.

    **Iterates the mapping's own order, not `allowed_keys`.** `allowed_keys` is a
    `frozenset` and its iteration order depends on the values' hashes, so
    ordering by it would make two identical evidence objects sanitize into two
    differently-ordered blocks. The mapping's order is the producer's and is
    deterministic for a given input; where an answer renders the block, the keys
    are sorted explicitly (`domain.degraded`).

    Never raises. `values` is `jsonb` written by six other modules and this is
    called on every question, so anything unexpected is dropped rather than
    propagated: an object whose `__repr__` raises is not evidence either.
    """
    sanitized: dict[str, FactValue] = {}
    for key, value in values.items():
        if key not in allowed_keys or key in DENIED_KEYS:
            continue
        coerced = _coerce_fact_value(value)
        if coerced is not None:
            sanitized[key] = coerced
    return MappingProxyType(sanitized)


def _coerce_fact_value(value: Any) -> FactValue | None:
    """`value` as a `FactValue`, or `None` when it may not be one.

    The order of the tests is the whole of it: `bool` before `int`, because
    `isinstance(True, int)` is true and a boolean that came out as `1` would be
    a flag rendered as a count.

    A `str` **subclass** is read through `str()` and not kept as the subclass
    instance: the alert evaluators write `StrEnum` members into their evidence
    (`prediction.event`), and a prompt that carries an enum object instead of its
    value is a serialization decision made in the wrong layer.

    A non-finite float is dropped. `nan` and `inf` are what a missing number
    becomes when something upstream divides by zero; they are a bug wearing a
    value's clothes, and `json.dumps` writes them as `NaN` / `Infinity`, which is
    not JSON.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = str(value)
        return None if contains_contact_data(text) else text
    return None
