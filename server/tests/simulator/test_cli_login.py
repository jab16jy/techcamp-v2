"""The simulator CLI's OTP login (docs/04-api.md:173-174, ADR-0021).

`--otp-code` is documented in the CLI as the scripted-run path, but
`otp_store.issue` overwrites any previous code for the phone, so requesting a
code unconditionally invalidated the one the caller had fetched in advance.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Receive, Scope, Send

from techcamp.identity.adapters.security.otp_store import otp_store
from techcamp.main import app
from techcamp.simulator.__main__ import _login

from .helpers import member

pytestmark = pytest.mark.anyio


def _recording_client() -> tuple[httpx.AsyncClient, list[str]]:
    """An in-process client that records the requests it was asked to make, so
    a test can assert on the calls themselves and not only on the outcome."""
    requested: list[str] = []

    async def _recording_app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            requested.append(f"{scope['method']} {scope['path']}")
        await app(scope, receive, send)

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_recording_app), base_url="http://testserver/api/v1"
    )
    return client, requested


async def test_login_with_an_otp_code_never_requests_a_new_one(
    db_session: AsyncSession,
) -> None:
    _org_id, phone = await member(db_session)
    otp_store.issue(phone)  # the code a scripted run fetches in advance
    code = otp_store._codes[phone][0]

    client, requested = _recording_client()
    async with client:
        token = await _login(client, phone=phone, otp_code=code)

    assert token
    assert requested == ["POST /api/v1/dev/auth/otp/verify"]


async def test_login_without_an_otp_code_requests_one_and_prompts_for_it(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interactive demo path must keep working: no code given means the
    simulator asks the api for one and reads it off the api console."""
    _org_id, phone = await member(db_session)
    prompts: list[str] = []

    def _fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return otp_store._codes[phone][0]

    monkeypatch.setattr("builtins.input", _fake_input)

    client, requested = _recording_client()
    async with client:
        token = await _login(client, phone=phone, otp_code=None)

    assert token
    assert prompts
    assert requested == ["POST /api/v1/dev/auth/otp", "POST /api/v1/dev/auth/otp/verify"]
