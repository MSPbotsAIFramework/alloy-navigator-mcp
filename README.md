# alloy-navigator-mcp

MCP server for [Alloy Navigator](https://www.alloysoftware.com/), a self-hosted IT asset
management / ITSM product, built for the MSPbots Agent platform.

It gives an Agent read access to a tenant's Alloy Navigator instance: list and fetch
objects of any object class (Incidents, Computers, Work Orders, or any tenant-defined
custom class), read an object's activity/history log, and resolve dictionary
(classification) code-to-label values. This is a read-only connector -- there are no
create/update/delete/run-action tools in this version (see "Scope" below).

- Transport: MCP Streamable HTTP (`POST /mcp`), stateless, one container instance can
  serve multiple tenants concurrently.
- Stack: Python 3.12, [FastMCP](https://github.com/modelcontextprotocol/python-sdk), `httpx`.
- License: Apache-2.0. Copyright MSPbots.ai.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

Every request to `/mcp` must carry these three headers. All three are required; the
service returns `401` naming any that are missing, and never falls back to an
environment variable.

| Header | Required | Meaning | Where to get it |
|---|---|---|---|
| `X-Alloy-Base-Url` | Yes | The tenant's own Alloy Navigator API URL, e.g. `https://navigator.example.com/api` (every instance is self-hosted/per-customer -- there is no single MSPbots-wide endpoint) | The tenant's Alloy Navigator administrator |
| `X-Alloy-Client-Id` | Yes | OAuth2 `client_credentials` Application ID | Alloy Navigator -> Settings -> Services -> API -> Applications |
| `X-Alloy-Client-Secret` | Yes | OAuth2 `client_credentials` Application Secret, issued alongside the Application ID | Same screen as above |

The service exchanges the Application ID/Secret for a bearer access token against the
tenant's own base URL on every tool call (no token caching across requests -- see
"Scope" below), then calls the corresponding Alloy Navigator endpoint with that token.

## Tools

All four tools are read-only (`readOnlyHint=True`); there is no write path in this version.

| Tool | Args | Returns |
|---|---|---|
| `alloy_list_objects` | `object_class` (required), `search_text`, `fields`, `sort_asc`, `limit` (default 50, max 200), `offset` | `{"objects": [...], "has_more": bool}` |
| `alloy_get_object` | `oid` (required) | The object's detail fields |
| `alloy_get_object_activities` | `oid` (required), `limit` (default 50, max 200), `offset` | `{"activities": [...], "has_more": bool}` |
| `alloy_get_dictionary` | `dictionary_name` (required) | `{"values": [{"code": ..., "label": ...}, ...]}` |

Errors are a structured envelope: `{"error": {"code", "message", "retryable"}}`, with
`code` one of `not_configured` / `unauthorized` / `not_found` / `invalid_argument` /
`rate_limited` / `upstream_error`.

## Scope

This is a v1, read-only connector: no create/update/delete/run-action tools, no
attachment download/upload, no SSO/user-level login path (service-account
`client_credentials` only). No token is cached across requests -- every tool call mints
its own token, trading a small amount of latency for a simple multi-tenant isolation
story. See `docs/tickets/PRD-21267.md` for the full product and technical design,
including the option analysis behind these choices.

## Local development

```bash
uv sync --group dev
uv run pytest
```

Run the server locally (reads no credentials of its own -- pass your own test tenant's
headers on each request):

```bash
uv run python -m alloy_mcp
# or: docker compose up --build
```

```bash
curl -s -X POST http://localhost:8080/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'X-Alloy-Base-Url: https://your-tenant.example.com/api' \
  -H 'X-Alloy-Client-Id: your_client_id' \
  -H 'X-Alloy-Client-Secret: your_client_secret' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
