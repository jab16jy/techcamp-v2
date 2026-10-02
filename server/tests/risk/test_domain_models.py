"""Severity rule of a risk prediction (docs/08-ml.md §M2 "Severidad", D-T0.4).

Pure: no database, no HTTP, no clock. `severity_for` is the one rule every
writer of a prediction shares — the serving job (T6b), the baseline ladder
(`ml/`) — and it reads the thresholds of the version that produced the
probability, never a constant of the server (docs/03-modelo-datos.md §Umbrales).
"""

from __future__ import annotations

import pytest

from techcamp.risk.domain.models import Severity, severity_for

pytestmark = pytest.mark.anyio


async def test_severity_is_high_exactly_at_the_high_threshold() -> None:
    """`alto`: probability >= the threshold that gives precision >= 0.7 in
    validation (docs/08 §M2 "Severidad"). The boundary belongs to `high`, not to
    `low`: the threshold is the smallest probability that passed."""
    assert severity_for(0.70, {"high": 0.70, "critical": 0.85}) is Severity.HIGH
    # Just below it is still `bajo`, never `high`.
    assert severity_for(0.6999, {"high": 0.70, "critical": 0.85}) is Severity.LOW


async def test_severity_is_critical_exactly_at_the_critical_threshold() -> None:
    """`crítico`: probability >= the threshold that gives precision >= 0.85,
    checked before `alto` so a critical probability is never reported as high."""
    assert severity_for(0.85, {"high": 0.70, "critical": 0.85}) is Severity.CRITICAL
    # Below the critical threshold the same probability is `high`, not `critical`.
    assert severity_for(0.84, {"high": 0.70, "critical": 0.85}) is Severity.HIGH


async def test_no_critical_threshold_means_the_version_never_reaches_critical() -> None:
    """`crítico` exists "solo si existe" (docs/08 §M2 "Severidad"): a version
    whose calibration never reached precision 0.85 carries `"critical": null`
    and its ceiling is `high`, however high the probability is."""
    assert severity_for(1.0, {"high": 0.70, "critical": None}) is Severity.HIGH
    # Below the high threshold it is still `low`: no threshold means no severity.
    assert severity_for(0.69, {"high": 0.70, "critical": None}) is Severity.LOW


async def test_a_probability_with_no_threshold_at_all_is_low() -> None:
    """A version with no `high` threshold has nothing to be high against: the
    probability is reported, the severity stays `bajo`. A threshold is missing
    evidence, not a zero one."""
    assert severity_for(0.5, {}) is Severity.LOW
    assert severity_for(0.5, {"critical": 0.85}) is Severity.LOW


async def test_the_critical_threshold_is_still_honoured_without_a_high_threshold() -> None:
    """The two thresholds are independent: a version calibrated only for the
    critical operating point is critical at its threshold and `low` below it."""
    assert severity_for(0.9, {"critical": 0.85}) is Severity.CRITICAL
    assert severity_for(0.8, {"critical": 0.85}) is Severity.LOW


async def test_the_extreme_probabilities_keep_their_severity() -> None:
    """A calibrated classifier emits 0.0 and 1.0; both are ordinary values of
    the rule, so neither is special-cased."""
    assert severity_for(0.0, {"high": 0.70, "critical": 0.85}) is Severity.LOW
    assert severity_for(1.0, {"high": 0.70, "critical": 0.85}) is Severity.CRITICAL


async def test_severity_codes_are_the_documented_ones() -> None:
    """The API and the stored rows speak `low`, `high`, `critical`
    (docs/08 §M2 "Severidad", docs/04 §Riesgo, métricas y asistente), while the
    prose says `alto`/`crítico`."""
    assert [severity.value for severity in Severity] == ["low", "high", "critical"]
