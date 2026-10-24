"""The eval package's shared fixtures.

Every module here pins and replays the packaged fixture corpus, so the one
hermeticity the whole package needs lives here, once.
"""

import pytest

from techcamp.assistant.adapters.kb_ingest import CORPUS_DIR_ENV_VAR


@pytest.fixture(autouse=True)
def _fixture_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """The packaged fixture corpus (ADR-0008:32), whatever the shell's environment says.

    `pin_corpus()` reads `TECHCAMP_KB_CORPUS_DIR` at call time — it is the owner's
    switch for the licence review — so a developer with it exported would have every
    pin, replay and artifact in this package assert against their own corpus instead
    of the pinned one.
    """
    monkeypatch.delenv(CORPUS_DIR_ENV_VAR, raising=False)
