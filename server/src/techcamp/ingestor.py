"""The `ingestor` process entrypoint (ADR-0002, docs/05-arquitectura.md:53-83:
"api, ingestor y worker usan la misma imagen con distinto comando").

The composition root of the reading-threshold rules (D9): `telemetry` owns the
MQTT loop and the ingest pipeline, but it must never import `alerts`
(docs/05 has no `telemetry → alerts` edge), so the evaluator that turns the
readings that landed into alerts is composed here and injected as the
`after_flush` hook. It is built on the session each flush already opened
(D14), and it runs after the batch commit, in its own transaction (D16).

Entrypoint: `python -m techcamp.ingestor`.
"""

from __future__ import annotations

import asyncio
import logging

from techcamp.alerts.adapters.evaluate_readings import build_evaluator
from techcamp.telemetry.adapters.ingestor import run


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(build_after_flush=build_evaluator))


if __name__ == "__main__":
    main()
