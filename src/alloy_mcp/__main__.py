"""Process entry point: run the Streamable HTTP server with uvicorn."""

from __future__ import annotations

import uvicorn

from alloy_mcp.config import settings
from alloy_mcp.server import create_app


def main() -> None:
    uvicorn.run(create_app(), host=settings.mcp_http_host, port=settings.mcp_http_port)


if __name__ == "__main__":
    main()
