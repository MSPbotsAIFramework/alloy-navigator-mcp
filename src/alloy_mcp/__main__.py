"""Process entry point: run the Streamable HTTP server with uvicorn."""

from __future__ import annotations

import logging

import uvicorn

from alloy_mcp.config import settings
from alloy_mcp.server import create_app


def main() -> None:
    # Root logger: emits the per-tool-call structured JSON lines (SOP section
    # 6 / tools/objects.py's `_log_call`) to stdout. One line per record --
    # the message itself is already JSON, so no extra formatting is added.
    # Set before uvicorn.run() so its own dictConfig (disable_existing_loggers
    # is False) does not clobber this.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(create_app(), host=settings.mcp_http_host, port=settings.mcp_http_port)


if __name__ == "__main__":
    main()
