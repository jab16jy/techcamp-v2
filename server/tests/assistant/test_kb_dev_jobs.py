"""The seminar trigger for the corpus ingestion and the registration it hangs on
(E12 T3, docs/04-api.md §Solo perfil seminario (`/dev`), ADR-0012, ADR-0021).

`POST /api/v1/dev/jobs/kb:ingest:run { directory? }` **queues** the run rather than
ingesting inline, like every other seminar trigger (ADR-0021:32 — "Programados **y**
ejecutables al instante"): the worker owns the run, and a seminar that presses the
button exercises the code path a deployed worker would use.

**The registration half lives in this file too, and not in `test_kb_ingest_job.py`,
because the two halves are one defect.** `ingest_kb_corpus` was reachable only from
the tests that call the coroutine directly: a task no entrypoint imported and a queue
no worker listens to are both dead code, and a direct call passes in either case. So
ADR-0012's own claim — the job runs on the queue the worker runs — is one test that
reads the task's registered queue and the queue list `worker.main()` hands
`run_worker`.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import techcamp.worker as worker
from techcamp.assistant.adapters.jobs import INGEST_CORPUS_TASK_NAME, QUEUE_NAME
from techcamp.main import app
from techcamp.shared.jobs import app as jobs_app

pytestmark = pytest.mark.anyio

# Client-relative: the TestClient's base_url already ends in `/api/v1` and this httpx
# version appends a leading-slash path to it rather than replacing it, so the
# registered path is asserted separately (see below).
_ROUTE = "/dev/jobs/kb:ingest:run"
_REGISTERED_PATH = "/api/v1/dev/jobs/kb:ingest:run"


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    """The route defers through procrastinate's own psycopg connector, on a connection
    this session cannot clean up through, so the job it leaves would outlive the test."""
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _registered_paths(application: Any) -> set[str]:
    return set(application.openapi()["paths"])


async def _queued_ingestions(db_session: AsyncSession) -> dict[int, dict[str, Any]]:
    """The queued ingestion jobs, by id.

    Scoped to the ingestion task name because other suites leave jobs behind: every
    telemetry reading defers one, and those are not what this route queues.
    """
    return {
        job.id: {
            "task_name": job.task_name,
            "queue_name": job.queue_name,
            "status": job.status,
            "args": job.args,
        }
        for job in (
            await db_session.execute(
                text(
                    "SELECT id, task_name, queue_name, status, args FROM procrastinate_jobs "
                    "WHERE task_name = :t ORDER BY id"
                ),
                {"t": INGEST_CORPUS_TASK_NAME},
            )
        ).all()
    }


# -- the worker registration (ADR-0012) ------------------------------------------


async def test_the_worker_entrypoint_imports_the_ingestion_job_module() -> None:
    """`techcamp/worker.py` is registration only (docs/05-arquitectura.md §Solo la
    fachada pública): it imports each module's `jobs` for the side effect of registering
    its tasks. Without that import `assistant.ingest_kb_corpus` does not exist in the
    worker process and no seminar run can ever pick it up."""
    import techcamp.assistant.adapters.jobs as assistant_jobs

    assert worker._assistant_jobs is assistant_jobs
    assert INGEST_CORPUS_TASK_NAME in jobs_app.tasks


async def test_the_ingestion_task_is_registered_on_the_queue_the_worker_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0012 in one assertion: the task names the queue on its `@app.task`, and the
    worker runs that same queue. Either half alone would leave the job dead — a task on a
    queue nobody listens to, or a listened queue whose task was never registered — and
    every test that calls the coroutine directly would stay green in both cases."""
    listened: list[str] = []
    monkeypatch.setattr(
        worker.app, "run_worker", lambda **kwargs: listened.extend(kwargs["queues"])
    )

    worker.main()

    assert jobs_app.tasks[INGEST_CORPUS_TASK_NAME].queue == QUEUE_NAME
    assert QUEUE_NAME in listened


# -- the seminar trigger (ADR-0021) -----------------------------------------------


async def test_the_dev_route_answers_a_job_id_and_queues_the_ingestion(
    db_session: AsyncSession,
) -> None:
    """`POST /dev/jobs/kb:ingest:run` → `{ job_id }` (docs/04-api.md §Solo perfil
    seminario), queued on the `assistant` queue the worker runs.

    `timestamp` is procrastinate's periodic argument and the corpus run does not read
    it, but the signature takes it first, so the defer has to send it. `directory` is
    sent as an explicit `null` rather than left out: the route resolves nothing, so the
    queued args say which corpus the run was asked for — the job's own default, which
    is `corpus_dir()` — instead of the args saying nothing at all.
    """
    response = _client().post(_ROUTE, json={})

    assert response.request.url.path == _REGISTERED_PATH
    assert response.status_code == 200
    queued = await _queued_ingestions(db_session)
    assert response.json()["job_id"] in queued
    queued_job = queued[response.json()["job_id"]]
    assert queued_job["task_name"] == INGEST_CORPUS_TASK_NAME
    assert queued_job["queue_name"] == QUEUE_NAME
    assert queued_job["status"] == "todo"
    assert queued_job["args"] == {"timestamp": 0, "directory": None}


async def test_the_dev_route_carries_the_requested_directory_to_the_job(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """ADR-0021's seminar profile points the run at a corpus the seminar brought:
    `directory` in the body is how, and without it the job resolves `corpus_dir()` —
    `TECHCAMP_KB_CORPUS_DIR` or the packaged fixtures (ADR-0008:32). The directory does
    not have to exist for this assertion: the route queues a run, it does not load a
    corpus."""
    corpus = tmp_path / "corpus-from-the-seminar"

    response = _client().post(_ROUTE, json={"directory": str(corpus)})

    assert response.status_code == 200
    queued = await _queued_ingestions(db_session)
    assert queued[response.json()["job_id"]]["args"]["directory"] == str(corpus)


async def test_the_dev_route_is_absent_in_the_production_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0021: `/dev` routes are registered only when the profile is seminar, so a
    production deployment cannot be talked into ingesting an arbitrary directory."""
    import techcamp.main as main

    assert _REGISTERED_PATH in _registered_paths(app), "seminar profile keeps the route"
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    try:
        production_app = importlib.reload(main).app
        assert _REGISTERED_PATH not in _registered_paths(production_app)
    finally:
        monkeypatch.undo()
        importlib.reload(main)
    assert _REGISTERED_PATH in _registered_paths(main.app)
