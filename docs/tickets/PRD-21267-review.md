# PRD-21267 · Code review

## Round 1 · 2026-11-03 · reviewed dev/PRD-21267 @ 42351b3 (base main @ e3da08a)

Verdict: changes requested

### Findings

1. [must-fix] `src/alloy_mcp/server.py`, `src/alloy_mcp/api_client.py`, `src/alloy_mcp/tools/objects.py` — no logging of any kind is implemented anywhere in the service (`grep -rn "log\b\|import logging\|getLogger" src/` returns zero matches). The tech design's own "Non-functional → Observability" section (`docs/tickets/PRD-21267.md`) states as a present-tense fact: "structured logs (tool name, `object_class`/`oid` param summary, Alloy Navigator HTTP status, latency, error `code`) per request-id; never log header values or response bodies verbatim" — but nothing in the code emits a log line, structured or otherwise. This is also an explicit, named line item in the vendor-mcp-development SOP's pre-delivery checklist (§14 "日志与敏感信息（§6、§11）": "结构化日志带 request-id；不打印凭据/token/敏感业务数据"), and `mcp-track.md` §4.3 lists "SOP §14 Checklist" as one of the review's must-fix criteria. Fix: add structured (JSON or key=value) per-request logging per SOP §6 — tool name, a redacted param summary, the upstream HTTP status, latency, and the error `code` on failure — with a request-id for correlation, and never log the three credential headers or raw response bodies. Either implement it, or if it is being deliberately deferred, say so explicitly in the tech design (the way the token-cache and write-tool deferrals already are) instead of describing it as already done.

2. [suggestion] `src/alloy_mcp/api_client.py:148,153,158,163` — `object_class`/`oid`/`dictionary_name` are interpolated directly into the request path (e.g. `f"/api/v2/{object_class}"`). httpx percent-encodes spaces, so ordinary class names like `"Work Orders"` are fine, but it also normalizes `..` segments in the path (confirmed locally: `httpx.URL(base + "/api/v2/" + "../../token")` collapses to `.../token`), so a crafted `object_class` or `oid` could reach other `GET` endpoints under the same base URL beyond the intended `/api/v2/<class>` family. Low severity — the HTTP method is always `GET`, the request still carries only that same tenant's own credentials, and the tenant's Alloy Navigator Application account already gates what those credentials can see — but it's inconsistent with treating `object_class` as a trusted-looking enum-like parameter. Consider rejecting `/` and `..` in these three parameters as a defense-in-depth check, with a clear `invalid_argument` error.

3. [suggestion] `src/alloy_mcp/api_client.py:85-108` — the comment above `_MAX_RETRIES`/`_RETRY_BACKOFF_SECONDS` (lines 30-32) says retries are "on 429/5xx only, with backoff", but `_request`'s `except httpx.TimeoutException` / `except httpx.HTTPError` branches (lines 94-97) have no `continue`/`return`/`raise` of their own — execution falls through to the end of the `for` loop body and the loop simply tries again on the next iteration, up to `_MAX_RETRIES + 1` total attempts, with no backoff between those particular retries (the `await asyncio.sleep(...)` call only happens on the 429/5xx path). This doesn't fail any AC (TC-ALY-010's timeout case still ends in `upstream_error`/`retryable=true` regardless of how many silent retries happened first) and is arguably more resilient than documented, not less, but the comment overstates what's guaranteed and could mislead a future maintainer into assuming transport-level failures are never retried. Either add the same backoff to this path or adjust the comment to describe the actual behavior.

## Self-test verification (spot-checked independently, not re-trusting the ticket's claims)

- `uv` is not installed on this review host; ran the repo's own committed `.venv` instead (python3.12, pytest 8.4.2): `.venv/bin/pytest -v` → **15 passed in 0.29s**, matching the Self-test section exactly.
- Chinese-text scan re-run (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ .`): no output — clean.
- `sudo docker build --platform linux/amd64 -t alloy-navigator-mcp:review .` → builds clean.
- Ran the built image (`docker run -d -p 18281:8080 ...`): `GET /health` → 200; `POST /mcp` with no credential headers → 401; `POST /mcp tools/list` with all three headers and no prior `initialize` → 200 with all 4 tools listed (confirms `stateless_http=True`). All match the Self-test section's claims.
- README "MSPbots Connector" section diffed byte-for-byte against `reference/readme-connector-section.md` (via `vendor-mcp-development` skill) — identical.
- `gitleaks` is not installed on this review host; not independently re-run (the ticket's own claim of "no leaks found" was not verified here, but a manual read of `api_client.py`/`server.py` found no hardcoded secrets, and `README.md`/`docker-compose.yml` use only placeholder credential values).

## Other review criteria checked (`legs/review.md` R3 table + `mcp-track.md` §4.3), no issues found

- Credentials only from the three declared headers, never cached/shared across requests (per-request `ContextVar`, reset in `finally`); `AC8`'s concurrent-tenant test exercises this directly and passes.
- Tool names/params/returns/errors match the tech design; errors are the fixed structured envelope (`code`/`message`/`retryable`), never a raised exception reaching the Agent.
- Self-test section shows tests run on the synced-with-default-branch commit (`main` had not moved, so no sync was needed) and includes a real `tools/list` result.
- README Connector section and credential header table match the code (`X-Alloy-Base-Url`/`X-Alloy-Client-Id`/`X-Alloy-Client-Secret` declared and read identically); no Chinese text anywhere in the repo.
- No cross-repo header/field compatibility concerns — single-repo ticket, `Release order: any`.
- Scope matches the tech design's change list; no unrelated files touched; this is a greenfield repo so there is no pre-existing code to clean up or reuse.
- No schema/migration involved (stateless service, no database).
