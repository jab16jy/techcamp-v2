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
from sqlalchemy import CheckConstraint, Computed, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class FarmRow(Base):
    __tablename__ = "farm"
    __table_args__ = (Index("ix_farm_org_id", "org_id"),)

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
        Index("ix_plot_boundary", "boundary", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    farm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("farm.id"), nullable=False)
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
