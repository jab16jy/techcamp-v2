"""add telemetry schema

Revision ID: 8c3983dc2dfd
Revises: ff21853418b8
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8c3983dc2dfd'
down_revision: Union[str, Sequence[str], None] = 'ff21853418b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ADR-0003: the schema must not depend on the compose init script (CI's
    # Postgres service has none, same lesson as postgis in 9098dc0927a3).
    # The image (timescale/timescaledb-ha:pg16) ships the extension.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        'node',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=True),
        sa.Column('plot_id', sa.Uuid(), nullable=True),
        sa.Column('transport', sa.String(), nullable=False),
        sa.Column('dev_eui', sa.String(), nullable=True),
        sa.Column('claim_code', sa.String(), nullable=False),
        sa.Column('credential_hash', sa.String(), nullable=False),
        sa.Column('firmware', sa.String(), nullable=True),
        sa.Column('interval_s', sa.Integer(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.CheckConstraint(
            "transport in ('wifi','cellular','lorawan')", name='ck_node_transport'
        ),
        sa.CheckConstraint(
            "status in ('provisioned','online','offline','retired')", name='ck_node_status'
        ),
        sa.CheckConstraint(
            "(org_id IS NULL) = (plot_id IS NULL) AND (plot_id IS NULL) = (claimed_at IS NULL)",
            name='ck_node_ownership_all_or_nothing',
        ),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id']),
        sa.ForeignKeyConstraint(['plot_id'], ['plot.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dev_eui', name='uq_node_dev_eui'),
        sa.UniqueConstraint('claim_code', name='uq_node_claim_code'),
    )
    op.create_index('ix_node_org_id', 'node', ['org_id'])
    op.create_index('ix_node_plot_id', 'node', ['plot_id'])

    op.create_table(
        'sensor',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('node_id', sa.Uuid(), nullable=False),
        sa.Column('channel_key', sa.String(), nullable=False),
        sa.Column('metric', sa.String(), nullable=False),
        sa.Column('depth_cm', sa.Integer(), nullable=True),
        sa.Column('unit', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['node_id'], ['node.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('node_id', 'channel_key', name='uq_sensor_node_channel'),
    )

    op.create_table(
        'calibration',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('sensor_id', sa.BigInteger(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('method', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('rmse_pct', sa.Numeric(), nullable=True),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "method in ('linear','two_point','polynomial')", name='ck_calibration_method'
        ),
        sa.CheckConstraint("kind in ('lab','field')", name='ck_calibration_kind'),
        sa.ForeignKeyConstraint(['sensor_id'], ['sensor.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sensor_id', 'version', name='uq_calibration_sensor_version'),
    )
    op.create_index(
        'ix_calibration_sensor_valid_from', 'calibration', ['sensor_id', 'valid_from']
    )

    op.create_table(
        'reading',
        sa.Column('time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sensor_id', sa.BigInteger(), nullable=False),
        sa.Column('raw_value', sa.Float(), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('quality', sa.SmallInteger(), nullable=False),
        sa.CheckConstraint('quality in (0,1,2)', name='ck_reading_quality'),
        sa.ForeignKeyConstraint(['sensor_id'], ['sensor.id']),
        sa.PrimaryKeyConstraint('sensor_id', 'time', name='pk_reading'),
    )
    op.create_index(
        'ix_reading_sensor_time', 'reading', ['sensor_id', sa.text('time DESC')]
    )

    # TimescaleDB: `CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous)`
    # and `add_continuous_aggregate_policy` cannot run inside a transaction
    # block (verified via ctx7 against timescale/timescaledb's own docs,
    # which always run these statements standalone). `create_hypertable` and
    # the compression setup don't strictly need it, but the whole block runs
    # in Alembic's `autocommit_block()` for one simple, uniform escape hatch
    # — the same one Alembic's own docs use for `ALTER TYPE ... ADD VALUE`.
    # GitHub issue #33: `autocommit_block()` escapes the migration's
    # transaction and commits `node`/`sensor`/`calibration`/`reading` before
    # the Timescale statements below run; a failure here leaves those tables
    # committed without the alembic stamp, so a retried upgrade re-runs
    # `op.create_table` above and fails on "already exists" — recover by
    # dropping those four tables (the seminar profile's local Postgres is
    # disposable) before retrying. The statements below are made idempotent
    # so the retry itself, once past `create_table`, is safe to repeat.
    with op.get_context().autocommit_block():
        op.execute(
            "SELECT create_hypertable('reading', 'time', "
            "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE)"
        )
        op.execute(
            "ALTER TABLE reading SET ("
            "timescaledb.compress, "
            "timescaledb.compress_segmentby = 'sensor_id', "
            "timescaledb.compress_orderby = 'time DESC'"
            ")"
        )
        op.execute(
            "SELECT add_compression_policy('reading', INTERVAL '7 days', if_not_exists => true)"
        )

        op.execute(
            """
            CREATE MATERIALIZED VIEW IF NOT EXISTS reading_hourly
            WITH (timescaledb.continuous) AS
            SELECT
                sensor_id,
                time_bucket(INTERVAL '1 hour', time) AS bucket,
                min(value) AS min_value,
                max(value) AS max_value,
                avg(value) AS avg_value,
                count(value) AS reading_count
            FROM reading
            GROUP BY sensor_id, bucket
            WITH NO DATA
            """
        )
        op.execute(
            "SELECT add_continuous_aggregate_policy('reading_hourly', "
            "start_offset => INTERVAL '3 hours', "
            "end_offset => INTERVAL '1 hour', "
            "schedule_interval => INTERVAL '1 hour', if_not_exists => true)"
        )

        op.execute(
            """
            CREATE MATERIALIZED VIEW IF NOT EXISTS reading_daily
            WITH (timescaledb.continuous) AS
            SELECT
                sensor_id,
                time_bucket(INTERVAL '1 day', time) AS bucket,
                min(value) AS min_value,
                max(value) AS max_value,
                avg(value) AS avg_value,
                count(value) AS reading_count
            FROM reading
            GROUP BY sensor_id, bucket
            WITH NO DATA
            """
        )
        op.execute(
            "SELECT add_continuous_aggregate_policy('reading_daily', "
            "start_offset => INTERVAL '3 days', "
            "end_offset => INTERVAL '1 hour', "
            "schedule_interval => INTERVAL '1 hour', if_not_exists => true)"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute("DROP MATERIALIZED VIEW IF EXISTS reading_daily")
        op.execute("DROP MATERIALIZED VIEW IF EXISTS reading_hourly")

    op.drop_index('ix_reading_sensor_time', table_name='reading')
    op.drop_table('reading')
    op.drop_index('ix_calibration_sensor_valid_from', table_name='calibration')
    op.drop_table('calibration')
    op.drop_table('sensor')
    op.drop_index('ix_node_plot_id', table_name='node')
    op.drop_index('ix_node_org_id', table_name='node')
    op.drop_table('node')
