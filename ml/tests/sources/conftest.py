"""Shared fixture loader: every parser test reads a trimmed real response."""

from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture() -> Callable[[str], bytes]:
    """The raw bytes of a recorded response under `fixtures/`."""

    def load(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return load
