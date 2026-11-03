"""TC-ALY-006 / AC5 (missing header -> 401) and TC-ALY-009 / AC8 (concurrent
tenant isolation) -- the two properties the credential middleware exists
to guarantee (vendor-mcp-development SOP section 3.3)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from alloy_mcp.server import (
    HEADER_BASE_URL,
    HEADER_CLIENT_ID,
    HEADER_CLIENT_SECRET,
    CredentialMiddleware,
    create_app,
    current_client,
)

FULL_HEADERS = {
    HEADER_BASE_URL: "https://tenant-a.example.com/api",
    HEADER_CLIENT_ID: "client-a",
    HEADER_CLIENT_SECRET: "secret-a",
}


async def test_missing_credential_header_returns_401_with_header_names():
    app = create_app()
    headers = dict(FULL_HEADERS)
    del headers[HEADER_CLIENT_SECRET]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response.status_code == 401
    assert HEADER_CLIENT_SECRET in response.json()["error"]
    # Only the missing header is named -- headers that were present should not
    # also be reported as missing.
    assert HEADER_CLIENT_ID not in response.json()["error"]


async def test_all_headers_missing_lists_all_three():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response.status_code == 401
    body = response.json()["error"]
    assert all(name in body for name in (HEADER_BASE_URL, HEADER_CLIENT_ID, HEADER_CLIENT_SECRET))


async def _echo_client_id(request: Request) -> JSONResponse:
    client = current_client()
    before = client.credentials.client_id if client else None
    # Yield control so a concurrently-running request's middleware has a
    # chance to run its own set/reset cycle while this one is still "in
    # flight" -- this is the race AC8 exists to rule out.
    await asyncio.sleep(0.05)
    client_after = current_client()
    after = client_after.credentials.client_id if client_after else None
    return JSONResponse({"before": before, "after": after})


def _isolation_test_app() -> Starlette:
    app = Starlette(routes=[Route("/mcp", _echo_client_id, methods=["POST"])])
    app.add_middleware(CredentialMiddleware)
    return app


async def test_concurrent_requests_do_not_leak_credentials_across_tenants():
    app = _isolation_test_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        async def call(client_id: str) -> dict:
            headers = {
                HEADER_BASE_URL: f"https://{client_id}.example.com/api",
                HEADER_CLIENT_ID: client_id,
                HEADER_CLIENT_SECRET: f"secret-{client_id}",
            }
            response = await client.post("/mcp", headers=headers)
            return response.json()

        result_a, result_b = await asyncio.gather(call("tenant-a"), call("tenant-b"))

    assert result_a == {"before": "tenant-a", "after": "tenant-a"}
    assert result_b == {"before": "tenant-b", "after": "tenant-b"}
