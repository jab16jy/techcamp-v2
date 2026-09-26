"""Ingest pipeline orchestration (docs/06-diseno-detallado.md §1, T4):
validate → map `channel_key` → calibrate → quality → batch insert → node
status → `NOTIFY plot_events`. Pure orchestration over ports, no aiomqtt or
SQL here (ADR-0002) — `telemetry/adapters/ingestor.py` owns the MQTT loop,
batching timer and JSON/text decoding into the `Raw*` messages below.

Only claimed nodes are ingested (task instruction): a `node_id` from an
unknown or unclaimed node is discarded and counted, never raised.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from techcamp.farms.application.ports import PlotRepository
from techcamp.telemetry.application.ports import (
    CalibrationRepository,
    NodeRepository,
    PlotEventsPort,
    ReadingRepository,
    SensorRepository,
)
from techcamp.telemetry.domain.errors import (
    MalformedUplinkPayloadError,
    UnsupportedUplinkVersionError,
)
from techcamp.telemetry.domain.models import (
    NodeSeenUpdate,
    NodeStatus,
    NodeStatusEvent,
    ReadingEvent,
    ReadingQuality,
    ReadingRecord,
    Sensor,
    apply_calibration,
    classify_reading_range,
    is_reading_too_old,
    parse_uplink,
    resolve_reading_time,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RawUplink:
    """One `tc/v1/{node_id}/up` message, already decoded off its MQTT topic
    (docs/04-api.md:192-224); `payload` is still raw JSON bytes so malformed
    JSON is this module's concern, not the adapter's."""

    node_id: UUID
    payload: bytes
    received_at: datetime


@dataclass(frozen=True, slots=True)
class RawStatusMessage:
    """One `tc/v1/{node_id}/status` message: plain `online`/`offline` text,
    retained, doubling as the Last Will (docs/04-api.md:192-224)."""

    node_id: UUID
    payload: bytes
    received_at: datetime


@dataclass
class IngestStats:
    """Flush outcome, for the ingestor's own logging (docs/06 §1 mentions
    `ingest_rejected_total{reason}` and `ingest_dead_letter`; a metrics/dead-
    letter sink is not part of this task's scope, only the counts)."""

    inserted: int = 0
    counts: Counter[str] = field(default_factory=Counter)


async def _flush_node_updates(
    seen: dict[UUID, NodeSeenUpdate],
    reading_events: list[ReadingEvent],
    *,
    nodes: NodeRepository,
    plots: PlotRepository,
    events: PlotEventsPort,
    farm_cache: dict[UUID, UUID],
) -> None:
    """Shared tail of both ingest functions: mark nodes seen and publish
    events in one pass, sharing the plot→farm lookup cache."""
    if not seen and not reading_events:
        return
    updated: set[UUID] = set()
    if seen:
        updated = await nodes.mark_seen_batch(list(seen.values()))
    status_events: list[NodeStatusEvent] = []
    for update in seen.values():
        if update.node_id not in updated:
            # A newer `last_seen_at` was already stored (a Last Will that
            # arrived after this message), so this status is stale: publishing
            # it would move the node's state backwards for every SSE client.
            continue
        farm_id = farm_cache.get(update.plot_id)
        if farm_id is None:
            plot = await plots.get_for_orgs(update.plot_id, [update.org_id])
            if plot is None:
                # The node's own plot vanished from under it; nothing sane to
                # notify with (no farm_id), but the node is still marked seen.
                continue
            farm_id = plot.farm_id
            farm_cache[update.plot_id] = farm_id
        status_events.append(
            NodeStatusEvent(
                org_id=update.org_id,
                farm_id=farm_id,
                node_id=update.node_id,
                status=update.status,
                at=update.last_seen_at,
            )
        )
    await events.publish(readings=reading_events, statuses=status_events)


async def ingest_uplinks(
    messages: Sequence[RawUplink],
    *,
    nodes: NodeRepository,
    sensors: SensorRepository,
    calibrations: CalibrationRepository,
    readings: ReadingRepository,
    plots: PlotRepository,
    events: PlotEventsPort,
) -> IngestStats:
    """One ingest flush (docs/06-diseno-detallado.md §1). `messages` is
    whatever `adapters/ingestor.py`'s batcher accumulated (500 msgs or 1s)."""
    stats = IngestStats()
    records: list[ReadingRecord] = []
    reading_events: list[tuple[tuple[int, datetime], ReadingEvent]] = []
    seen: dict[UUID, NodeSeenUpdate] = {}
    sensor_cache: dict[UUID, dict[str, Sensor]] = {}
    farm_cache: dict[UUID, UUID] = {}

    for msg in messages:
        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            stats.counts["malformed_json"] += 1
            logger.warning("ingest: malformed JSON from node %s", msg.node_id)
            continue

        try:
            uplink = parse_uplink(payload)
        except UnsupportedUplinkVersionError:
            stats.counts["unsupported_version"] += 1
            logger.warning("ingest: unsupported uplink version from node %s", msg.node_id)
            continue
        except MalformedUplinkPayloadError as exc:
            stats.counts["malformed_payload"] += 1
            logger.warning("ingest: malformed payload from node %s: %s", msg.node_id, exc)
            continue

        if uplink.ts is not None and is_reading_too_old(uplink.ts, msg.received_at):
            stats.counts["too_old"] += 1
            continue

        node = await nodes.get_by_id(msg.node_id)
        if node is None or node.org_id is None or node.plot_id is None:
            stats.counts["unclaimed_or_unknown_node"] += 1
            logger.info("ingest: unknown or unclaimed node %s", msg.node_id)
            continue

        farm_id = farm_cache.get(node.plot_id)
        if farm_id is None:
            plot = await plots.get_for_orgs(node.plot_id, [node.org_id])
            if plot is None:
                stats.counts["unknown_plot"] += 1
                continue
            farm_id = plot.farm_id
            farm_cache[node.plot_id] = farm_id

        node_sensors = sensor_cache.get(node.id)
        if node_sensors is None:
            node_sensors = {
                s.channel_key: s for s in await sensors.list_for_node(node.id, node.org_id)
            }
            sensor_cache[node.id] = node_sensors

        reading_time, ts_quality = resolve_reading_time(uplink.ts, msg.received_at)

        for channel_key, raw in uplink.channels.items():
            sensor = node_sensors.get(channel_key)
            if sensor is None:
                stats.counts["unknown_channel"] += 1
                logger.info("ingest: unknown channel %s for node %s", channel_key, node.id)
                continue

            calibration = await calibrations.get_latest_valid_at(
                sensor.id, node.org_id, reading_time
            )
            if calibration is None:
                # T4 decision (docs are silent on this case, flagged gap):
                # store the raw value uncalibrated rather than dropping it.
                value = None
                stats.counts["uncalibrated"] += 1
            else:
                value = apply_calibration(calibration, raw)

            quality = ts_quality
            if value is not None:
                range_quality = classify_reading_range(sensor.unit, value)
                # T4 decision (docs are silent on precedence when both apply,
                # flagged gap): out-of-range is the stronger signal — this
                # falls out of `ReadingQuality`'s own ordering (2 > 1).
                quality = ReadingQuality(max(int(ts_quality), int(range_quality)))

            records.append(
                ReadingRecord(
                    sensor_id=sensor.id,
                    time=reading_time,
                    raw_value=raw,
                    value=value,
                    received_at=msg.received_at,
                    quality=quality,
                )
            )
            if value is not None:
                reading_events.append(
                    (
                        (sensor.id, reading_time),
                        ReadingEvent(
                            org_id=node.org_id,
                            farm_id=farm_id,
                            plot_id=node.plot_id,
                            metric=sensor.metric,
                            value=value,
                            at=reading_time,
                        ),
                    )
                )

        # Hot alert-rule evaluation: no-op until E7 (feature doc decision).

        seen[node.id] = NodeSeenUpdate(
            node_id=node.id,
            org_id=node.org_id,
            plot_id=node.plot_id,
            last_seen_at=msg.received_at,
            status=NodeStatus.ONLINE,
        )

    inserted = await readings.insert_batch(records)
    stats.inserted = len(inserted)
    # `ON CONFLICT DO NOTHING` skips a redelivered reading, and a QoS-1
    # duplicate must not send a second `reading` event to the SSE fan-out
    # (GitHub #36): only rows that actually landed are published.
    new_events = [event for key, event in reading_events if key in inserted]

    await _flush_node_updates(
        seen, new_events, nodes=nodes, plots=plots, events=events, farm_cache=farm_cache
    )
    return stats


async def ingest_status_messages(
    messages: Sequence[RawStatusMessage],
    *,
    nodes: NodeRepository,
    plots: PlotRepository,
    events: PlotEventsPort,
) -> IngestStats:
    """`tc/v1/{node_id}/status` (docs/04-api.md:192-224): retained
    online/offline, the second half doubling as the node's Last Will."""
    stats = IngestStats()
    seen: dict[UUID, NodeSeenUpdate] = {}
    farm_cache: dict[UUID, UUID] = {}

    for msg in messages:
        text = msg.payload.decode("utf-8", errors="replace").strip().lower()
        if text not in ("online", "offline"):
            stats.counts["malformed_status"] += 1
            logger.warning("ingest: malformed status %r from node %s", text, msg.node_id)
            continue

        node = await nodes.get_by_id(msg.node_id)
        if node is None or node.org_id is None or node.plot_id is None:
            stats.counts["unclaimed_or_unknown_node"] += 1
            continue

        seen[node.id] = NodeSeenUpdate(
            node_id=node.id,
            org_id=node.org_id,
            plot_id=node.plot_id,
            last_seen_at=msg.received_at,
            status=NodeStatus.ONLINE if text == "online" else NodeStatus.OFFLINE,
        )

    await _flush_node_updates(
        seen, [], nodes=nodes, plots=plots, events=events, farm_cache=farm_cache
    )
    return stats
