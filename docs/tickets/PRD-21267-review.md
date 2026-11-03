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

### Fixes (round 1)

1. [must-fix] Structured per-tool-call logging — implemented.
   - `src/alloy_mcp/tools/objects.py`: added `_log_call` (one compact JSON line per tool invocation to `logging.getLogger("alloy_mcp")`) and an `_execute` helper that every one of the four tools now routes through. Each line carries a `request_id` (generated per call, for correlation), `tool`, a `params` summary limited to `object_class`/`oid`/`dictionary_name` plus pagination (`limit`/`offset`) — `search_text`/`fields`/`sort_asc` are deliberately excluded as free-text, and credential headers are never read on this path at all — the upstream HTTP `status`, `latency_ms`, and (on failure) the mapped `error_code`.
   - `src/alloy_mcp/api_client.py`: `AlloyClient` now tracks `self.last_status_code`, set on every successful response, so the tools layer can log the upstream status without re-deriving it; failures already carry their status on the raised `AlloyApiError`.
   - `src/alloy_mcp/errors.py`: factored `status_to_code` out of `map_status_to_error` so the log line and the returned error envelope use the exact same status→code mapping instead of two copies of the same logic (no behavior change — `map_status_to_error` calls `status_to_code` internally and keeps producing byte-identical envelopes).
   - Refactoring the four tool handlers onto one shared `_execute` (previously each had its own duplicated try/except) was necessary to add this consistently without 4x duplicating the logging/timing code; it is not a behavior change, confirmed by the unchanged test result below.
   - `src/alloy_mcp/__main__.py`: added `logging.basicConfig(level=logging.INFO, format="%(message)s")` before `uvicorn.run(...)`. Without this, the `alloy_mcp` logger's INFO records would be silently dropped under Python's default logging config (no handler on the root logger below WARNING) — confirmed uvicorn's own internal `dictConfig` call does not touch the root logger (`disable_existing_loggers=False`, no `"root"` key in its `LOGGING_CONFIG`), so this survives `uvicorn.run()`.
   - Retest on this commit: `.venv/bin/pytest -v` → **15 passed** (same count/names as round 1's self-test — no regression).
   - Independently verified the log lines themselves with an ad-hoc script (`~/.claude/skills/mb-ticket-pipeline/scratch/PRD-21267/verify_logging.py`, not part of this repo): two tool calls through `mcp.call_tool` (one upstream 200, one upstream 404) each produced exactly one JSON log line with all six fields (`request_id`/`tool`/`params`/`status`/`latency_ms`/`error_code`), `status`/`error_code` matched the actual outcome, and a planted fake credential secret did not appear in either line.
   - Also reran the full ASGI smoke path with the real lifespan (`.../scratch/PRD-21267/verify_smoke.py`, `starlette.testclient.TestClient`): `GET /health` → 200; `POST /mcp` with no credential headers → 401; `POST /mcp initialize` with all three headers → 200 — unchanged from round 1's claims.
   - Chinese-text scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ .`) rerun: the only hit is this review document's own round-1 finding #1, which quotes the vendor-mcp-development SOP's Chinese section title verbatim as evidence — nothing in `src/`, `README.md`, `tests/`, or any other file. That quoted text is round 1's frozen finding and is not edited here.
   - `gitleaks` is still not installed on this host (same gap round 1 noted); a manual read of every changed file found no hardcoded secrets or tokens.

2. [suggestion] `object_class`/`oid`/`dictionary_name` path-traversal hardening — not changed this round. It was marked a suggestion, not a must-fix; left as-is per the review-rework rule of only changing listed must-fix items.
3. [suggestion] retry/backoff comment accuracy on the transport-error fallthrough — not changed this round, same reason as #2.

Verdict after fixes: must-fix #1 addressed; suggestions #2/#3 intentionally left open for a future round or ticket.

## Round 2 · 2026-11-03 · reviewed dev/PRD-21267 @ 7238dc5 (base main @ e3da08a)

Verdict: approved

### Round 1 must-fix re-check

1. [must-fix #1, round 1] Structured per-tool-call logging — **confirmed fixed**, re-verified independently on this host (not just re-reading the round-1 Fixes writeup):
   - `git diff 42351b3..7238dc5 -- src/` shows exactly the change the Fixes section describes: `_log_call`/`_execute` in `tools/objects.py`, `last_status_code` in `api_client.py`, `status_to_code` factored out in `errors.py`, `logging.basicConfig(...)` in `__main__.py`. No test files changed (tool signatures/params did not change, so `mcp-track.md` §4.3's "tools/params 改了，测试跟着改" bullet does not apply here).
   - `.venv/bin/pytest -v` on `7238dc5` → **15 passed in 0.29s**, same 15 names as round 1 — no regression.
   - Re-ran the Chinese-text scan myself (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ .`): the only hit is this review file's own round-1 finding text (quotes the SOP's Chinese section title as evidence) — nothing in `src/`, `README.md`, or `tests/`. Matches the claim, not just trusted it.
   - `sudo docker build -t alloy-navigator-mcp:review2 .` → builds clean. Ran the image (`-p 18901:8080`, the app listens on 8080 not 8000 — confirmed from `Dockerfile`/`uvicorn` output): `GET /health` → 200; `POST /mcp tools/list` with no credential headers → 401 naming all three headers; with all three headers and no `initialize` → 200 with all 4 tools (confirms `stateless_http=True`).
   - **Independently exercised the new logging end-to-end** (round 1 used an ad-hoc script outside the repo for this; I called the live container instead): `tools/call alloy_list_objects` against an unreachable fake `X-Alloy-Base-Url` with a planted secret value `SUPERSECRETVALUE123` in `X-Alloy-Client-Secret` → container log emitted exactly one line: `{"request_id":"...","tool":"alloy_list_objects","params":{"object_class":"Incidents","limit":50,"offset":0},"status":null,"latency_ms":19.4,"error_code":"upstream_error"}`. `docker logs | grep` for the planted secret value: zero matches. Confirms the log line shape (6 fields), the params redaction (`object_class`/pagination only, no `search_text`/`fields`/`sort_asc`), and that the credential header value never reaches the log, under an actual failing call rather than only the happy path.
   - README "MSPbots Connector" section re-diffed against `reference/readme-connector-section.md`: byte-identical (confirmed the apparent mismatch in my first diff attempt was my own section-boundary slicing, not a real difference).
   - `ac.json`: 10/11 cases `pass`, `TC-ALY-011` `untested` with a documented reason (needs a real tenant account) — matches the `[DEV]` stub, and "no vendor test account" is explicitly not a blocker per `mcp-track.md` §3's completion standard.

### New findings this round

4. [suggestion] `src/alloy_mcp/tools/objects.py:101-106` — `_execute`'s inner `try` only catches `AlloyApiError`; any other exception raised inside a tool's `call(client)` (e.g. an unexpected bug, not an upstream HTTP condition) propagates without going through `_log_call`, so that one request-id never appears in the logs despite the module's docstring claiming "every tool gets the same logging shape ... for free". Pre-existing shape (the four tools never caught bare exceptions before round 1 either), not introduced by this round's fix, and no AC exercises this path, so not a must-fix. Consider a bare `except Exception` fallback that logs `error_code="internal_error"` and re-raises, so the observability story in the tech design's "Non-functional" section holds even for bugs, not just mapped vendor errors.

No other issues found; suggestions #2 and #3 from round 1 remain open and unaddressed, as expected (review-rework scope).

Status: frozen @ 7238dc5a6a2c474dd07f613ab9fd976b57226f08
