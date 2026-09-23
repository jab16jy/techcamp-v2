"""SQLAlchemy table mappings for identity (docs/03-modelo-datos.md).

Invariants (role, kind) are enforced with CHECK constraints, not only in
application code, per docs/03's modeling rules.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class OrganizationRow(Base):
    __tablename__ = "organization"
    __table_args__ = (
        CheckConstraint(
            "kind in ('cooperative','individual','institution')",
            name="ck_organization_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)


class AppUserRow(Base):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    """The auth provider's `sub` claim (ADR-0014); our local issuer in seminar."""
    phone: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MembershipRow(Base):
    __tablename__ = "membership"
    __table_args__ = (
        CheckConstraint(
            "role in ('owner','technician','producer','viewer')",
            name="ck_membership_role",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
