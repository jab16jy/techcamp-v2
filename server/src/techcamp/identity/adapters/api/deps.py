"""FastAPI dependencies wiring identity adapters into requests."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header

from techcamp.identity.adapters.repositories import (
    SqlAlchemyMembershipRepository,
    SqlAlchemyUserRepository,
)
from techcamp.identity.adapters.security.token_issuer import InvalidTokenError, decode_token
from techcamp.shared.db import SessionDep
from techcamp.shared.errors import ProblemError


async def get_user_repository(session: SessionDep) -> SqlAlchemyUserRepository:
    return SqlAlchemyUserRepository(session)


async def get_membership_repository(session: SessionDep) -> SqlAlchemyMembershipRepository:
    return SqlAlchemyMembershipRepository(session)


UserRepoDep = Annotated[SqlAlchemyUserRepository, Depends(get_user_repository)]
MembershipRepoDep = Annotated[SqlAlchemyMembershipRepository, Depends(get_membership_repository)]


async def get_current_user_id(
    authorization: Annotated[str | None, Header()] = None,
) -> UUID:
    """The JWKS validation dependency: resolves `sub` from a valid bearer token."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise ProblemError(
            status=401,
            title="Missing bearer token",
            detail="Authorization header must be 'Bearer <token>'.",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        claims = decode_token(token)
    except InvalidTokenError as exc:
        raise ProblemError(status=401, title="Invalid token", detail=str(exc)) from exc
    return UUID(claims["sub"])


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
