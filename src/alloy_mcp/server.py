"""FastMCP service factory and request-scoped credential middleware.

Implements the platform's three non-negotiable rules (vendor-mcp-development
SOP section 3): credentials only from headers, request-level isolation via
contextvars (never a global/module-level credential store), and a stateless
service (stateless_http=True, no session stickiness).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from alloy_mcp.api_client import AlloyClient, AlloyCredentials
from alloy_mcp.tools import objects as objects_tools

# Header names, declared verbatim in README.md's "Authentication headers"
# table. Code and docs must match character-for-character (SOP section 3.1).
HEADER_BASE_URL = "X-Alloy-Base-Url"
HEADER_CLIENT_ID = "X-Alloy-Client-Id"
HEADER_CLIENT_SECRET = "X-Alloy-Client-Secret"

_REQUIRED_HEADERS = (HEADER_BASE_URL, HEADER_CLIENT_ID, HEADER_CLIENT_SECRET)

# Request-scoped credential store. Set by the middleware below at the start
# of each request and reset in a finally block -- never a global/singleton
# (SOP section 3.3/3.4; this is what AC8's concurrent-tenant test exercises).
_credentials_var: ContextVar[AlloyCredentials | None] = ContextVar("alloy_credentials", default=None)


def current_client() -> AlloyClient | None:
    """Build a fresh AlloyClient from this request's credentials.

    Returns None if called outside of a request that passed the middleware
    (should not happen in practice -- tools map that case to not_configured).
    """
    creds = _credentials_var.get()
    if creds is None:
        return None
    return AlloyClient(creds)


class CredentialMiddleware:
    """Raw ASGI middleware: validates the three required headers on /mcp
    requests before the MCP app ever sees them, and resets the contextvar
    after the request completes so no state leaks to the next request on
    this same connection/worker (SOP section 3.3).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            await self._app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        missing = [name for name in _REQUIRED_HEADERS if not headers.get(name.lower())]
        if missing:
            response: Response = JSONResponse(
                {"error": "missing required header(s): " + ", ".join(missing)},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        credentials = AlloyCredentials(
            base_url=headers[HEADER_BASE_URL.lower()],
            client_id=headers[HEADER_CLIENT_ID.lower()],
            client_secret=headers[HEADER_CLIENT_SECRET.lower()],
        )
        token = _credentials_var.set(credentials)
        try:
            await self._app(scope, receive, send)
        finally:
            _credentials_var.reset(token)


_INSTRUCTIONS = """\
Alloy Navigator is a self-hosted IT asset / ITSM product. Each tenant runs
its own instance; its object model (Incidents, Computers, Work Orders, and
any tenant-defined custom classes) is generic -- the same four tools work
against whichever object classes that tenant's instance defines.

Typical flow: call alloy_list_objects to find candidates (filter with
search_text), then alloy_get_object for one record's full detail, and
alloy_get_object_activities for its history/notes. Use alloy_get_dictionary
to turn a coded field (e.g. a priority or status code) into its human label.

All four tools are read-only; there is no write/create/delete capability in
this version.
"""


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        name="alloy-navigator-mcp",
        instructions=_INSTRUCTIONS,
        stateless_http=True,
        # The service sits behind the Gateway's reverse proxy, not on the
        # public internet directly -- DNS-rebinding protection would reject
        # the proxy-forwarded Host header (SOP section 7).
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    objects_tools.register(mcp, current_client)
    return mcp


async def health(_: Request) -> Response:
    """Pure local liveness probe -- must never depend on Alloy Navigator
    being reachable (SOP section 1.1)."""
    return JSONResponse({"status": "ok"})


def create_app() -> Starlette:
    mcp = build_mcp()
    mcp_app = mcp.streamable_http_app()

    # Mount() does not run the child app's lifespan on its own -- it must be
    # wired to the outer Starlette app explicitly, or /mcp fails with
    # "Task group is not initialized" while /health still reports 200
    # (SOP section 7, the most common new-connector regression).
    @asynccontextmanager
    async def lifespan(_: Starlette):
        async with mcp.session_manager.run():
            yield

    # mcp.streamable_http_app() already serves its own "/mcp" route
    # internally (FastMCP's default streamable_http_path) -- mount it at
    # "/" here, not "/mcp" again, or every request 404s at "/mcp/mcp".
    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(CredentialMiddleware)
    return app
