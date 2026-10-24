"""The send-list and the ban-list as code (E12 T4; D-T12.6; docs/09-cuellos-de-botella.md
§Seguridad, fila "Prompt injection en el asistente"; ADR-0007:39).

Only the minimum necessary leaves the server and with it **no personal data**
(ADR-0007:39). Two rules make that mechanical instead of a promise in review:
a key **allowlist** derived from the producers that write each object, and a
**denylist** of keys plus **value patterns** that catch a contact detail or a
geometry wherever it hides.

The negative assertions are the point. `alert.evidence`, the irrigation
`rationale` and `risk_prediction.top_factors` are free-form JSONB written by
other modules, so nothing upstream guarantees they hold a phone number; this
module is where that guarantee is made, and each test below proves the string
does not survive.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from techcamp.assistant.domain.fact_safety import (
    ALERT_EVIDENCE_KEYS,
    DENIED_KEYS,
    IRRIGATION_RATIONALE_KEYS,
    RISK_FACTOR_KEYS,
    RISK_METRICS_KEYS,
    contains_contact_data,
    sanitize_fact_values,
)


def _serialized(values: dict[str, object]) -> str:
    """What the fact block would carry, as one string: the shape the send-list
    test of D-T12.6 asserts over."""
    return json.dumps(values, sort_keys=True, default=str)


def test_an_e_mail_a_phone_and_a_geometry_never_survive_into_the_fact_block() -> None:
    """ADR-0007:39 ("sin datos personales"), D-T12.6. A free-form `evidence` that
    somebody once filled with contact data and a WKT geometry comes out with
    neither, while the agronomic keys around them are kept."""
    evidence = {
        "at": "2026-10-06T14:00:00+00:00",
        "samples": 3,
        "technician_phone": "+57 300 123 4567",
        "contact": "camila.perez@cooperative.co",
        "boundary": "POLYGON((-74.1 10.9, -74.0 10.9, -74.0 11.0, -74.1 10.9))",
    }

    sanitized = sanitize_fact_values(evidence, allowed_keys=ALERT_EVIDENCE_KEYS)
    block = _serialized(sanitized)

    assert sanitized == {"at": "2026-10-06T14:00:00+00:00", "samples": 3}
    assert "300 123 4567" not in block
    assert "camila.perez@cooperative.co" not in block
    assert "POLYGON" not in block


def test_a_colombian_mobile_without_a_country_code_is_caught_too() -> None:
    """A `3xx xxx xxxx` is a Colombian mobile written the way a person writes it,
    so the pattern that finds `+57 300…` has to find this one as well: a ban-list
    that only catches the international shape is no ban-list."""
    assert contains_contact_data("escríbale al 300 123 4567")
    assert contains_contact_data("+573001234567")
    assert contains_contact_data("teléfono: 301-555-7788")


def test_a_colombian_landline_is_caught_like_a_mobile_is() -> None:
    """R3-PHONE-DETECTOR-GAP. The mobile pattern only knows numbers beginning with
    `3`, so a farm's **fixed** line — the ten-digit area-code form a technician or
    a seed house is reached at — passed through untouched while the ban-list
    claimed to carry no contact data. `601` is Bogotá, `604` Medellín."""
    for written in ("6041234567", "604 123 4567", "601-234-5678", "llame al 6073334444"):
        assert contains_contact_data(written), written


def test_a_labelled_short_number_is_caught_while_a_bare_iso_instant_is_not() -> None:
    """R3-PHONE-DETECTOR-GAP, second half. A seven-digit local number is written
    with a label far more often than bare, and the label is the one part of that
    string no evidence carries — so the label is what the pattern anchors on.

    The other half is the price of that decision: the ISO instants that are the
    `at`, `issued_at`, `day` and `last_seen_at` of real evidence must survive, or
    the filter empties the very alerts it exists to protect."""
    assert contains_contact_data("Tel: 2345678")
    assert contains_contact_data("celular 3001234567")
    assert not contains_contact_data("2026-10-06T14:00:00+00:00")
    assert not contains_contact_data("2026-10-06")


def test_the_sanitized_mapping_cannot_be_written_through() -> None:
    """R3-MUTABLE-FACT-MAPS. Sanitization is only a guarantee while nothing can
    write to what it produced: a caller that kept the returned mapping could add
    a key the allowlist never approved, and the block would leave the server
    carrying it."""
    sanitized = sanitize_fact_values({"samples": 3}, allowed_keys=ALERT_EVIDENCE_KEYS)

    with pytest.raises(TypeError):
        sanitized["technician_phone"] = "6041234567"  # type: ignore[index]


def test_a_bare_coordinate_pair_is_caught_as_geometry() -> None:
    """docs/09 §Seguridad: the plot's geometry never leaves the server. The WKT
    keywords are only the labelled shape; a bare `-74.1,10.9` pair is the same
    location with no label on it."""
    assert contains_contact_data("centroide -74.1,10.9")
    assert contains_contact_data("SRID=4326;POINT(-74.1 10.9)")


def test_ordinary_agronomic_text_is_not_contact_data() -> None:
    """The negative half, and the reason the patterns are narrow: an answer that
    dropped real evidence would degrade into "no hay información" and hide the
    facts the producer needs. ISO instants in particular must survive — every
    alert's `at` carries one, and `+00:00` is not a phone number."""
    for text in (
        "2026-10-06T14:00:00+00:00",
        "2026-10-06T09:00:00-05:00",
        "balance",
        "climatology_month",
        "delay_sowing",
        "inundación",
        "",
    ):
        assert not contains_contact_data(text), text


def test_an_unknown_key_is_dropped_even_when_its_value_is_harmless() -> None:
    """The allowlist is derived from the producers, so a key nobody writes is a
    key the assistant does not send: the first producer to add one has to add it
    here, in review, on purpose."""
    sanitized = sanitize_fact_values(
        {"samples": 3, "farm_notes": "todo bien"}, allowed_keys=ALERT_EVIDENCE_KEYS
    )

    assert sanitized == {"samples": 3}
    assert "farm_notes" not in sanitized


def test_a_nested_object_is_dropped_rather_than_flattened() -> None:
    """A nested free-form object is where personal data hides, so the coercion
    keeps scalars only. Flattening would turn `{"phone": {"number": "…"}}` into a
    key the denylist no longer recognises."""
    sanitized = sanitize_fact_values(
        {"at": "2026-10-06T14:00:00+00:00", "last_seen_at": {"value": "+57 300 123 4567"}},
        allowed_keys=ALERT_EVIDENCE_KEYS,
    )

    assert sanitized == {"at": "2026-10-06T14:00:00+00:00"}


def test_everything_that_is_not_a_scalar_is_dropped() -> None:
    """`None`, a `datetime`, a `list` and a `Decimal` are not `FactValue`s. The
    `None` matters on its own: `evaluate_node_health` writes
    `last_seen_at=None` for a node that has never been heard from, and that is
    missing evidence the answer must report as such, not a value to print."""
    sanitized = sanitize_fact_values(
        {
            "last_seen_at": None,
            "at": datetime(2026, 10, 6, 14, tzinfo=UTC),
            "interval_s": Decimal("900"),
            "samples": [1, 2, 3],
        },
        allowed_keys=ALERT_EVIDENCE_KEYS,
    )

    assert sanitized == {}


def test_a_denied_key_is_dropped_even_when_it_is_also_allowlisted() -> None:
    """Defence in depth: the allowlists are derived from today's producers, and
    the denylist is what survives the day a producer starts writing one of these
    keys. Passing `DENIED_KEYS` as the allowlist is exactly that mistake."""
    sanitized = sanitize_fact_values(
        {"phone": "+57 300 123 4567", "email": "a@b.co", "token": "abc", "boundary": "POINT(1 2)"},
        allowed_keys=DENIED_KEYS,
    )

    assert sanitized == {}


def test_a_non_finite_float_is_dropped_because_it_is_not_a_value() -> None:
    """`nan` and `inf` are what a missing number becomes when something divides
    by zero upstream. They are not a finding, they are a bug wearing a value's
    clothes, and `json.dumps` would write them as `NaN`, which is not JSON."""
    sanitized = sanitize_fact_values(
        {"samples": float("nan"), "interval_s": float("inf"), "at": 1.5},
        allowed_keys=ALERT_EVIDENCE_KEYS,
    )

    assert sanitized == {"at": 1.5}


def test_sanitizing_an_unexpected_object_never_raises() -> None:
    """The evidence is JSONB written by six other modules, so this function is
    reached with values it was not designed for. It drops what it does not
    understand and returns: an answer that fails to render because of one bad key
    is a worse failure than an answer missing one figure."""

    class Unprintable:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    sanitized = sanitize_fact_values(
        {"samples": 2, "source": Unprintable(), "at": "2026-10-06T14:00:00+00:00"},
        allowed_keys=ALERT_EVIDENCE_KEYS,
    )

    assert sanitized == {"samples": 2, "at": "2026-10-06T14:00:00+00:00"}


def test_the_same_input_sanitizes_to_the_same_values_every_time() -> None:
    """The fact block is assembled once, before the single call, and it has to be
    the same block for the same evidence: an order that came from a set or from
    dict iteration would make two identical answers differ for no reason."""
    rationale = {
        "et0_mm": 4.5,
        "kc": 0.9,
        "kc_source": "fao56",
        "taw_mm": 118.0,
        "low_confidence": True,
    }

    first = sanitize_fact_values(rationale, allowed_keys=IRRIGATION_RATIONALE_KEYS)
    second = sanitize_fact_values(rationale, allowed_keys=IRRIGATION_RATIONALE_KEYS)

    assert (
        first
        == second
        == {
            "et0_mm": 4.5,
            "kc": 0.9,
            "kc_source": "fao56",
            "taw_mm": 118.0,
            "low_confidence": True,
        }
    )


def test_the_alert_allowlist_is_exactly_what_the_four_evaluators_write() -> None:
    """`alerts/application/evaluate_readings.py`, `evaluate_water_stress.py`,
    `evaluate_weather_rules.py`, `evaluate_node_health.py` and
    `alerts/domain/models.prediction_evidence`. This test is the tripwire: a
    producer that adds a key breaks it, and the fix is a decision here rather
    than a surprise in a prompt."""
    assert ALERT_EVIDENCE_KEYS == frozenset(
        {
            "event",
            "severity",
            "issued_at",
            "horizon_start",
            "model_version_id",
            "samples",
            "at",
            "day",
            "source",
            "last_seen_at",
            "interval_s",
        }
    )


def test_the_irrigation_allowlist_is_exactly_what_the_rationale_builds() -> None:
    """`irrigation/domain/models.py`, the `rationale` dict of
    `decide_recommendation` plus its three optional confidence flags."""
    assert IRRIGATION_RATIONALE_KEYS == frozenset(
        {
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
            "low_confidence",
            "forecast_missing",
            "missing_observed_weather",
        }
    )


def test_the_risk_allowlists_are_the_api_shapes_of_docs_04() -> None:
    """docs/04-api.md §Riesgo, métricas y asistente: `top_factors` entries are
    `{ feature, value, contribution }`, and `model_version.metrics` are the
    four validation figures of the register (ADR-0020)."""
    assert RISK_FACTOR_KEYS == frozenset({"feature", "value", "contribution"})
    assert RISK_METRICS_KEYS == frozenset({"pr_auc", "pr_auc_ci_low", "pr_auc_ci_high", "brier"})


def test_the_denylist_names_the_personal_and_the_geometric_fields_the_docs_ban() -> None:
    """docs/09 §Seguridad ("Datos personales (Ley 1581)") and D-T12.6 ("no
    `farm`/`plot` geometry"). Both Spanish spellings are here because the repo's
    own columns use them (`Farm.municipality_code` is DIVIPOLA text, and the
    wire format of the auth endpoints is Spanish)."""
    assert {"phone", "email", "telefono", "correo", "contact", "boundary", "location"} <= (
        DENIED_KEYS
    )
    assert {"geometry", "token", "password", "secret", "api_key"} <= DENIED_KEYS
