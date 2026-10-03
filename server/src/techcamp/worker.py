"""The `worker` process entrypoint (ADR-0012, docs/05-arquitectura.md:78-83:
"api, ingestor y worker usan la misma imagen con distinto comando").

Entrypoint: `python -m techcamp.worker`.
"""

from __future__ import annotations

from techcamp.alerts.adapters import jobs as _alerts_jobs  # noqa: F401  registers tasks
from techcamp.irrigation.adapters import jobs as _irrigation_jobs  # noqa: F401  registers tasks
from techcamp.notifications.adapters import (
    jobs as _notifications_jobs,  # noqa: F401  registers tasks
)
from techcamp.risk.adapters import jobs as _risk_jobs
from techcamp.shared.jobs import app
from techcamp.telemetry.adapters import jobs as _telemetry_jobs  # noqa: F401  registers tasks
from techcamp.weather.adapters import jobs as _weather_jobs  # noqa: F401  registers tasks
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository


def main() -> None:
    # Composition root (docs/05-arquitectura.md §Solo la fachada pública): `risk`
    # needs weather's cell reader and may not import `weather.adapters`, so the
    # concrete repository is built here and handed to the task, the way
    # `techcamp.ingestor` composes the `alerts` evaluator into `telemetry`.
    _risk_jobs.configure_weather_cells(SqlAlchemyWeatherRepository)

    app.run_worker(
        queues=[
            _telemetry_jobs.QUEUE_NAME,
            _weather_jobs.QUEUE_NAME,
            _irrigation_jobs.QUEUE_NAME,
            _alerts_jobs.QUEUE_NAME,
            _notifications_jobs.QUEUE_NAME,
            _risk_jobs.QUEUE_NAME,
        ]
    )


if __name__ == "__main__":
    main()
