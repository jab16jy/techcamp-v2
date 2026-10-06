"""Errors of the climate-risk domain.

The archive is an external source, so its failure is a domain fact the job has
to branch on: with no answer there is no prediction for that cell this month,
and the cell keeps serving the one it has (docs/06-diseno-detallado.md §8).
"""

from __future__ import annotations


class ArchiveUnavailableError(Exception):
    """Raised when the Open-Meteo archive is unreachable, returns an error
    status, or returns a body this adapter cannot read."""


class ArchiveCircuitBreakerOpenError(ArchiveUnavailableError):
    """Raised when the archive circuit breaker is OPEN, failing fast."""
