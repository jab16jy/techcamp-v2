"""Identity entities (docs/03-modelo-datos.md). Pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    OWNER = "owner"
    TECHNICIAN = "technician"
    PRODUCER = "producer"
    VIEWER = "viewer"


@dataclass(frozen=True, slots=True)
class Organization:
    id: UUID
    name: str
    kind: str


@dataclass(frozen=True, slots=True)
class AppUser:
    id: UUID
    phone: str
    email: str | None = None
    full_name: str | None = None
    consent_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Membership:
    org_id: UUID
    user_id: UUID
    role: Role
