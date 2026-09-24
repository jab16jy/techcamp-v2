"""RFC 9457 (application/problem+json) error responses (docs/04-api.md)."""

from __future__ import annotations

import math
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ProblemError(Exception):
    """Raised by adapters to produce a problem+json response."""

    def __init__(
        self,
        *,
        status: int,
        title: str,
        detail: str | None = None,
        type_: str = "about:blank",
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.type_ = type_
        self.errors = errors or []
        super().__init__(detail or title)


def _problem_response(error: ProblemError) -> JSONResponse:
    body: dict[str, object] = {"type": error.type_, "title": error.title, "status": error.status}
    if error.detail:
        body["detail"] = error.detail
    if error.errors:
        body["errors"] = error.errors
    return JSONResponse(
        status_code=error.status, content=body, media_type="application/problem+json"
    )


async def _problem_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProblemError)
    return _problem_response(exc)


def _sanitize_non_finite_floats(value: Any) -> Any:
    """A rejected non-finite coordinate (`nan`/`inf`) echoes back as the
    validation error's `input`; Starlette's `JSONResponse` refuses to
    serialize those (`allow_nan=False`), which would otherwise turn our 422
    into an unhandled 500 (T2b review follow-up).
    """
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _sanitize_non_finite_floats(v) for key, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_non_finite_floats(v) for v in value]
    return value


async def _validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    content = _sanitize_non_finite_floats(jsonable_encoder({"detail": exc.errors()}))
    return JSONResponse(status_code=422, content=content)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, _problem_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
