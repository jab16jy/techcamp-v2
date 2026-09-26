from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.domain.errors import CalibrationVersionConflictError
from techcamp.telemetry.domain.models import (
    CalibrationKind,
    CalibrationMethod,
    NodeStatus,
    NodeTransport,
)

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


async def _make_org_and_plot(
    db_session: AsyncSession, org_name: str = "Finca"
) -> tuple[object, object]:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name=org_name, kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name=org_name, municipality_code="47001", location=_POINT
        )
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
    return org_id, plot_id


async def _make_node(
    db_session: AsyncSession, org_id: object, plot_id: object, *, claim_code: str = "CODE1"
) -> object:
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            dev_eui=None,
            claim_code=claim_code,
            credential_hash="hash",
            firmware=None,
            interval_s=300,
            claimed_at=datetime(2026, 1, 1, tzinfo=UTC),
            last_seen_at=None,
            status="provisioned",
        )
    )
    await db_session.commit()
    return node_id


async def _make_sensor(
    db_session: AsyncSession, node_id: object, *, channel_key: str = "sm_10"
) -> int:
    sensor = SensorRow(
        node_id=node_id, channel_key=channel_key, metric="soil_moisture", depth_cm=10, unit="pct"
    )
    db_session.add(sensor)
    await db_session.commit()
    await db_session.refresh(sensor)
    return sensor.id


async def test_node_repository_round_trips_by_id_and_org(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)

    node = await SqlAlchemyNodeRepository(db_session).get(node_id, org_id)

    assert node is not None
    assert node.id == node_id
    assert node.plot_id == plot_id
    assert node.transport is NodeTransport.WIFI
    assert node.status is NodeStatus.PROVISIONED
    assert node.claimed_at is not None


async def test_node_repository_hides_nodes_of_other_orgs(db_session: AsyncSession) -> None:
    org_a, plot_a = await _make_org_and_plot(db_session, "Finca A")
    org_b, _plot_b = await _make_org_and_plot(db_session, "Finca B")
    node_id = await _make_node(db_session, org_a, plot_a)

    repo = SqlAlchemyNodeRepository(db_session)
    assert await repo.get(node_id, org_b) is None
    assert await repo.get(node_id, org_a) is not None


async def test_get_by_claim_code_finds_the_node_regardless_of_org(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id, claim_code="ABC123")

    node = await SqlAlchemyNodeRepository(db_session).get_by_claim_code("ABC123")

    assert node is not None
    assert node.id == node_id
    assert await SqlAlchemyNodeRepository(db_session).get_by_claim_code("NOPE") is None


async def test_get_by_claim_code_finds_an_unclaimed_node(db_session: AsyncSession) -> None:
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=None,
            plot_id=None,
            transport="wifi",
            dev_eui=None,
            claim_code="UNCLAIMED2",
            credential_hash="hash",
            firmware=None,
            interval_s=300,
            claimed_at=None,
            last_seen_at=None,
            status="provisioned",
        )
    )
    await db_session.commit()

    node = await SqlAlchemyNodeRepository(db_session).get_by_claim_code("UNCLAIMED2")

    assert node is not None
    assert node.id == node_id
    assert node.org_id is None
    assert node.plot_id is None


async def test_list_for_org_does_not_return_unclaimed_nodes(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    claimed_node = await _make_node(db_session, org_id, plot_id, claim_code="CLAIMED1")
    db_session.add(
        NodeRow(
            id=uuid7(),
            org_id=None,
            plot_id=None,
            transport="wifi",
            dev_eui=None,
            claim_code="UNCLAIMED3",
            credential_hash="hash",
            firmware=None,
            interval_s=300,
            claimed_at=None,
            last_seen_at=None,
            status="provisioned",
        )
    )
    await db_session.commit()

    nodes = await SqlAlchemyNodeRepository(db_session).list_for_org(org_id)

    assert [n.id for n in nodes] == [claimed_node]


async def test_list_for_org_filters_by_plot_and_status(db_session: AsyncSession) -> None:
    org_id, plot_a = await _make_org_and_plot(db_session, "Finca A")
    _org_id2, plot_b = await _make_org_and_plot(db_session, "Finca B")
    node_a = await _make_node(db_session, org_id, plot_a, claim_code="A1")
    node_b = await _make_node(db_session, org_id, plot_b, claim_code="A2")

    repo = SqlAlchemyNodeRepository(db_session)

    assert {n.id for n in await repo.list_for_org(org_id)} == {node_a, node_b}
    assert [n.id for n in await repo.list_for_org(org_id, plot_id=plot_a)] == [node_a]
    assert {n.id for n in await repo.list_for_org(org_id, status=NodeStatus.PROVISIONED)} == {
        node_a,
        node_b,
    }
    assert await repo.list_for_org(org_id, status=NodeStatus.RETIRED) == []


async def test_sensor_repository_lists_sensors_for_a_node(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    await _make_sensor(db_session, node_id, channel_key="sm_10")
    await _make_sensor(db_session, node_id, channel_key="temp")

    sensors = await SqlAlchemySensorRepository(db_session).list_for_node(node_id, org_id)

    assert sorted(s.channel_key for s in sensors) == ["sm_10", "temp"]


async def test_sensor_repository_hides_sensors_of_other_orgs(db_session: AsyncSession) -> None:
    org_a, plot_a = await _make_org_and_plot(db_session, "Finca A")
    org_b, _plot_b = await _make_org_and_plot(db_session, "Finca B")
    node_id = await _make_node(db_session, org_a, plot_a)
    await _make_sensor(db_session, node_id)

    repo = SqlAlchemySensorRepository(db_session)
    assert await repo.list_for_node(node_id, org_b) == []
    assert len(await repo.list_for_node(node_id, org_a)) == 1


async def test_calibration_repository_add_version_and_get_latest_valid_at(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)

    created = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.TWO_POINT,
        kind=CalibrationKind.FIELD,
        params={"raw_dry": 2900, "raw_wet": 1300, "vwc_dry": 5, "vwc_wet": 45},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert created is not None
    assert created.sensor_id == sensor_id
    assert created.version == 1
    assert created.method is CalibrationMethod.TWO_POINT
    assert created.kind is CalibrationKind.FIELD
    assert created.params["raw_dry"] == 2900

    found = await repo.get_latest_valid_at(sensor_id, org_id, datetime(2026, 6, 1, tzinfo=UTC))
    assert found is not None
    assert found.id == created.id


async def test_get_latest_valid_at_picks_the_most_recent_version_not_after_the_given_time(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)
    v1 = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.LAB,
        params={"scale": 1.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    v2 = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=2,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.FIELD,
        params={"scale": 1.1, "offset": 0.2},
        rmse_pct=2.5,
        valid_from=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert v1 is not None
    assert v2 is not None

    before_v2 = await repo.get_latest_valid_at(sensor_id, org_id, datetime(2026, 3, 1, tzinfo=UTC))
    after_v2 = await repo.get_latest_valid_at(sensor_id, org_id, datetime(2026, 12, 1, tzinfo=UTC))

    assert before_v2 is not None and before_v2.id == v1.id
    assert after_v2 is not None and after_v2.id == v2.id


async def test_get_latest_valid_at_breaks_valid_from_ties_by_highest_version(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)
    same_valid_from = datetime(2026, 1, 1, tzinfo=UTC)
    await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.LAB,
        params={"scale": 1.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=same_valid_from,
    )
    v2 = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=2,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.FIELD,
        params={"scale": 1.1, "offset": 0.2},
        rmse_pct=2.5,
        valid_from=same_valid_from,
    )
    assert v2 is not None

    found = await repo.get_latest_valid_at(sensor_id, org_id, datetime(2026, 6, 1, tzinfo=UTC))

    assert found is not None
    assert found.id == v2.id


async def test_add_version_is_scoped_to_the_sensors_org(db_session: AsyncSession) -> None:
    org_a, plot_a = await _make_org_and_plot(db_session, "Finca A")
    org_b, _plot_b = await _make_org_and_plot(db_session, "Finca B")
    node_id = await _make_node(db_session, org_a, plot_a)
    sensor_id = await _make_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)

    result = await repo.add_version(
        org_id=org_b,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.LAB,
        params={"scale": 1.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result is None
    assert (
        await repo.get_latest_valid_at(sensor_id, org_a, datetime(2026, 6, 1, tzinfo=UTC)) is None
    )


async def test_claim_matches_zero_rows_for_an_already_claimed_node(
    db_session: AsyncSession,
) -> None:
    """The `org_id IS NULL` guard is the whole point of `claim`: a second claim
    of the same node must update nothing (docs/06-diseno-detallado.md §2,
    `claim_code` is single-use)."""
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id, claim_code="RACECLAIM")
    repo = SqlAlchemyNodeRepository(db_session)

    assert (
        await repo.claim(
            node_id,
            org_id=uuid7(),
            plot_id=plot_id,
            credential_hash="hashed",
            claimed_at=datetime(2026, 2, 1, tzinfo=UTC),
        )
        is None
    )

    # the rollback the lost race forces leaves the session usable and the node
    # untouched
    still = await repo.get(node_id, org_id)
    assert still is not None
    assert still.org_id == org_id


async def test_add_version_duplicate_sensor_and_version_raises_and_keeps_session_usable(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)
    await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.LAB,
        params={"scale": 1.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(CalibrationVersionConflictError):
        await repo.add_version(
            org_id=org_id,
            sensor_id=sensor_id,
            version=1,
            method=CalibrationMethod.LINEAR,
            kind=CalibrationKind.LAB,
            params={"scale": 2.0, "offset": 0.0},
            rmse_pct=None,
            valid_from=datetime(2026, 2, 1, tzinfo=UTC),
        )

    # the session stays usable after the repository rolls back
    found = await repo.get_latest_valid_at(sensor_id, org_id, datetime(2026, 6, 1, tzinfo=UTC))
    assert found is not None
    assert found.version == 1


async def test_get_latest_valid_at_hides_calibrations_of_other_orgs(
    db_session: AsyncSession,
) -> None:
    org_a, plot_a = await _make_org_and_plot(db_session, "Finca A")
    org_b, _plot_b = await _make_org_and_plot(db_session, "Finca B")
    node_id = await _make_node(db_session, org_a, plot_a)
    sensor_id = await _make_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)
    await repo.add_version(
        org_id=org_a,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.LAB,
        params={"scale": 1.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )

    found_in_own_org = await repo.get_latest_valid_at(
        sensor_id, org_a, datetime(2026, 6, 1, tzinfo=UTC)
    )
    found_in_other_org = await repo.get_latest_valid_at(
        sensor_id, org_b, datetime(2026, 6, 1, tzinfo=UTC)
    )

    assert found_in_own_org is not None
    assert found_in_other_org is None
