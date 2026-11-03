"""tools/list snapshot + one test per AC covered by tests/test_tools.py in
the PRD-21267 change list (docs/tickets/PRD-21267.ac.json has the full
TC-ALY-xxx mapping)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from alloy_mcp.server import build_mcp
from tests.conftest import TEST_BASE_URL


async def _call(mcp, name: str, args: dict) -> dict:
    content, _ = await mcp.call_tool(name, args)
    return json.loads(content[0].text)


# --- TC-ALY snapshot: tool names + required params are an external contract
# (SOP section 13) -- a change here should break this test on purpose. ---


async def test_tools_list_snapshot():
    mcp = build_mcp()
    tools = await mcp.list_tools()
    by_name = {t.name: t for t in tools}

    assert set(by_name) == {
        "alloy_list_objects",
        "alloy_get_object",
        "alloy_get_object_activities",
        "alloy_get_dictionary",
    }
    assert by_name["alloy_list_objects"].inputSchema["required"] == ["object_class"]
    assert by_name["alloy_get_object"].inputSchema["required"] == ["oid"]
    assert by_name["alloy_get_object_activities"].inputSchema["required"] == ["oid"]
    assert by_name["alloy_get_dictionary"].inputSchema["required"] == ["dictionary_name"]
    for tool in tools:
        assert len(tool.description) <= 500, tool.name
        assert tool.annotations.readOnlyHint is True, tool.name


# --- TC-ALY-001 / AC1 ---


async def test_list_objects_returns_requested_fields_and_respects_limit(mcp_with_credentials):
    incidents = [{"ObjectID": f"INC-{i}", "Summary": f"issue {i}"} for i in range(10)]
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        mock.get(f"{TEST_BASE_URL}/api/v2/Incidents").respond(200, json=incidents)
        result = await _call(mcp_with_credentials, "alloy_list_objects", {"object_class": "Incidents", "limit": 10})
    assert len(result["objects"]) == 10
    assert result["objects"][0]["Summary"] == "issue 0"


# --- TC-ALY-002 / AC1 ---


async def test_list_objects_empty_result_is_not_an_error(mcp_with_credentials):
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        mock.get(f"{TEST_BASE_URL}/api/v2/Incidents").respond(200, json=[])
        result = await _call(
            mcp_with_credentials, "alloy_list_objects", {"object_class": "Incidents", "search_text": "no-match"}
        )
    assert result == {"objects": [], "has_more": False}
    assert "error" not in result


# --- TC-ALY-003 / AC2 ---


async def test_get_object_returns_single_object_detail(mcp_with_credentials):
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        mock.get(f"{TEST_BASE_URL}/api/v2/object/INC-1").respond(200, json={"ObjectID": "INC-1", "Summary": "disk full"})
        result = await _call(mcp_with_credentials, "alloy_get_object", {"oid": "INC-1"})
    assert result == {"ObjectID": "INC-1", "Summary": "disk full"}


# --- TC-ALY-004 / AC3 ---


async def test_get_object_activities_returns_history_entries(mcp_with_credentials):
    activities = [{"Date": "2026-11-01", "Note": "opened"}, {"Date": "2026-11-02", "Note": "assigned"}]
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        mock.get(f"{TEST_BASE_URL}/api/v2/Activities/INC-1").respond(200, json=activities)
        result = await _call(mcp_with_credentials, "alloy_get_object_activities", {"oid": "INC-1"})
    assert result["activities"] == activities


# --- TC-ALY-005 / AC4 ---


async def test_get_dictionary_returns_classification_values(mcp_with_credentials):
    values = [{"Code": 1, "Label": "Low"}, {"Code": 2, "Label": "High"}]
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        mock.get(f"{TEST_BASE_URL}/api/v2/Dictionary").respond(200, json=values)
        result = await _call(mcp_with_credentials, "alloy_get_dictionary", {"dictionary_name": "IncidentPriority"})
    assert result["values"] == values


# --- TC-ALY-007 / AC6 ---


async def test_list_objects_invalid_object_class_is_invalid_argument(mcp_with_credentials):
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        mock.get(f"{TEST_BASE_URL}/api/v2/Bogus").respond(400, json={"message": "Unknown object class 'Bogus'"})
        result = await _call(mcp_with_credentials, "alloy_list_objects", {"object_class": "Bogus"})
    assert result["error"]["code"] == "invalid_argument"
    assert result["error"]["retryable"] is False


# --- TC-ALY-008 / AC7 ---


async def test_get_object_unknown_oid_is_not_found(mcp_with_credentials):
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        mock.get(f"{TEST_BASE_URL}/api/v2/object/does-not-exist").respond(404, json={"message": "not found"})
        result = await _call(mcp_with_credentials, "alloy_get_object", {"oid": "does-not-exist"})
    assert result["error"]["code"] == "not_found"


# --- SOP section 5: 429 retries, honoring Retry-After, extra coverage beyond
# the AC wording (rate_limited only becomes visible to the Agent if every
# retry is also rate-limited). ---


async def test_rate_limit_retries_and_honors_retry_after(mcp_with_credentials, monkeypatch):
    sleeps: list[float] = []
    real_sleep = __import__("asyncio").sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    import alloy_mcp.api_client as api_client_module

    monkeypatch.setattr(api_client_module.asyncio, "sleep", fake_sleep)

    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        route = mock.get(f"{TEST_BASE_URL}/api/v2/Incidents")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "7"}, json={"message": "slow down"}),
            httpx.Response(200, json=[{"ObjectID": "INC-1"}]),
        ]
        result = await _call(mcp_with_credentials, "alloy_list_objects", {"object_class": "Incidents"})

    assert result["objects"] == [{"ObjectID": "INC-1"}]
    assert sleeps == [7.0]


# --- TC-ALY-010 / AC9 (5xx case) ---


async def test_upstream_5xx_or_timeout_is_upstream_error(mcp_with_credentials):
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        mock.get(f"{TEST_BASE_URL}/api/v2/Incidents").respond(503)
        result = await _call(mcp_with_credentials, "alloy_list_objects", {"object_class": "Incidents"})
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["retryable"] is True


# --- TC-ALY-010 / AC9 (timeout case) ---


async def test_upstream_timeout_is_upstream_error(mcp_with_credentials):
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(200, json={"access_token": "tok"})
        mock.get(f"{TEST_BASE_URL}/api/v2/Incidents").mock(side_effect=httpx.ConnectTimeout("timed out"))
        result = await _call(mcp_with_credentials, "alloy_list_objects", {"object_class": "Incidents"})
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["retryable"] is True


# --- Token mint rejected -> unauthorized, matches errors.py's mapping table ---


async def test_token_mint_rejected_is_unauthorized(mcp_with_credentials):
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{TEST_BASE_URL}/token").respond(401, json={"error": "invalid_client"})
        result = await _call(mcp_with_credentials, "alloy_get_object", {"oid": "INC-1"})
    assert result["error"]["code"] == "unauthorized"
