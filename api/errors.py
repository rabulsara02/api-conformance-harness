"""
Structured error responses.

FastAPI's default error body is {"detail": ...}, where `detail` is a string for
HTTPException and a LIST OF OBJECTS for validation failures. No single useful
schema describes both, so error responses cannot be meaningfully contract-tested
-- and for most APIs, error behaviour is a large share of the behaviour worth
testing.

This module normalises every error the application can produce into one declared
shape:

    {"error": {"code": "not_found", "message": "No device with id 999"}}

Note that the handlers below intercept FRAMEWORK-generated errors too, not just
ones we raise. Without that, a request to an unknown path would still return
FastAPI's default {"detail": "Not Found"} -- a single endpoint disagreeing with
every other one, which is exactly the kind of inconsistency the harness is being
built to detect.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.models import ErrorDetail, ErrorResponse

# Status code -> stable machine-readable code. Clients are expected to branch on
# `code`, never on `message`: the message is for humans and may be reworded, the
# code is part of the contract.
_CODE_BY_STATUS: dict[int, str] = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    500: "internal_error",
}


def _payload(status_code: int, message: str) -> dict:
    """Build the error body for a status code, as a plain JSON-ready dict."""
    body = ErrorResponse(
        error=ErrorDetail(
            code=_CODE_BY_STATUS.get(status_code, "error"),
            message=message,
        )
    )
    # model_dump() converts the Pydantic object into a dict JSONResponse can
    # serialize. Building it through the model (rather than writing the dict by
    # hand) guarantees the response can never drift from the declared schema.
    return body.model_dump()


def register_error_handlers(app: FastAPI) -> None:
    """
    Attach the handlers to an app.

    A function rather than module-level decorators so the app object is passed
    in explicitly -- it keeps main.py readable and avoids a circular import.
    """

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """
        Catch every HTTPException, ours and the framework's.

        Registered against STARLETTE's HTTPException rather than FastAPI's on
        purpose: FastAPI's class is a subclass of it, so this catches both --
        including the 404 the router raises for an unknown path and the 405 it
        raises for a wrong method, neither of which our code ever raises.
        """
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.status_code, str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """
        Reshape FastAPI's validation errors into the standard envelope.

        FastAPI reports a list of problems; we surface the first one as a
        readable message like "path.device_id: Input should be a valid integer".
        Detail is lost, which is a deliberate trade: one predictable shape is
        worth more to a machine consumer than a rich but unschematisable one.
        """
        problems = exc.errors()
        if problems:
            first = problems[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            reason = first.get("msg", "invalid request")
            message = f"{location}: {reason}" if location else reason
        else:
            message = "Request validation failed"

        return JSONResponse(status_code=422, content=_payload(422, message))