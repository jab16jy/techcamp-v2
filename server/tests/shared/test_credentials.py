"""`shared/credentials.py`: a stored hash this module can't read is a failed
verification, never a raised `ValueError`."""

from __future__ import annotations

import pytest

from techcamp.shared.credentials import generate_password, hash_password, verify_password


def test_a_generated_password_verifies_against_its_own_hash() -> None:
    password = generate_password()
    stored = hash_password(password)

    assert verify_password(password, stored) is True
    assert verify_password(f"{password}x", stored) is False


@pytest.mark.parametrize(
    "stored_hash",
    [
        pytest.param("", id="empty"),
        pytest.param("scrypt$16384$8$1", id="fewer-than-six-fields"),
        pytest.param("bcrypt$16384$8$1$00$00", id="unknown-algorithm"),
        pytest.param("scrypt$16384$8$1$zz$zz", id="non-hex-salt"),
        pytest.param("scrypt$x$8$1$00$00", id="non-numeric-cost"),
    ],
)
def test_verify_password_rejects_a_malformed_stored_hash(stored_hash: str) -> None:
    assert verify_password("any-password", stored_hash) is False
