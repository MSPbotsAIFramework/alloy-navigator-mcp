"""Structured error envelope returned by every tool (never a raised exception).

Error code vocabulary is fixed by the vendor-mcp-development SOP section 4.2:
not_configured / unauthorized / not_found / invalid_argument / rate_limited /
upstream_error. Tools must map vendor HTTP responses onto this table, never
invent a new code.
"""

from __future__ import annotations

import json
from typing import Literal

ErrorCode = Literal[
    "not_configured",
    "unauthorized",
    "not_found",
    "invalid_argument",
    "rate_limited",
    "upstream_error",
]


class AlloyApiError(Exception):
    """Raised by api_client.py; carries the vendor's status code and message.

    Tool handlers catch this and translate it to the error envelope -- it
    never escapes a tool function as an unhandled exception.
    """

    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def error_envelope(code: ErrorCode, message: str, *, retryable: bool) -> str:
    """Serialize the SOP section 4.2 error envelope as a compact JSON string."""
    payload = {"error": {"code": code, "message": message, "retryable": retryable}}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def status_to_code(status_code: int | None) -> ErrorCode:
    """Map an Alloy Navigator HTTP status (or None for a transport failure)
    onto the fixed error-code vocabulary. Used for both the OAuth2 token
    endpoint and the object/activities/dictionary endpoints -- Alloy
    Navigator uses the same HTTP semantics for both. Factored out of
    `map_status_to_error` so per-request logging (PRD-21267 review round 1)
    can record the same error code without re-parsing the JSON envelope.
    """
    if status_code in (401, 403):
        return "unauthorized"
    if status_code == 404:
        return "not_found"
    if status_code in (400, 422):
        return "invalid_argument"
    if status_code == 429:
        return "rate_limited"
    if status_code is None or status_code >= 500:
        return "upstream_error"
    # Any other 4xx we did not anticipate: treat as a client-side argument
    # problem rather than silently inventing a new code.
    return "invalid_argument"


def map_status_to_error(status_code: int | None, message: str) -> str:
    """Serialize `status_to_code`'s mapping as the SOP section 4.2 error envelope."""
    code = status_to_code(status_code)
    retryable = code in ("rate_limited", "upstream_error")
    return error_envelope(code, message, retryable=retryable)
