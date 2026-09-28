"""Extension visit read endpoints (docs/04 §Visitas; docs/09; D9).

GET /api/v1/farms/{farm_id}/visits
GET /api/v1/organizations/{org_id}/visits?from=&to=
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from techcamp.farms.adapters.api.deps import FarmRepoDep
from techcamp.farms.domain.errors import FarmNotFoundError
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.identity.domain.errors import NotAMemberError
from techcamp.logbook.adapters.api.deps import VisitRepoDep
from techcamp.logbook.application import list_farm_visits, list_org_visits
from techcamp.logbook.domain import ExtensionVisit, InsufficientRoleError
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["visits"])


class ExtensionVisitView(BaseModel):
    id: UUID
    org_id: UUID
    farm_id: UUID
    plot_id: UUID | None
    technician_id: UUID
    visited_on: date
    topics: list[str]
    recommendations: str | None
    commitments: str | None
    notes: str | None
    client_updated_at: datetime
    server_version: int
    deleted_at: datetime | None = None


class ExtensionVisitPage(BaseModel):
    items: list[ExtensionVisitView]
    next_cursor: str | None


def _visit_view(visit: ExtensionVisit) -> ExtensionVisitView:
    return ExtensionVisitView(
        id=visit.id,
        org_id=visit.org_id,
        farm_id=visit.farm_id,
        plot_id=visit.plot_id,
        technician_id=visit.technician_id,
        visited_on=visit.visited_on,
        topics=visit.topics,
        recommendations=visit.recommendations,
        commitments=visit.commitments,
        notes=visit.notes,
        client_updated_at=visit.client_updated_at,
        server_version=visit.server_version,
        deleted_at=visit.deleted_at,
    )


@router.get("/farms/{farm_id}/visits", response_model=ExtensionVisitPage)
async def get_farm_visits(
    farm_id: UUID,
    user_id: CurrentUserId,
    visits: VisitRepoDep,
    farms: FarmRepoDep,
    memberships: MembershipRepoDep,
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> ExtensionVisitPage:
    """docs/04 `GET /farms/{farm_id}/visits`: any member of the farm's org."""
    try:
        page = await list_farm_visits(
            user_id=user_id,
            farm_id=farm_id,
            visits=visits,
            farms=farms,
            memberships=memberships,
            limit=limit,
            cursor=cursor,
        )
    except FarmNotFoundError as exc:
        raise ProblemError(status=404, title="Farm not found") from exc

    next_cursor = str(page[-1].id) if len(page) == limit else None
    return ExtensionVisitPage(items=[_visit_view(visit) for visit in page], next_cursor=next_cursor)


@router.get("/organizations/{org_id}/visits", response_model=ExtensionVisitPage)
async def get_org_visits(
    org_id: UUID,
    user_id: CurrentUserId,
    visits: VisitRepoDep,
    memberships: MembershipRepoDep,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> ExtensionVisitPage:
    """docs/04 `GET /organizations/{org_id}/visits?from=&to=`: export (RF-19)."""
    try:
        page = await list_org_visits(
            user_id=user_id,
            org_id=org_id,
            visits=visits,
            memberships=memberships,
            from_date=from_,
            to_date=to,
            limit=limit,
            cursor=cursor,
        )
    except NotAMemberError as exc:
        raise ProblemError(status=404, title="Organization not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot export visits") from exc

    next_cursor = str(page[-1].id) if len(page) == limit else None
    return ExtensionVisitPage(items=[_visit_view(visit) for visit in page], next_cursor=next_cursor)
