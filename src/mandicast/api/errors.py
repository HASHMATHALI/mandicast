"""One error format for every failure (docs/07 §1): {"error": {"code", "message", "details"}}.

Validation errors become 400 `invalid_parameter` (422 is reserved for `insufficient_data`); the offending input is
never echoed back. Unexpected errors return a generic 500 with the request id; the details go to the log only.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("mandicast.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def bad_param(message: str, **details) -> ApiError:
    return ApiError(400, "invalid_parameter", message, details)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = [{"parameter": ".".join(str(p) for p in e.get("loc", ())[1:]) or str(e.get("loc", ("?",))[0]),
                 "problem": e.get("msg", "invalid")} for e in exc.errors()]
    return JSONResponse(error_body("invalid_parameter", "One or more parameters are invalid.", {"errors": problems}),
                        status_code=400)


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    message = {404: "No such endpoint.", 405: "Method not allowed."}.get(exc.status_code, "Request failed.")
    return JSONResponse(error_body(code, message), status_code=exc.status_code, headers=getattr(exc, "headers", None))


def internal_error(request_id: str) -> JSONResponse:
    return JSONResponse(error_body("internal_error", "Unexpected error. It has been logged.",
                                   {"request_id": request_id}), status_code=500)
