"""Seminar-only OTP sign-in (ADR-0021). Registered only when the profile is seminar."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.identity.adapters.api.deps import UserRepoDep
from techcamp.identity.adapters.security.otp_store import otp_store
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.shared.errors import ProblemError

router = APIRouter(prefix="/dev/auth", tags=["dev-auth"])

_INVALID_CODE_DETAIL = "The OTP code is invalid or has expired."


class OtpRequest(BaseModel):
    phone: str


class OtpVerifyRequest(BaseModel):
    phone: str
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/otp", status_code=204)
async def request_otp(payload: OtpRequest) -> None:
    code = otp_store.issue(payload.phone)
    print(f"[dev] OTP for {payload.phone}: {code}")  # noqa: T201 — the doc-specified delivery


@router.post("/otp/verify", response_model=TokenResponse)
async def verify_otp(payload: OtpVerifyRequest, users: UserRepoDep) -> TokenResponse:
    if not otp_store.verify(payload.phone, payload.code):
        raise ProblemError(status=401, title="Invalid or expired code", detail=_INVALID_CODE_DETAIL)
    user = await users.get_by_phone(payload.phone)
    if user is None:
        raise ProblemError(status=401, title="Invalid or expired code", detail=_INVALID_CODE_DETAIL)
    return TokenResponse(access_token=issue_token(str(user.id)))
