"""The adoption-month read endpoint (docs/04-api.md:224, 234; D-T0.10).

Against the real database and the real router: the stored row, the `month` of the
query and org isolation are all adapter behavior, so a double at the port would
prove none of them.

Every negative test carries its negative assertion: the state the rejected call
must not have changed follows the call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from metrics.conftest import MONTH
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.main import app
from techcamp.metrics.adapters.orm import PlotMetricMonthlyRow

pytestmark = pytest.mark.anyio

_COMPUTED_AT = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
"""Frozen: what the month-1 02:00 job wrote (D-T0.7)."""

_MONTH_QUERY = "2026-09"


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _stored_month(db_session: AsyncSession, *, plot_id: UUID, org_id: UUID) -> None:
    """One stored plot-month with three components and a null `decision`.

    The null component is the interesting figure: a rainfed plot has no applied
    depth to follow (docs/11-metricas.md:52), so it must reach the wire as `null`
    and never as 0 (D-T0.3). The index is the 100 points split over the three
    components that do have a denominator: `100 × (0.5 + 0.25 + 0.75) / 3 = 50`.
    """
    db_session.add(
        PlotMetricMonthlyRow(
            plot_id=plot_id,
            month=MONTH,
            org_id=org_id,
            monitoring=Decimal("0.5"),
            record_keeping=Decimal("0.25"),
            decision=None,
            risk_management=Decimal("0.75"),
            digital_adoption_index=Decimal("50"),
            computed_at=_COMPUTED_AT,
        )
    )
    await db_session.commit()
