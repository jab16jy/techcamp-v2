"""`home` wires no repository of its own: every dep comes from its own module.

`home/adapters/api/router.py` once carried a local
`get_monthly_metric_repository` plus its own `MonthlyMetricRepoDep` alias,
because `metrics` shipped no dep yet, and its docstring promised the removal
"the moment T7's `MonthlyMetricRepoDep` lands". Nothing collected that promise,
so it outlived its condition. These tests are the collection: re-introducing a
provider in `home`, or naming the metrics SQL adapter from it, fails here.

Pure wiring, no database. The behaviour these deps serve is
`test_plot_status.py` and `test_api.py`: `/status` returns
`digital_adoption_index` from the plot's latest `plot_metric_monthly` row
(D-T0.13).
"""

from __future__ import annotations

import inspect
from typing import get_type_hints

from techcamp.home.adapters.api import router as home_router
from techcamp.home.application import build_plot_status
from techcamp.metrics.adapters.api import deps as metrics_deps
from techcamp.metrics.application.adoption import MonthlyMetricRepository


def test_home_status_uses_the_shared_metrics_dep() -> None:
    assert home_router.MonthlyMetricRepoDep is metrics_deps.MonthlyMetricRepoDep

    # Negative: `home` defines no provider of its own ...
    assert not hasattr(home_router, "get_monthly_metric_repository")
    # ... and the metrics SQL adapter stays behind the `metrics` boundary: it is
    # not even spelled in this file, so nothing here can bind to it.
    assert "SqlAlchemyMonthlyMetricRepository" not in inspect.getsource(home_router)


def test_home_status_use_case_is_typed_to_the_metrics_port() -> None:
    annotation = get_type_hints(build_plot_status)["metrics"]

    assert annotation is MonthlyMetricRepository
    # Negative: the use case asks for the port, never for an implementation —
    # a concrete adapter is not a protocol.
    assert getattr(annotation, "_is_protocol", False) is True
