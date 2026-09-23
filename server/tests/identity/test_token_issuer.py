import pytest

from techcamp.identity.adapters.security.token_issuer import (
    InvalidTokenError,
    decode_token,
    issue_token,
)


def test_issue_and_decode_token_round_trip() -> None:
    token = issue_token("user-123")

    claims = decode_token(token)

    assert claims["sub"] == "user-123"


def test_decode_token_rejects_wrong_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    token = issue_token("user-123")
    monkeypatch.setenv("TECHCAMP_JWT_AUDIENCE", "someone-else")

    with pytest.raises(InvalidTokenError):
        decode_token(token)


def test_decode_token_rejects_expired_token() -> None:
    token = issue_token("user-123", ttl_seconds=-10)

    with pytest.raises(InvalidTokenError):
        decode_token(token)


def test_decode_token_rejects_malformed_token() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token("not-a-jwt")
