"""HTTP-level MCP bearer authentication."""

from __future__ import annotations

from starlette.testclient import TestClient

from tests.conftest import TEST_INGEST_KEY, TEST_MCP_KEY, TEST_READ_KEY, assert_no_secrets


def test_missing_authorization_returns_401(client: TestClient):
    resp = client.post("/mcp", json={})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["message"] == "Invalid or missing MCP credentials"
    assert "request_id" in body
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_malformed_authorization_returns_401(client: TestClient):
    resp = client.post(
        "/mcp",
        headers={"Authorization": "Basic abc"},
        json={},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_bearer_without_token_returns_401(client: TestClient):
    resp = client.post(
        "/mcp",
        headers={"Authorization": "Bearer"},
        json={},
    )
    assert resp.status_code == 401


def test_wrong_mcp_key_returns_401(client: TestClient):
    resp = client.post(
        "/mcp",
        headers={"Authorization": "Bearer wrong-key"},
        json={},
    )
    assert resp.status_code == 401
    assert "wrong-key" not in resp.text
    assert_no_secrets(resp.text)


def test_read_api_key_does_not_authenticate_mcp(client: TestClient):
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {TEST_READ_KEY}"},
        json={},
    )
    assert resp.status_code == 401
    assert_no_secrets(resp.text)


def test_ingest_api_key_does_not_authenticate_mcp(client: TestClient):
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {TEST_INGEST_KEY}"},
        json={},
    )
    assert resp.status_code == 401
    assert_no_secrets(resp.text)


def test_correct_key_allows_mcp_protocol_handling(
    client: TestClient, mcp_headers: dict[str, str]
):
    resp = client.post(
        "/mcp",
        headers={
            **mcp_headers,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "health-db-tests", "version": "0.0.1"},
            },
        },
    )
    assert resp.status_code == 200
    assert_no_secrets(resp.text)
    assert TEST_MCP_KEY not in resp.text


def test_authenticated_http_lists_four_tools(client: TestClient, mcp_headers: dict[str, str]):
    headers = {
        **mcp_headers,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-03-26",
    }
    init = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "health-db-tests", "version": "0.0.1"},
            },
        },
    )
    assert init.status_code == 200
    listed = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert "result" in payload
    names = [tool["name"] for tool in payload["result"]["tools"]]
    assert names == [
        "get_data_coverage",
        "get_glucose_series",
        "get_glucose_summary",
        "get_meals",
    ]


def test_401_body_is_safe_and_has_www_authenticate(client: TestClient):
    supplied = "super-secret-wrong-key-xyz"
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {supplied}"},
        json={"unexpected": True},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert set(body.keys()) == {"error", "request_id"}
    assert set(body["error"].keys()) == {"code", "message"}
    assert supplied not in resp.text
    assert "traceback" not in resp.text.lower()
    assert "query-api.test" not in resp.text
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_missing_origin_is_allowed(client: TestClient, mcp_headers: dict[str, str]):
    resp = client.post(
        "/mcp",
        headers={
            **mcp_headers,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "health-db-tests", "version": "0.0.1"},
            },
        },
    )
    assert resp.status_code not in {401, 403, 421}


def test_unexpected_origin_is_rejected(client: TestClient, mcp_headers: dict[str, str]):
    resp = client.post(
        "/mcp",
        headers={
            **mcp_headers,
            "Origin": "https://evil.example",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert resp.status_code == 403


def test_no_permissive_cors_star(client: TestClient):
    resp = client.options(
        "/mcp",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") != "*"
