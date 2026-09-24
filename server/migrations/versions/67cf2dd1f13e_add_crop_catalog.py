"""add crop catalog

Revision ID: 67cf2dd1f13e
Revises: 6628f7c0aa3b
Create Date: 2026-09-23 21:13:57.717965

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '67cf2dd1f13e'
down_revision: Union[str, Sequence[str], None] = '6628f7c0aa3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


crop_table = sa.table(
    "crop",
    sa.column("id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("name_es", sa.String),
    sa.column("kc_source", sa.String),
)

crop_stage_table = sa.table(
    "crop_stage",
    sa.column("crop_id", sa.Integer),
    sa.column("stage", sa.String),
    sa.column("length_days", sa.Integer),
    sa.column("kc", sa.Numeric),
    sa.column("depletion_fraction_p", sa.Numeric),
)

_STAGES = ("initial", "development", "mid", "late")

# v1 seed: `crops_requirements.csv` (docs/03-modelo-datos.md:489). Kc is
# completed from FAO-56 (Allen, Pereira, Raes & Smith, 1998, Irrigation and
# Drainage Paper 56, fao.org) Table 12 (Kc initial/mid/end per crop category)
# and Table 22 (p, the no-stress depletion fraction at ETc=5 mm/day; the app
# corrects it per plot/day per docs/03-modelo-datos.md:440). `kc_source`:
# - fao56: the crop is (or is the direct variety of) a FAO-56 Table 12 entry.
# - approximate: mapped to the closest FAO-56 Table 12 entry, a different
#   species/variant with a similar canopy and water-use profile (cited per
#   crop below).
# - none: no reasonable FAO-56 match (docs/03-modelo-datos.md:446 names this
#   exact case for Ñame); no crop_stage rows, blocks the irrigation depth
#   recommendation until an agronomist validates a local Kc.
#
# Table 12 tabulates only Kc initial/mid/end; `development` is not a table
# value; FAO-56 ramps it linearly from Kc initial to Kc mid, so the value
# used here is that ramp's midpoint, `(kc_initial + kc_mid) / 2`.
# `length_days` is not a literal FAO-56 Table 11 row either (Table 11's rows
# are region/planting-date examples, not universal constants, and don't
# match this Caribbean dataset's varieties). It's a 20/30/35/15%
# initial/development/mid/late split of each crop's `ciclo_dias` from the
# v1 CSV, with `mid` absorbing the rounding remainder so the four stages
# always sum to that documented total cycle.
#
# Each row: (id, code, name_es, kc_source, (kc_initial, kc_development,
# kc_mid, kc_end, p), (days_initial, days_development, days_mid, days_late)).
_CROPS: list[tuple[int, str, str, str, tuple[float, float, float, float, float] | None, tuple[int, int, int, int] | None]] = [
    (1, "maize", "Maíz", "fao56", (0.30, 0.75, 1.20, 0.35, 0.55), (18, 27, 31, 14)),
    # FAO-56 Table 12, Cereals: Maize (grain).
    (2, "cassava", "Yuca", "approximate", (0.30, 0.55, 0.80, 0.30, 0.35), (54, 80, 95, 41)),
    # FAO-56 Table 12, Roots and Tubers: Cassava, year 1 (closest entry; the
    # v1 dataset doesn't record plant age to choose year 1 vs. year 2 canopy).
    (3, "rice", "Arroz", "fao56", (1.05, 1.13, 1.20, 0.90, 0.20), (24, 36, 42, 18)),
    # FAO-56 Table 12: Rice (wetland, continued flooding through harvest).
    (4, "beans", "Frijol", "fao56", (0.40, 0.78, 1.15, 0.35, 0.45), (15, 23, 26, 11)),
    # FAO-56 Table 12, Legumes (Leguminosae): Beans, dry.
    (5, "yam", "Ñame", "none", None, None),
    # Not in FAO-56 Table 12 (docs/03-modelo-datos.md:446's own example).
    (6, "plantain", "Plátano", "approximate", (0.50, 0.80, 1.10, 1.00, 0.35), (73, 110, 127, 55)),
    # FAO-56 Table 12, Tropical Fruits and Trees: Banana, 1st year (closest
    # entry; plantain, Musa paradisiaca, isn't tabulated separately from
    # banana, Musa acuminata — same genus, similar canopy/water use).
    (7, "cacao", "Cacao", "fao56", (1.00, 1.03, 1.05, 1.05, 0.30), (36, 54, 63, 27)),
    # FAO-56 Table 12, Tropical Fruits and Trees: Cacao.
    (8, "cotton", "Algodón", "fao56", (0.35, 0.75, 1.15, 0.70, 0.65), (30, 45, 52, 23)),
    # FAO-56 Table 12, Fiber Crops: Cotton (leaves not removed before
    # harvest scenario).
    (9, "sorghum", "Sorgo", "fao56", (0.30, 0.68, 1.05, 0.55, 0.55), (22, 33, 38, 17)),
    # FAO-56 Table 12, Cereals: Sorghum (grain).
    (10, "oil_palm", "Palma Aceitera", "approximate", (0.95, 0.98, 1.00, 1.00, 0.65), (73, 110, 127, 55)),
    # FAO-56 Table 12, Tropical Fruits and Trees: Palm Trees (generic
    # evergreen palm canopy; oil palm, Elaeis guineensis, isn't tabulated
    # separately).
    (11, "mango", "Mango", "approximate", (0.60, 0.73, 0.85, 0.75, 0.70), (73, 110, 127, 55)),
    # FAO-56 Table 12, Fruit Trees: Avocado (closest evergreen
    # sub/tropical fruit-tree analog; mango isn't tabulated).
    (12, "chili_pepper", "Ají", "fao56", (0.60, 0.83, 1.05, 0.90, 0.30), (24, 36, 42, 18)),
    # FAO-56 Table 12, Vegetables – Solanum Family: Peppers, bell.
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "crop",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name_es", sa.String(), nullable=False),
        sa.Column("kc_source", sa.String(), nullable=False),
        sa.UniqueConstraint("code", name="uq_crop_code"),
        sa.CheckConstraint(
            "kc_source in ('fao56','local','approximate','none')", name="ck_crop_kc_source"
        ),
    )
    op.create_table(
        "crop_stage",
        sa.Column("crop_id", sa.Integer(), sa.ForeignKey("crop.id"), primary_key=True),
        sa.Column("stage", sa.String(), primary_key=True),
        sa.Column("length_days", sa.Integer(), nullable=False),
        sa.Column("kc", sa.Numeric(), nullable=False),
        sa.Column("depletion_fraction_p", sa.Numeric(), nullable=False),
        sa.CheckConstraint(
            "stage in ('initial','development','mid','late')", name="ck_crop_stage_name"
        ),
        sa.CheckConstraint("length_days > 0", name="ck_crop_stage_length_positive"),
        sa.CheckConstraint("kc > 0", name="ck_crop_stage_kc_positive"),
        sa.CheckConstraint(
            "depletion_fraction_p > 0 and depletion_fraction_p < 1",
            name="ck_crop_stage_depletion_fraction_range",
        ),
    )

    op.bulk_insert(
        crop_table,
        [
            {"id": crop_id, "code": code, "name_es": name_es, "kc_source": kc_source}
            for crop_id, code, name_es, kc_source, _kc, _days in _CROPS
        ],
    )

    stage_rows = []
    for crop_id, _code, _name_es, kc_source, kc, days in _CROPS:
        if kc_source == "none":
            continue
        assert kc is not None and days is not None
        kc_by_stage = dict(zip(_STAGES, (kc[0], kc[1], kc[2], kc[3])))
        days_by_stage = dict(zip(_STAGES, days))
        p = kc[4]
        for stage in _STAGES:
            stage_rows.append(
                {
                    "crop_id": crop_id,
                    "stage": stage,
                    "length_days": days_by_stage[stage],
                    "kc": kc_by_stage[stage],
                    "depletion_fraction_p": p,
                }
            )
    op.bulk_insert(crop_stage_table, stage_rows)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("crop_stage")
    op.drop_table("crop")
