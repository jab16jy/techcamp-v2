"""SQLAlchemy table mappings for farms (docs/03-modelo-datos.md:85-135; ADR-0023).

Invariants are enforced with CHECK constraints, not only in application code,
per docs/03's modeling rules. Geometry columns use `Mapped[Any]`: GeoAlchemy2
accepts a WKT/EWKT `str` on write and returns a `WKBElement` on read, and the
domain layer only ever sees the WKT text a repository extracts with
`ST_AsText` (techcamp/farms/domain/models.py).
"""

from __future__ import annotations

import decimal
import uuid
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class FarmRow(Base):
    __tablename__ = "farm"
    __table_args__ = (
        Index("ix_farm_org_id", "org_id"),
        UniqueConstraint("id", "org_id", name="uq_farm_id_org_id"),
        # T1 review follow-up: a composite unique key lets `plot` carry a
        # composite FK to `(farm.id, farm.org_id)`, so a plot can never point
        # at a farm belonging to a different organization.
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    municipality_code: Mapped[str] = mapped_column(String, nullable=False)
    """DIVIPOLA code as plain text: docs/03 models it as a FK to a `municipality`
    table, but that table isn't migrated from v1 until a later epic."""
    location: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )
    technician_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id"), nullable=True
    )


class PlotRow(Base):
    __tablename__ = "plot"
    __table_args__ = (
        CheckConstraint(
            "irrigation_system in ('none','drip','sprinkler','gravity')",
            name="ck_plot_irrigation_system",
        ),
        CheckConstraint(
            "irrigation_system <> 'none' "
            "or (irrigation_efficiency is null and system_flow_lph is null)",
            name="ck_plot_rainfed_has_no_irrigation",
        ),
        CheckConstraint(
            "irrigation_efficiency is null "
            "or (irrigation_efficiency > 0 and irrigation_efficiency <= 1)",
            name="ck_plot_irrigation_efficiency_range",
        ),
        CheckConstraint(
            "system_flow_lph is null or system_flow_lph > 0",
            name="ck_plot_system_flow_positive",
        ),
        ForeignKeyConstraint(
            ["farm_id", "org_id"],
            ["farm.id", "farm.org_id"],
            name="fk_plot_farm_id_org_id",
        ),
        Index("ix_plot_boundary", "boundary", postgresql_using="gist"),
        Index("ix_plot_org_farm", "org_id", "farm_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    farm_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    """No column-level FK: the composite `fk_plot_farm_id_org_id` below ties
    it to `farm.id` together with `org_id` (T1 review follow-up)."""
    name: Mapped[str] = mapped_column(String, nullable=False)
    boundary: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False), nullable=False
    )
    area_ha: Mapped[decimal.Decimal] = mapped_column(
        Numeric,
        Computed("ST_Area(boundary::geography) / 10000", persisted=True),
        nullable=False,
    )
    weather_cell_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """No FK yet: `weather_cell` is owned by E5."""
    irrigation_system: Mapped[str] = mapped_column(String, nullable=False)
    irrigation_efficiency: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    """Null in secano (docs/03-modelo-datos.md:102)."""
    system_flow_lph: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    """Null in secano (docs/03-modelo-datos.md:103)."""


class CropRow(Base):
    """Global reference data (docs/03-modelo-datos.md:115-120): no `org_id`,
    seeded by the `67cf2dd1f13e` migration, not written through the API."""

    __tablename__ = "crop"
    __table_args__ = (
        UniqueConstraint("code", name="uq_crop_code"),
        CheckConstraint(
            "kc_source in ('fao56','local','approximate','none')", name="ck_crop_kc_source"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    name_es: Mapped[str] = mapped_column(String, nullable=False)
    kc_source: Mapped[str] = mapped_column(String, nullable=False)


class CropStageRow(Base):
    __tablename__ = "crop_stage"
    __table_args__ = (
        CheckConstraint(
            "stage in ('initial','development','mid','late')", name="ck_crop_stage_name"
        ),
        CheckConstraint("length_days > 0", name="ck_crop_stage_length_positive"),
        CheckConstraint("kc > 0", name="ck_crop_stage_kc_positive"),
        CheckConstraint(
            "depletion_fraction_p > 0 and depletion_fraction_p < 1",
            name="ck_crop_stage_depletion_fraction_range",
        ),
    )

    crop_id: Mapped[int] = mapped_column(ForeignKey("crop.id"), primary_key=True)
    stage: Mapped[str] = mapped_column(String, primary_key=True)
    length_days: Mapped[int] = mapped_column(Integer, nullable=False)
    kc: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    depletion_fraction_p: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)


class SoilProfileRow(Base):
    """docs/03-modelo-datos.md:106-113. No `org_id` column: access is always
    gated through the plot (`plot_id` PK/FK), which is itself org-scoped
    (T4 decision, odd/tasks/techcamp-v2-e3-farms.md)."""

    __tablename__ = "soil_profile"
    __table_args__ = (
        CheckConstraint(
            "source is null or source in ('soilgrids','lab','fao56_texture')",
            name="ck_soil_profile_source",
        ),
        CheckConstraint("ph is null or (ph >= 0 and ph <= 14)", name="ck_soil_profile_ph_range"),
        CheckConstraint(
            "organic_matter_pct is null or (organic_matter_pct >= 0 and organic_matter_pct <= 100)",
            name="ck_soil_profile_organic_matter_range",
        ),
        CheckConstraint(
            "field_capacity_pct is null or (field_capacity_pct > 0 and field_capacity_pct <= 100)",
            name="ck_soil_profile_field_capacity_range",
        ),
        CheckConstraint(
            "wilting_point_pct is null or (wilting_point_pct >= 0 and wilting_point_pct < 100)",
            name="ck_soil_profile_wilting_point_range",
        ),
        CheckConstraint(
            "root_depth_cm is null or root_depth_cm > 0", name="ck_soil_profile_root_depth_positive"
        ),
        CheckConstraint(
            "field_capacity_pct is null or wilting_point_pct is null "
            "or wilting_point_pct < field_capacity_pct",
            name="ck_soil_profile_wilting_point_lt_field_capacity",
        ),
    )

    plot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plot.id"), primary_key=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    ph: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    organic_matter_pct: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    texture: Mapped[str | None] = mapped_column(String, nullable=True)
    field_capacity_pct: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    wilting_point_pct: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    root_depth_cm: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
