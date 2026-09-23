"""Repository ports the application layer depends on (never on adapters).

Required by ADR-0002's layering (`application` may only import `domain`) since
repositories are external I/O (Postgres). Concrete adapters satisfy these
structurally; fakes in tests satisfy the "test double" leg of the port rule.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from techcamp.identity.domain.models import AppUser, Membership


class UserRepository(Protocol):
    async def get_by_id(self, user_id: UUID) -> AppUser | None: ...

    async def get_by_phone(self, phone: str) -> AppUser | None: ...


class MembershipRepository(Protocol):
    async def list_for_user(self, user_id: UUID) -> list[Membership]: ...

    async def list_for_org(self, org_id: UUID) -> list[Membership]: ...

    async def get(self, user_id: UUID, org_id: UUID) -> Membership | None: ...
