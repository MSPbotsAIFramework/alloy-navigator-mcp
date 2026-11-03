"""Shared test fixtures.

All tests mock Alloy Navigator over HTTP with respx -- no network, no real
vendor account, per vendor-mcp-development SOP section 10 (mock the API).
"""

from __future__ import annotations

import pytest

from alloy_mcp import api_client as api_client_module
from alloy_mcp import server as server_module
from alloy_mcp.api_client import AlloyCredentials

TEST_BASE_URL = "https://tenant-a.example.com/api"


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry/backoff tests (429/5xx) would otherwise sleep for real between
    attempts; zero the backoff so the suite stays fast (SOP section 10)."""
    monkeypatch.setattr(api_client_module, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))


@pytest.fixture
def credentials() -> AlloyCredentials:
    return AlloyCredentials(base_url=TEST_BASE_URL, client_id="client-a", client_secret="secret-a")


@pytest.fixture
def mcp_with_credentials(credentials: AlloyCredentials):
    """A fresh FastMCP instance with this request's credentials already set
    in the same ContextVar the real middleware uses -- bypasses the HTTP
    layer so tool-level tests stay fast and focused on tool behavior."""
    mcp = server_module.build_mcp()
    token = server_module._credentials_var.set(credentials)
    try:
        yield mcp
    finally:
        server_module._credentials_var.reset(token)
