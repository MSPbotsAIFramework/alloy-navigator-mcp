"""The four read-only tools covering PRD-21267's acceptance criteria.

Tool shape mirrors Alloy Navigator's own generic "object class" API (Option B
in the tech design) rather than one hand-named tool per resource type: the
vendor's API, and this ticket's "and other objects", both treat the object
class as data, not as a fixed enum baked into the connector.
"""

from __future__ import annotations

import json
from typing import Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from alloy_mcp.api_client import AlloyClient
from alloy_mcp.errors import AlloyApiError, error_envelope, map_status_to_error

# SOP section 2.5: single tool-call response size cap. Alloy Navigator list
# endpoints do not themselves truncate, so this is enforced here.
_MAX_RESPONSE_CHARS = 20_000

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)


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
        try:
            client = require_client()
            result = await client.list_objects(
                object_class,
                search_text=search_text,
                fields=fields,
                sort_asc=sort_asc,
                limit=limit,
                offset=offset,
            )
        except AlloyApiError as exc:
            return map_status_to_error(exc.status_code, exc.message)
        except _NotConfigured:
            return error_envelope("not_configured", "No Alloy Navigator credentials in request context.", retryable=False)
        return _dump(result)

    @mcp.tool(
        name="alloy_get_object",
        annotations=_READ_ONLY,
        description=(
            "Get one Alloy Navigator object's full detail by id.\n"
            "Args: oid (required, the object id)."
        ),
    )
    async def alloy_get_object(oid: str) -> str:
        try:
            client = require_client()
            result = await client.get_object(oid)
        except AlloyApiError as exc:
            return map_status_to_error(exc.status_code, exc.message)
        except _NotConfigured:
            return error_envelope("not_configured", "No Alloy Navigator credentials in request context.", retryable=False)
        return _dump(result)

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
        try:
            client = require_client()
            result = await client.get_object_activities(oid, limit=limit, offset=offset)
        except AlloyApiError as exc:
            return map_status_to_error(exc.status_code, exc.message)
        except _NotConfigured:
            return error_envelope("not_configured", "No Alloy Navigator credentials in request context.", retryable=False)
        return _dump(result)

    @mcp.tool(
        name="alloy_get_dictionary",
        annotations=_READ_ONLY,
        description=(
            "Resolve an Alloy Navigator dictionary (e.g. incident priorities) to its code-to-label values.\n"
            "Args: dictionary_name (required)."
        ),
    )
    async def alloy_get_dictionary(dictionary_name: str) -> str:
        try:
            client = require_client()
            result = await client.get_dictionary(dictionary_name)
        except AlloyApiError as exc:
            return map_status_to_error(exc.status_code, exc.message)
        except _NotConfigured:
            return error_envelope("not_configured", "No Alloy Navigator credentials in request context.", retryable=False)
        return _dump(result)


class _NotConfigured(Exception):
    """Internal signal only -- never leaves register(); mapped to the
    not_configured error envelope above."""
