"""The procrastinate job queue in Postgres (ADR-0012), run by the `worker`
process (docs/05-arquitectura.md:78-83). Each module's own `adapters/jobs.py`
registers its tasks on this shared `app` (only `telemetry` has one so far).

Deferring a job from the `api`/`ingestor` processes does **not** go through
this `app`: it needs psycopg (this app's connector), a different DBAPI driver
from the asyncpg one `shared/db.py`'s `AsyncSession` uses, so it can't share
one physical Postgres transaction with a SQLAlchemy write (T7 decision,
flagged doc gap: ADR-0012 says jobs are "enqueued in the same transaction as
the data that triggers them", which this app's connector alone can't do
across drivers). Same-transaction enqueuing instead calls procrastinate's own
`procrastinate_defer_jobs_v1` SQL function directly over the SQLAlchemy
session already open for the write (see `telemetry/adapters/jobs.py`); the
server-side triggers that log the job and wake the worker fire the same way
regardless of which client performed the `INSERT`. This `app` is only for
running the worker itself.
"""

from __future__ import annotations

from procrastinate import App, PsycopgConnector

from techcamp.shared.config import psycopg_conninfo

app = App(connector=PsycopgConnector(conninfo=psycopg_conninfo()))
