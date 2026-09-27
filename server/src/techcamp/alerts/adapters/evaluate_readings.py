"""Composition of the reading-threshold evaluator (D9, D14).

`telemetry` must never import `alerts` (docs/05 has no `telemetry → alerts`
edge), so the evaluator is built here, on the session the ingestor flush already
opened, and injected into `ingest_uplinks` as its `after_flush` hook. The
repositories are the same `AsyncSession` (the `farms` → `weather` precedent):
the alert write joins the unit of work of the batch it was decided from, and
opens its own transaction for the alert and its `notification` rows (ADR-0016).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.application.evaluate_readings import evaluate_landed_readings
from techcamp.farms.adapters.repositories import (
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.application.ingest_uplinks import AfterFlush
from techcamp.telemetry.domain.models import ReadingEvent


def build_evaluator(session: AsyncSession) -> AfterFlush:
    """The `after_flush` hook: decides the org's reading-threshold rules over the
    readings that landed, and returns nothing because the ingestor owns the loop
    (`_flush_with_retry` keeps the batch for the next attempt if this raises)."""

    async def after_flush(events: Sequence[ReadingEvent]) -> None:
        await evaluate_landed_readings(
            events=events,
            rules=SqlAlchemyAlertRuleRepository(session),
            readings=SqlAlchemyReadingRepository(session),
            sensors=SqlAlchemySensorRepository(session),
            nodes=SqlAlchemyNodeRepository(session),
            plots=SqlAlchemyPlotRepository(session),
            soils=SqlAlchemySoilProfileRepository(session),
            alerts=SqlAlchemyAlertRepository(session),
        )

    return after_flush
