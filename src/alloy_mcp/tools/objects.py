"""The four read-only tools covering PRD-21267's acceptance criteria.

Tool shape mirrors Alloy Navigator's own generic "object class" API (Option B
in the tech design) rather than one hand-named tool per resource type: the
vendor's API, and this ticket's "and other objects", both treat the object
class as data, not as a fixed enum baked into the connector.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from alloy_mcp.api_client import AlloyClient
from alloy_mcp.errors import AlloyApiError, error_envelope, map_status_to_error, status_to_code

# SOP section 2.5: single tool-call response size cap. Alloy Navigator list
# endpoints do not themselves truncate, so this is enforced here.
_MAX_RESPONSE_CHARS = 20_000

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)

# SOP section 6 / tech design "Non-functional -> Observability": one
# structured JSON log line per tool call, carrying a request-id for
# correlation, the (redacted) identifying params, the upstream HTTP status,
# latency, and -- on failure -- the mapped error code. Never the credential
# headers (never read here) or a raw response/request body.
_logger = logging.getLogger("alloy_mcp")


def _log_call(
    request_id: str,
    tool: str,
    params: dict[str, Any],
    status_code: int | None,
    latency_ms: float,
    error_code: str | None,
) -> None:
    record = {
        "request_id": request_id,
        "tool": tool,
        "params": params,
        "status": status_code,
        "latency_ms": round(latency_ms, 1),
        "error_code": error_code,
    }
    _logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def _dump(payload: dict) -> str:
    """Compact, non-ASCII-escaped JSON -- indent=2 would double token cost
    for no benefit to the Agent (SOP section 2.5)."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= _MAX_RESPONSE_CHARS:
        return text
    # Degrade gracefully rather than silently truncating valid JSON mid
    # object: report truncation explicitly so the Agent knows to narrow its
    # request (smaller limit, fewer fields) instead of trusting a partial
    # result.
    return error_envelope(
        "invalid_argument",
        f"Result exceeds {_MAX_RESPONSE_CHARS} characters; retry with a smaller limit or a narrower fields list.",
        retryable=False,
    )


def register(mcp: FastMCP, client_factory: Callable[[], AlloyClient | None]) -> None:
    def require_client() -> AlloyClient:
        client = client_factory()
        if client is None:
            # Defensive only: the credential middleware already rejects any
            # /mcp request missing a header before a tool ever runs.
            raise _NotConfigured()
        return client

    async def _execute(
        tool: str,
        params: dict[str, Any],
        call: Callable[[AlloyClient], Awaitable[Any]],
    ) -> str:
        """Run one tool's upstream call and emit its structured log line.

        Centralized here (rather than duplicated per tool) so every tool
        gets the same logging shape and the same credential/error handling
        for free -- see the module docstring on `_log_call`.
        """
        request_id = uuid.uuid4().hex[:12]
        start = time.monotonic()
        try:
            client = require_client()
        except _NotConfigured:
            _log_call(request_id, tool, params, None, (time.monotonic() - start) * 1000, "not_configured")
            return error_envelope("not_configured", "No Alloy Navigator credentials in request context.", retryable=False)

        try:
            result = await call(client)
        except AlloyApiError as exc:
            error_code = status_to_code(exc.status_code)
            _log_call(request_id, tool, params, exc.status_code, (time.monotonic() - start) * 1000, error_code)
            return map_status_to_error(exc.status_code, exc.message)

        _log_call(request_id, tool, params, client.last_status_code, (time.monotonic() - start) * 1000, None)
        return _dump(result)

    @mcp.tool(
        name="alloy_list_objects",
        annotations=_READ_ONLY,
        description=(
            "List objects of an Alloy Navigator class (e.g. Incidents, Computers, Work Orders).\n"
            "Also works for any class this tenant's instance defines. No matches returns an "
            "empty list, not an error.\n"
            "Args: object_class (required, e.g. 'Incidents'); search_text (optional free-text "
            "filter); fields (optional, trims returned fields); sort_asc (optional field name); "
            "limit (default 50, max 200); offset (default 0)."
        ),
    )
    async def alloy_list_objects(
        object_class: str,
        search_text: str | None = None,
        fields: list[str] | None = None,
        sort_asc: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        limit = max(1, min(limit, 200))

        async def call(client: AlloyClient) -> Any:
            return await client.list_objects(
                object_class,
                search_text=search_text,
                fields=fields,
                sort_asc=sort_asc,
                limit=limit,
                offset=offset,
            )

        # search_text/fields/sort_asc are free-text and not named in the
        # tech design's log field list -- only object_class and pagination
        # are logged (SOP section 6: never a raw request/response body).
        params = {"object_class": object_class, "limit": limit, "offset": offset}
        return await _execute("alloy_list_objects", params, call)

    @mcp.tool(
        name="alloy_get_object",
        annotations=_READ_ONLY,
        description=(
            "Get one Alloy Navigator object's full detail by id.\n"
            "Args: oid (required, the object id)."
        ),
    )
    async def alloy_get_object(oid: str) -> str:
        async def call(client: AlloyClient) -> Any:
            return await client.get_object(oid)

        return await _execute("alloy_get_object", {"oid": oid}, call)

    @mcp.tool(
        name="alloy_get_object_activities",
        annotations=_READ_ONLY,
        description=(
            "Get an Alloy Navigator object's activity/history entries (status changes, notes), newest first.\n"
            "Args: oid (required); limit (default 50, max 200); offset (default 0)."
        ),
    )
    async def alloy_get_object_activities(oid: str, limit: int = 50, offset: int = 0) -> str:
        limit = max(1, min(limit, 200))

        async def call(client: AlloyClient) -> Any:
            return await client.get_object_activities(oid, limit=limit, offset=offset)

        params = {"oid": oid, "limit": limit, "offset": offset}
        return await _execute("alloy_get_object_activities", params, call)

    @mcp.tool(
        name="alloy_get_dictionary",
        annotations=_READ_ONLY,
        description=(
            "Resolve an Alloy Navigator dictionary (e.g. incident priorities) to its code-to-label values.\n"
            "Args: dictionary_name (required)."
        ),
    )
    async def alloy_get_dictionary(dictionary_name: str) -> str:
        async def call(client: AlloyClient) -> Any:
            return await client.get_dictionary(dictionary_name)

        return await _execute("alloy_get_dictionary", {"dictionary_name": dictionary_name}, call)


class _NotConfigured(Exception):
    """Internal signal only -- never leaves register(); mapped to the
    not_configured error envelope above."""
