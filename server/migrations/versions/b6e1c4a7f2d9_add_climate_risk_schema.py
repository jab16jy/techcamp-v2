"""add climate risk tables

`model_version` registers every model and baseline the risk job can serve, with
the thresholds its severity rule reads; `risk_prediction` stores one cell's
risk per event, month and version (docs/03-modelo-datos.md §`municipality`,
`model_version` y `risk_prediction`: riesgo climático (E10)).

Neither table carries `org_id`: a prediction is a property of a `weather_cell`,
which is shared reference data — the same row for every organization
(docs/09-cuellos-de-botella.md:39). Org isolation is enforced at the plot
endpoint (T6b).

Revision ID: b6e1c4a7f2d9
Revises: 43c9c5cc68c9
Create Date: 2026-10-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b6e1c4a7f2d9"
down_revision: Union[str, Sequence[str], None] = "43c9c5cc68c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "model_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        # The baseline's own table or rule lives here too: a baseline is a
        # registered `model_version` with `is_baseline` (docs/08 §M2
        # "Línea base servida"), which is why every prediction has a
        # `model_version_id` even before any model passes the gate.
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("baseline_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_baseline", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("artifact_sha256", sa.String(), nullable=True),
        sa.Column("dataset_hash", sa.String(), nullable=True),
        sa.Column("git_commit", sa.String(), nullable=True),
        # `{"high": p, "critical": p | null}`: the severity is the version's,
        # never a constant of the server (docs/03 §Umbrales).
        sa.Column("thresholds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("promoted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("promotion_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # At most one promoted version per event: reverting is promoting the
    # previous one (docs/03 §Versión promovida).
    op.create_index(
        "uq_model_version_promoted_name",
        "model_version",
        ["name"],
        unique=True,
        postgresql_where=sa.text("promoted"),
    )
    op.create_table(
        "risk_prediction",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cell_id", sa.Integer(), sa.ForeignKey("weather_cell.id"), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), sa.ForeignKey("model_version.id"), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("horizon_start", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("probability", sa.REAL(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("top_factors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type in ('flood', 'drought')", name="ck_risk_prediction_event_type"
        ),
        sa.CheckConstraint(
            "severity in ('low', 'high', 'critical')", name="ck_risk_prediction_severity"
        ),
        sa.CheckConstraint(
            "probability >= 0 and probability <= 1", name="ck_risk_prediction_probability"
        ),
        # One prediction per cell, event, month and version: the daily job is
        # idempotent, and promoting another version adds its prediction without
        # deleting the served one (docs/03 §Unicidad de la predicción,
        # docs/06 §8).
        sa.UniqueConstraint(
            "cell_id",
            "event_type",
            "horizon_start",
            "model_version_id",
            name="uq_risk_prediction_cell_event_month_version",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("risk_prediction")
    op.drop_index("uq_model_version_promoted_name", table_name="model_version")
    op.drop_table("model_version")
