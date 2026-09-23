"""RFC 9457 (application/problem+json) error responses (docs/04-api.md)."""

from __future__ import annotations

from fastapi import FastAPI, Request
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


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, _problem_exception_handler)
