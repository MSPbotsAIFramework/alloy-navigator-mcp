"""Alloy Navigator ITSM API client.

Per tech design section "Auth flow": every tool call mints its own OAuth2
client_credentials bearer token against the tenant's own base URL, then
calls the requested endpoint. No token is cached across requests -- this is
the deliberate v1 trade-off documented in the ticket's Open Questions (one
extra outbound call per tool invocation, in exchange for a trivially simple
multi-tenant isolation story -- see AC8).

One AlloyClient instance is constructed per request from that request's
credential headers (see server.py's ContextVar-based middleware) and is
never reused across requests or cached anywhere.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from alloy_mcp.errors import AlloyApiError

# SOP section 5: outbound connect/read timeouts.
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 30.0
_TIMEOUT = httpx.Timeout(connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT, write=_READ_TIMEOUT, pool=_READ_TIMEOUT)

# SOP section 5: limited retries on 429/5xx only, with backoff.
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = (1.0, 2.0)


@dataclass(frozen=True)
class AlloyCredentials:
    """The three per-tenant values read from request headers. Never persisted."""

    base_url: str
    client_id: str
    client_secret: str


class AlloyClient:
    """Thin wrapper over Alloy Navigator's generic object-class REST API.

    Constructed fresh per tool call with one httpx.AsyncClient (connection
    reuse within the call, per SOP section 5), and discarded afterwards.
    """

    def __init__(self, credentials: AlloyCredentials) -> None:
        # Public: this is a transport-parameter holder, not a secret store by
        # itself -- isolation is enforced by never sharing an AlloyClient
        # instance across requests (see server.py's per-request ContextVar).
        self.credentials = credentials
        # httpx normalizes a trailing slash inconsistently across versions;
        # strip it once here so every endpoint below can join with "/...".
        self._base_url = credentials.base_url.rstrip("/")
        # Set on a successful response in `_request`; read by the tools layer
        # for per-call structured logging (SOP section 6). On failure the
        # status is already on the raised AlloyApiError, so this is only
        # meaningful after a call that returned normally.
        self.last_status_code: int | None = None

    async def _mint_token(self, http: httpx.AsyncClient) -> str:
        try:
            response = await http.post(
                f"{self._base_url}/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.credentials.client_id,
                    "client_secret": self.credentials.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.TimeoutException as exc:
            raise AlloyApiError(None, f"Timed out requesting an access token: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AlloyApiError(None, f"Could not reach Alloy Navigator to request a token: {exc}") from exc

        if response.status_code >= 400:
            raise AlloyApiError(response.status_code, "Alloy Navigator rejected the client_credentials token request")

        body = response.json()
        token = body.get("access_token")
        if not token:
            raise AlloyApiError(response.status_code, "Alloy Navigator's token response had no access_token")
        return str(token)

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            token = await self._mint_token(http)
            headers = {"Authorization": f"bearer {token}"}

            last_error: AlloyApiError | None = None
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = await http.request(method, f"{self._base_url}{path}", params=params, headers=headers)
                except httpx.TimeoutException as exc:
                    last_error = AlloyApiError(None, f"Request to Alloy Navigator timed out: {exc}")
                except httpx.HTTPError as exc:
                    last_error = AlloyApiError(None, f"Could not reach Alloy Navigator: {exc}")
                else:
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES:
                        last_error = AlloyApiError(response.status_code, response.text[:500])
                        await asyncio.sleep(self._retry_delay(response, attempt))
                        continue
                    if response.status_code >= 400:
                        raise AlloyApiError(response.status_code, self._summarize_error(response))
                    self.last_status_code = response.status_code
                    return response.json() if response.content else None

            assert last_error is not None
            raise last_error

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        """SOP section 5: respect Retry-After on 429/5xx when the vendor sends
        one; otherwise fall back to the fixed exponential backoff table."""
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass  # Retry-After as an HTTP-date is not expected from this vendor; fall through.
        return _RETRY_BACKOFF_SECONDS[attempt]

    @staticmethod
    def _summarize_error(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:500]
        message = body.get("message") or body.get("error") or body
        return f"Alloy Navigator returned {response.status_code}: {message}"[:500]

    async def list_objects(
        self,
        object_class: str,
        *,
        search_text: str | None = None,
        fields: list[str] | None = None,
        sort_asc: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"par_limit": limit, "par_offset": offset}
        if search_text:
            params["par_search_text"] = search_text
        if fields:
            params["par_fields"] = ",".join(fields)
        if sort_asc:
            params["par_sort_asc"] = sort_asc
        result = await self._request("GET", f"/api/v2/{object_class}", params=params)
        objects = result if isinstance(result, list) else result.get("objects", []) if result else []
        return {"objects": objects, "has_more": len(objects) == limit}

    async def get_object(self, oid: str) -> dict[str, Any]:
        result = await self._request("GET", f"/api/v2/object/{oid}")
        return result or {}

    async def get_object_activities(self, oid: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        params = {"par_limit": limit, "par_offset": offset}
        result = await self._request("GET", f"/api/v2/Activities/{oid}", params=params)
        activities = result if isinstance(result, list) else result.get("activities", []) if result else []
        return {"activities": activities, "has_more": len(activities) == limit}

    async def get_dictionary(self, dictionary_name: str) -> dict[str, Any]:
        result = await self._request("GET", "/api/v2/Dictionary", params={"par_name": dictionary_name})
        values = result if isinstance(result, list) else result.get("values", []) if result else []
        return {"values": values}
