"""Non-credential runtime configuration.

Credentials are never configured here -- they arrive per request as HTTP
headers (see server.py). This module only covers how the process itself
listens, per PRD-21267 tech design section "Data & API contract".
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level settings. Unknown env vars must be ignored, not fatal."""

    model_config = SettingsConfigDict(extra="ignore")

    mcp_http_host: str = "0.0.0.0"
    mcp_http_port: int = 8080


settings = Settings()
