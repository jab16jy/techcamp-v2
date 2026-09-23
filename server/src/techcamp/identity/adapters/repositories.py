"""Postgres repositories for identity (structurally satisfy application/ports.py)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.identity.adapters.orm import AppUserRow, MembershipRow
from techcamp.identity.domain.models import AppUser, Membership, Role


def _user_from_row(row: AppUserRow) -> AppUser:
    return AppUser(
        id=row.id,
        phone=row.phone,
        email=row.email,
        full_name=row.full_name,
        consent_at=row.consent_at,
    )


def _membership_from_row(row: MembershipRow) -> Membership:
    return Membership(org_id=row.org_id, user_id=row.user_id, role=Role(row.role))


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> AppUser | None:
        row = await self._session.get(AppUserRow, user_id)
        return _user_from_row(row) if row is not None else None

    async def get_by_phone(self, phone: str) -> AppUser | None:
        result = await self._session.execute(select(AppUserRow).where(AppUserRow.phone == phone))
        row = result.scalar_one_or_none()
        return _user_from_row(row) if row is not None else None


class SqlAlchemyMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        result = await self._session.execute(
            select(MembershipRow).where(MembershipRow.user_id == user_id)
        )
        return [_membership_from_row(row) for row in result.scalars()]

    async def list_for_org(self, org_id: UUID) -> list[Membership]:
        result = await self._session.execute(
            select(MembershipRow).where(MembershipRow.org_id == org_id)
        )
        return [_membership_from_row(row) for row in result.scalars()]

    async def get(self, user_id: UUID, org_id: UUID) -> Membership | None:
        row = await self._session.get(MembershipRow, {"org_id": org_id, "user_id": user_id})
        return _membership_from_row(row) if row is not None else None
