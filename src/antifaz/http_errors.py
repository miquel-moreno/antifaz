"""Errors with a fixed HTTP answer, shared by the gateway and the panel (ADR-0015, ADR-0018).

Neutral on purpose: no project import and no pydantic, only Starlette's JSONResponse. The panel
(antifaz.web) uses these without loading antifaz.api.errors (pydantic models for the OpenAPI
file) or antifaz.config (the settings, where the secrets live); a test checks it in a fresh
interpreter. The gateway's own errors and handlers stay in antifaz.api.errors.

No response ever includes what the client sent.
"""

from collections.abc import Mapping

from starlette.responses import JSONResponse


class AppError(Exception):
    """Base error. Subclasses set a status code and a stable error code."""

    status_code = 400
    code = "app_error"

    def __init__(self, message: str, headers: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        # Fixed response headers (Retry-After on 429); never anything the client sent.
        self.headers: dict[str, str] = dict(headers or {})


class PanelNotFoundError(AppError):
    """Any /panel* path while ANTIFAZ_ADMIN_TOKEN is unset.

    Byte-identical in status and body to the router's 404 for an unknown route. That does not
    hide whether the panel is on: with the token set, the public GET /panel/login answers 200
    to anyone, so an anonymous client can always tell. It only keeps the "off" answer plain.
    """

    status_code = 404
    code = "not_found"

    def __init__(self) -> None:
        super().__init__("no such route")


class PanelUnauthorizedError(AppError):
    status_code = 401
    code = "panel_unauthorized"

    def __init__(self) -> None:
        super().__init__("log in to the panel first")


class PanelLoginFailedError(AppError):
    """Every failed login, whatever went wrong (body, size, token): byte-identical answers."""

    status_code = 401
    code = "login_failed"

    def __init__(self) -> None:
        super().__init__("login failed")


class TooManyAttemptsError(AppError):
    status_code = 429
    code = "too_many_attempts"

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "too many failed logins: wait and try again",
            headers={"Retry-After": str(retry_after)},
        )


class PanelInternalError(AppError):
    """An unexpected error inside /panel*: a fixed body, never the error's text."""

    status_code = 500
    code = "internal_error"

    def __init__(self) -> None:
        super().__init__("internal error")


class CsrfRejectedError(AppError):
    status_code = 403
    code = "csrf_rejected"

    def __init__(self) -> None:
        super().__init__("request refused: it must come from the panel's own page, as JSON")


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def error_response(exc: AppError) -> JSONResponse:
    """The JSON answer of an AppError, with its fixed headers (handler, gate and panel door)."""
    return JSONResponse(
        status_code=exc.status_code, content=error_body(exc.code, exc.message), headers=exc.headers
    )
