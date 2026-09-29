"""`GET /sync/pull` use case (docs/04 §Bitácora; docs/06 §7; D1, D2).

Merges `logbook_entry` and `extension_visit` changes across every organization
the caller is a member of, ordered strictly by `server_version` ascending.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from techcamp.logbook.domain.models import ExtensionVisit, LogbookEntry, SyncEntity, SyncOp

if TYPE_CHECKING:
    from techcamp.identity.application.ports import MembershipRepository
    from techcamp.logbook.application.ports import (
        ExtensionVisitSyncRepository,
        LogbookEntrySyncRepository,
    )


@dataclass(frozen=True, slots=True)
class PullChangeItem:
    id: UUID
    entity: SyncEntity
    op: SyncOp
    server_version: int
    data: LogbookEntry | ExtensionVisit


@dataclass(frozen=True, slots=True)
class PullPage:
    changes: list[PullChangeItem]
    next_since: int
    has_more: bool


async def pull_changes(
    *,
    caller_id: UUID,
    since: int,
    limit: int,
    memberships: MembershipRepository,
    entries: LogbookEntrySyncRepository,
    visits: ExtensionVisitSyncRepository,
) -> PullPage:
    """Pull merged offline changes for all orgs of the caller (D2).

    Scope: every org the caller is a member of, any role (viewer included).
    No membership -> empty page, never an error.
    """
    user_memberships = await memberships.list_for_user(caller_id)
    if not user_memberships:
        return PullPage(changes=[], next_since=since, has_more=False)

    org_ids = [m.org_id for m in user_memberships]
    entry_rows = await entries.list_for_pull(org_ids, since=since, limit=limit)
    visit_rows = await visits.list_for_pull(org_ids, since=since, limit=limit)

    candidates: list[PullChangeItem] = []
    for e in entry_rows:
        op = SyncOp.DELETE if e.deleted_at is not None else SyncOp.UPSERT
        candidates.append(
            PullChangeItem(
                id=e.id,
                entity=SyncEntity.LOGBOOK_ENTRY,
                op=op,
                server_version=e.server_version,
                data=e,
            )
        )
    for v in visit_rows:
        op = SyncOp.DELETE if v.deleted_at is not None else SyncOp.UPSERT
        candidates.append(
            PullChangeItem(
                id=v.id,
                entity=SyncEntity.EXTENSION_VISIT,
                op=op,
                server_version=v.server_version,
                data=v,
            )
        )

    candidates.sort(key=lambda c: c.server_version)
    has_more = len(candidates) > limit
    page = candidates[:limit]
    next_since = page[-1].server_version if page else since
    return PullPage(changes=page, next_since=next_since, has_more=has_more)
