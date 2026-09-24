"""Soil profile write use case (docs/04-api.md:49; docs/03-modelo-datos.md:106-113).

`PUT /plots/{plot_id}/soil`. Same access rule as plots (T4 decision,
odd/tasks/techcamp-v2-e3-farms.md): owner and technician write, producer and
viewer read only; a plot outside the caller's orgs is `PlotNotFoundError` (404).
"""

from __future__ import annotations

from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import PlotRepository, SoilProfileRepository
from techcamp.farms.domain.models import SoilProfile, apply_fao56_texture_fallback, ensure_can_write
from techcamp.identity.application.ports import MembershipRepository


async def put_soil_profile(
    *,
    user_id: UUID,
    plot_id: UUID,
    texture: str | None,
    ph: float | None,
    organic_matter_pct: float | None,
    field_capacity_pct: float | None,
    wilting_point_pct: float | None,
    root_depth_cm: float | None,
    plots: PlotRepository,
    soil_profiles: SoilProfileRepository,
    memberships: MembershipRepository,
) -> SoilProfile:
    plot, role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    ensure_can_write(role)
    resolved_fc, resolved_wp, source = apply_fao56_texture_fallback(
        texture, field_capacity_pct, wilting_point_pct
    )
    profile = SoilProfile(
        plot_id=plot.id,
        source=source,
        ph=ph,
        organic_matter_pct=organic_matter_pct,
        texture=texture,
        field_capacity_pct=resolved_fc,
        wilting_point_pct=resolved_wp,
        root_depth_cm=root_depth_cm,
    )
    return await soil_profiles.put(profile)
