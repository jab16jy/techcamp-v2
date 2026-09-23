"""Async SQLAlchemy engine and session wiring, shared by every module's adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from techcamp.shared.config import database_url


class Base(DeclarativeBase):
    """Shared declarative base; every module's ORM rows register on this metadata."""


# ponytail: NullPool avoids asyncpg connections outliving the event loop that
# opened them — each test function gets its own loop under anyio. At this
# project's scale (docs/02-estimaciones.md: ~11 writes/s in year 3) skipping a
# pooled cache costs nothing measurable; revisit with pooling if load grows.
engine = create_async_engine(database_url(), poolclass=NullPool)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
