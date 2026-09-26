"""Shared org/plot setup for the simulator tests.

#62 R2-private-test-helper-coupling: `test_cli_login.py` and `test_cli_run.py`
used to import `_member`/`_make_plot` out of `test_node_client.py`, so renaming
a private helper in one test module broke an unrelated one. They live here
instead, as the module they are: a shared setup, not a private of a test.

Not named `test_*`, so pytest does not collect it as a test module.
"""

from __future__ import annotations

from itertools import count
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.shared.ids import uuid7

_POINT = "POINT(-74.1 10.9)"
_BOUNDARY = "POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
_phone_seq = count()


async def member(db_session: AsyncSession) -> tuple[UUID, str]:
    """An organization with one owner member, and that member's phone (the
    dev OTP login identity, docs/04-api.md:173)."""
    org_id, user_id = uuid7(), uuid7()
    phone = f"+5730099{next(_phone_seq):05d}"
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=phone))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role="owner"))
    await db_session.commit()
    return org_id, phone


async def make_plot(db_session: AsyncSession, org_id: UUID) -> UUID:
    """A farm and one plot in `org_id` — a node can only be claimed onto a
    plot the caller can write to (docs/06-diseno-detallado.md §2)."""
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="none",
        )
    )
    await db_session.commit()
    return plot_id
