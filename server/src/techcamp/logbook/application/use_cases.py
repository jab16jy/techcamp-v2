"""Logbook use cases for extension visits read API (docs/04 §Visitas; docs/09; D9)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from techcamp.farms.application.manage_farms import resolve_farm_access
from techcamp.farms.application.ports import FarmRepository
from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.application.resolve_org_access import resolve_org_membership
from techcamp.logbook.application.ports import ExtensionVisitRepository
from techcamp.logbook.domain.models import ExtensionVisit, ensure_can_export_visits


async def list_farm_visits(
    *,
    user_id: UUID,
    farm_id: UUID,
    visits: ExtensionVisitRepository,
    farms: FarmRepository,
    memberships: MembershipRepository,
    limit: int = 50,
    cursor: UUID | None = None,
) -> list[ExtensionVisit]:
    """docs/04 `GET /farms/{farm_id}/visits`: any member of the farm's org.

    Farm access resolution checks every org the caller belongs to and raises
    `FarmNotFoundError` (404) if not found or in an unassociated org.
    """
    farm, _role = await resolve_farm_access(
        user_id=user_id, farm_id=farm_id, farms=farms, memberships=memberships
    )
    return await visits.list_for_farm(
        farm_id=farm.id,
        org_id=farm.org_id,
        limit=limit,
        cursor=cursor,
    )


async def list_org_visits(
    *,
    user_id: UUID,
    org_id: UUID,
    visits: ExtensionVisitRepository,
    memberships: MembershipRepository,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 50,
    cursor: UUID | None = None,
) -> list[ExtensionVisit]:
    """docs/04 `GET /organizations/{org_id}/visits?from=&to=`: export (RF-19).

    Membership is resolved first -> `NotAMemberError` (404 in adapter) so
    non-members never discover organization existence.
    Role must be owner or technician -> `VisitExportForbiddenError` (403 in adapter).
    """
    membership = await resolve_org_membership(
        user_id=user_id, org_id=org_id, memberships=memberships
    )
    ensure_can_export_visits(membership.role)
    return await visits.list_for_org(
        org_id=org_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        cursor=cursor,
    )


async def get_latest_visit_dates(
    *,
    farm_ids: Sequence[UUID],
    org_ids: Sequence[UUID],
    visits: ExtensionVisitRepository,
) -> dict[UUID, date]:
    """Last visit date per farm for a set of farms within a set of orgs (docs/04 §Visitas; D-T0.8).

    Farms without visits are absent from the returned dictionary.
    """
    if not farm_ids or not org_ids:
        return {}
    return await visits.get_latest_visit_dates_for_farms(farm_ids=farm_ids, org_ids=org_ids)
