"""Errors and their HTTP responses. No response ever includes what the client sent."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

MAX_LOCATIONS = 10


class AppError(Exception):
    """Base error. Subclasses set a status code and a stable error code."""

    status_code = 400
    code = "app_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):  # pragma: no cover - registered only for AppError
        raise exc
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message))


def _safe_location(loc: tuple[object, ...]) -> str:
    """Where the problem is, without anything the client wrote.

    Only the source ("body", "query"...) and list indexes are kept. Every other part is
    replaced by "*": a dict key sent by the client (e.g. {"12345678Z": 1}) is client data.
    """
    parts = [str(loc[0])] if loc else []
    parts += [str(p) if isinstance(p, int) else "*" for p in loc[1:]]
    return ".".join(parts)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # FastAPI's default 422 echoes the received input, which may contain personal data.
    # Only a sanitised location of each problem is returned, never values or keys.
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - registered only for it
        raise exc
    fields = sorted({_safe_location(tuple(e.get("loc", ()))) for e in exc.errors()})
    if len(fields) > MAX_LOCATIONS:  # a body with thousands of errors must not grow the answer
        fields = [*fields[:MAX_LOCATIONS], "..."]
    message = "invalid request: check " + ", ".join(fields) if fields else "invalid request"
    return JSONResponse(status_code=422, content=error_body("invalid_request", message))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
