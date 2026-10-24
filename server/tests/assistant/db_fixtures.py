"""Real rows for the assistant's database tests (E12 T5).

`conversation.user_id` and `plot_id` are foreign keys
(docs/03-modelo-datos.md §`kb_document`, `kb_chunk`, `conversation` y `message`:
el asistente (E12)), so a repository test that wants a conversation needs the
`app_user`, `organization`, `membership`, `farm` and `plot` rows behind it. The
rows are seeded the way the other modules' API tests seed them (`tests/risk/
test_api.py`, `tests/telemetry/test_stream_api.py`) rather than mocked: the
isolation these tests pin is `docs/09-cuellos-de-botella.md` §Seguridad, and it
is a property of the schema plus the repositories together.

Shared by `test_repositories.py` and the T5 API tests, because the two assert
the same thing at different levels and a second copy of these rows would be a
second place where a test's fixture could differ from the endpoint's.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.shared.ids import uuid7

POINT = "SRID=4326;POINT(-74.1 10.9)"
BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


@dataclass(frozen=True, slots=True)
class SeededDomain:
    """One organization with a producer, and a second user who belongs to nobody.

    `outsider` is the user an isolation test needs: a real `app_user` row (so a
    `404` is about authorization and never about a missing account) who is not a
    member of `org_id`, and `other_org_id` is an organization of their own so the
    cross-organization case has a plot on the far side of the boundary too.
    """

    org_id: UUID
    user_id: UUID
    token: str
    farm_id: UUID
    plot_id: UUID
    outsider_id: UUID
    outsider_token: str
    other_org_id: UUID
    other_plot_id: UUID


async def seed_domain(session: AsyncSession, *, with_plot: bool = True) -> SeededDomain:
    """A producer with one plot, and an outsider with one plot of their own."""
    org_id = uuid7()
    user_id = uuid7()
    outsider_id = uuid7()
    other_org_id = uuid7()

    session.add(OrganizationRow(id=org_id, name="Finca El Arroyo", kind="individual"))
    session.add(OrganizationRow(id=other_org_id, name="Otra finca", kind="individual"))
    session.add(AppUserRow(id=user_id, phone="+573001234567", full_name="Productor del arroyo"))
    session.add(AppUserRow(id=outsider_id, phone="+573009876543", full_name="Productor de otra"))
    await session.commit()

    session.add(MembershipRow(org_id=org_id, user_id=user_id, role="owner"))
    session.add(MembershipRow(org_id=other_org_id, user_id=outsider_id, role="owner"))
    await session.commit()

    farm_id = uuid7()
    session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=POINT)
    )
    await session.commit()
    plot_id = uuid7()
    session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=BOUNDARY,
            irrigation_system="none",
            weather_cell_id=None,
        )
    )

    other_farm_id = uuid7()
    session.add(
        FarmRow(
            id=other_farm_id,
            org_id=other_org_id,
            name="Finca ajena",
            municipality_code="47002",
            location=POINT,
        )
    )
    await session.commit()
    other_plot_id = uuid7()
    session.add(
        PlotRow(
            id=other_plot_id,
            org_id=other_org_id,
            farm_id=other_farm_id,
            name="Lote ajeno",
            boundary=BOUNDARY,
            irrigation_system="none",
            weather_cell_id=None,
        )
    )
    await session.commit()

    return SeededDomain(
        org_id=org_id,
        user_id=user_id,
        token=issue_token(str(user_id)),
        farm_id=farm_id,
        plot_id=plot_id if with_plot else uuid7(),
        outsider_id=outsider_id,
        outsider_token=issue_token(str(outsider_id)),
        other_org_id=other_org_id,
        other_plot_id=other_plot_id,
    )
