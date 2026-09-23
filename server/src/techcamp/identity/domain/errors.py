"""Domain-level identity failures. Pure, no I/O."""

from __future__ import annotations

from uuid import UUID


class UserNotFoundError(Exception):
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"User {user_id} not found")


class NotAMemberError(Exception):
    """Raised when a user has no membership in the given organization.

    Callers map this to a 404 (not 403), per docs/04-api.md: a resource in
    another organization must not reveal that it exists.
    """

    def __init__(self, user_id: UUID, org_id: UUID) -> None:
        self.user_id = user_id
        self.org_id = org_id
        super().__init__(f"User {user_id} is not a member of organization {org_id}")
