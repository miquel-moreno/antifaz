"""Errors and their HTTP responses. No response ever includes what the client sent."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from antifaz.errors import AntifazBlocked

MAX_LOCATIONS = 10


class AppError(Exception):
    """Base error. Subclasses set a status code and a stable error code."""

    status_code = 400
    code = "app_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"

    def __init__(self) -> None:
        super().__init__("missing or invalid Antifaz key")


class NotConfiguredError(AppError):
    status_code = 503
    code = "not_configured"

    def __init__(self) -> None:
        super().__init__("the gateway is missing a key in its configuration")


class InvalidRequestError(AppError):
    code = "invalid_request"

    def __init__(self) -> None:
        super().__init__("the body must be a JSON object in UTF-8")


class InvalidHeaderError(AppError):
    code = "invalid_header"

    def __init__(self) -> None:
        super().__init__(
            "anthropic-version and anthropic-beta only accept letters, digits, . _ - ,"
        )


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"

    def __init__(self) -> None:
        super().__init__("request body too large")


class StreamingNotSupportedError(AppError):
    code = "streaming_not_supported"

    def __init__(self) -> None:
        super().__init__("streaming is not supported yet")


class UpstreamTimeoutError(AppError):
    status_code = 504
    code = "upstream_timeout"

    def __init__(self) -> None:
        super().__init__("the provider did not answer in time")


class UpstreamUnavailableError(AppError):
    status_code = 502
    code = "upstream_unavailable"

    def __init__(self) -> None:
        super().__init__("could not reach the provider")


class UpstreamRedirectError(AppError):
    status_code = 502
    code = "upstream_redirect"

    def __init__(self) -> None:
        super().__init__("the provider answered with a redirect, which is not followed")


class BadUpstreamResponseError(AppError):
    status_code = 502
    code = "bad_upstream_response"

    def __init__(self) -> None:
        super().__init__("the provider sent an answer that is not valid JSON")


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


async def _blocked_handler(_: Request, exc: Exception) -> JSONResponse:
    # The message is fixed per class (antifaz.errors): it never carries values.
    if not isinstance(exc, AntifazBlocked):  # pragma: no cover - registered only for it
        raise exc
    return JSONResponse(status_code=400, content=error_body("antifaz_blocked", exc.message))


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
    app.add_exception_handler(AntifazBlocked, _blocked_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
