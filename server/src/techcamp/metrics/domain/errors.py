"""Domain-level enrollment-survey failures. Pure, no I/O."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from techcamp.identity.domain.models import Role


class PlotBaselineNotFoundError(Exception):
    """Raised when a plot has no enrollment survey yet.

    docs/04-api.md:233 answers `404` in this case: a plot without a survey is a
    missing resource, not an empty one. A caller cannot tell it apart from a
    plot outside their orgs, which is the same `404` by design
    (docs/09-cuellos-de-botella.md#seguridad).
    """

    def __init__(self, plot_id: UUID) -> None:
        self.plot_id = plot_id
        super().__init__(f"Plot {plot_id} has no enrollment survey")


class InsufficientRoleError(Exception):
    """Raised when a role outside `BASELINE_WRITE_ROLES` saves a survey.

    docs/04-api.md:233 gives owner and technician the write and every other
    member read-only access (D-T0.10); the adapter maps this to `403`.

    Its own type rather than `farms`' (docs/05: a module imports only another
    module's `application` package), with the same meaning and message shape.
    """

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__(f"Role '{role}' cannot save an enrollment survey")


class UnknownCropError(Exception):
    """Raised when `crop_id` is not in the crop catalog.

    docs/04-api.md:233 answers `422`: a bad referenced id on an input field is
    not the endpoint's own resource, the same treatment `POST /plots/{id}/cycles`
    gives an unknown `crop_id`.
    """

    def __init__(self, crop_id: int) -> None:
        self.crop_id = crop_id
        super().__init__(f"Crop {crop_id} not found")


class PlotMonthOwnedByAnotherOrganizationError(Exception):
    """Raised when an upsert would move a stored month to another organization.

    `(plot_id, month)` is unique across organizations, so an upsert carrying a
    foreign `org_id` collides with a month the caller does not own. The store
    refuses it instead of transferring the row: ownership is never rewritten by
    a write (docs/09-cuellos-de-botella.md#seguridad).
    """

    def __init__(self, plot_id: UUID, month: date) -> None:
        self.plot_id = plot_id
        self.month = month
        super().__init__(
            f"Month {month.isoformat()} of plot {plot_id} belongs to another organization"
        )
