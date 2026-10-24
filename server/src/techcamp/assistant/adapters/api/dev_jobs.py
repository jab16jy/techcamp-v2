"""Seminar-only manual trigger for the knowledge-base corpus ingestion
(docs/04-api.md §Solo perfil seminario (`/dev`), ADR-0008:32, ADR-0021). Registered
only when the profile is seminar.

It *queues* the ingestion rather than doing its work inline, like every other seminar
trigger (ADR-0021:32 — "Programados **y** ejecutables al instante"): the run opens
transactions and embeds, which belongs to the `worker`, and a seminar that presses the
button wants the same code path a deployed worker would use.

`directory` is how the seminar points the run at the corpus it brought
(`TECHCAMP_KB_CORPUS_DIR` is what the owner turns, and the packaged fixtures are the
default). **A directory that does not exist is not refused here**: this route queues a
run, it does not read a corpus, and `kb_ingest.load_corpus` is the single place that
decides what a directory is — its refusal reaches procrastinate, which retries, rather
than becoming a 422 the seminar has to guess its way out of.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.assistant.adapters.jobs import QUEUE_NAME, ingest_kb_corpus
from techcamp.shared.jobs import app as jobs_app

router = APIRouter(prefix="/dev/jobs", tags=["dev-jobs"])


class KbJobsRunRequest(BaseModel):
    """`{ directory? }`: the corpus to ingest, defaulting to `corpus_dir()` inside the
    job — `TECHCAMP_KB_CORPUS_DIR`, else the packaged fixtures (ADR-0008:32). A plain
    string rather than a validated path, for the reason in this module's docstring."""

    directory: str | None = None


class KbJobsRunResponse(BaseModel):
    """The queued run: one job, because the ingestion walks the corpus directory itself
    and there is nothing to fan out over (D-T12.2)."""

    job_id: int


@router.post("/kb:ingest:run", response_model=KbJobsRunResponse)
async def run_kb_ingest(payload: KbJobsRunRequest) -> KbJobsRunResponse:
    # `queue` on the defer, not only on the task: procrastinate's `defer_async` builds
    # the job from the options it is handed and never from the task's own default
    # (`configure_task`), so without it the job would sit in the default queue this
    # project's worker does not listen to.
    #
    # The procrastinate `App` is opened per call because nothing else opens it in the
    # `api` process (the worker opens its own when it runs), and a dev-only trigger
    # paying one connection per request is the cheap way to be correct.
    async with jobs_app.open_async():
        job_id = await ingest_kb_corpus.configure(queue=QUEUE_NAME).defer_async(
            timestamp=0, directory=payload.directory
        )
    return KbJobsRunResponse(job_id=job_id)
