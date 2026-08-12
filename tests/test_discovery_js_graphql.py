"""Unit tests for the JS bundle endpoint mining and GraphQL introspection module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.endpoint_registry import EndpointRegistry
from src.discovery.js_graphql import (
    execute_graphql_introspection,
    mine_endpoints_from_js,
    parse_graphql_schema,
    process_js_endpoint_mining,
)


def test_mine_endpoints_from_js():
    js_code = """
    // Common configurations
    const config = {
        endpoint: "/api/v1/config",
        url: '/api/v2/user-settings',
        otherUrl: "https://external.com/api"
    };

    // Ajax call triggers
    fetch('/api/v3/feed');
    axios.get("/api/v1/profile");
    axios.post(`/api/v2/create-item`);

    // Hardcoded route variables
    const metricsPath = "/internal/metrics";
    const debugUrl = "/debug/details";
    const graphQLUrl = "/graphql";
    const oauthUrl = "/oauth/callback";
    const loginUrl = "/login";
    const logoutUrl = "/logout";
    const healthUrl = "/health";

    // Non-matching strings
    const random = "just some random text";
    const relativeText = "./relative/file.html";
    """

    paths = mine_endpoints_from_js(js_code)

    assert "/api/v1/config" in paths
    assert "/api/v2/user-settings" in paths
    assert "/api/v3/feed" in paths
    assert "/api/v1/profile" in paths
    assert "/api/v2/create-item" in paths
    assert "/internal/metrics" in paths
    assert "/debug/details" in paths
    assert "/graphql" in paths
    assert "/oauth/callback" in paths
    assert "/login" in paths
    assert "/logout" in paths
    assert "/health" in paths

    # Excludes non-matching strings
    assert "just some random text" not in paths
    assert "./relative/file.html" not in paths


@pytest.mark.asyncio
async def test_process_js_endpoint_mining(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """
    fetch('/api/v1/users');
    const updatePath = "/api/v1/update-profile";
    const deletePath = "/api/v1/delete-user";
    """

    mock_get = AsyncMock(return_value=mock_resp)
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    registry = EndpointRegistry(base_url="http://example.com")
    new_count = await process_js_endpoint_mining(
        js_url="http://example.com/static/bundle.js", registry=registry, base_url="http://example.com"
    )

    assert new_count == 3
    all_eps = registry.get_all()

    # Verify method heuristics
    assert any(ep.path == "/api/v1/users" and ep.method == "GET" for ep in all_eps)
    assert any(ep.path == "/api/v1/update-profile" and ep.method == "POST" for ep in all_eps)
    assert any(ep.path == "/api/v1/delete-user" and ep.method == "DELETE" for ep in all_eps)


def test_parse_graphql_schema_standard():
    schema_data = {
        "queryType": {"name": "QueryType"},
        "mutationType": {"name": "MutationType"},
        "types": [
            {"name": "QueryType", "fields": [{"name": "user"}, {"name": "products"}]},
            {"name": "MutationType", "fields": [{"name": "createUser"}, {"name": "deleteProduct"}]},
        ],
    }

    operations = parse_graphql_schema(schema_data)

    assert ("user", "query") in operations
    assert ("products", "query") in operations
    assert ("createUser", "mutation") in operations
    assert ("deleteProduct", "mutation") in operations


def test_parse_graphql_schema_custom_mock():
    schema_data = {"queries": ["user(id: ID!)", "products"], "mutations": ["createUser(input: CreateUserInput!)"]}

    operations = parse_graphql_schema(schema_data)

    assert ("user", "query") in operations
    assert ("products", "query") in operations
    assert ("createUser", "mutation") in operations


@pytest.mark.asyncio
async def test_execute_graphql_introspection(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(
        return_value={"data": {"__schema": {"queries": ["users", "settings"], "mutations": ["saveSettings"]}}}
    )

    mock_post = AsyncMock(return_value=mock_resp)
    monkeypatch.setattr("httpx.AsyncClient.post", mock_post)

    registry = EndpointRegistry(base_url="http://example.com")
    new_count = await execute_graphql_introspection(graphql_url="http://example.com/graphql", registry=registry)

    assert new_count == 3
    all_eps = registry.get_all()

    assert any(ep.url == "http://example.com/graphql?query=users" and ep.method == "GET" for ep in all_eps)
    assert any(ep.url == "http://example.com/graphql?query=settings" and ep.method == "GET" for ep in all_eps)
    assert any(ep.url == "http://example.com/graphql?mutation=saveSettings" and ep.method == "POST" for ep in all_eps)
