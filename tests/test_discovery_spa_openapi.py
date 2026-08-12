"""Unit tests for the SPA state-flow graph and OpenAPI discovery modules."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.endpoint_registry import EndpointRegistry, EndpointSource
from src.discovery.openapi import (
    auto_discover_openapi_specs,
    fetch_and_parse_spec,
    parse_openapi_spec_content,
)
from src.discovery.spa_graph import (
    SPAGraph,
    SPAState,
    SPATransition,
    compute_python_dom_hash,
)

# ─── SPA Graph Tests ───


def test_spa_graph_path_planning():
    graph = SPAGraph()

    # Create states: state A (home), state B (products), state C (checkout), state D (isolated)
    state_a = SPAState(state_hash="hash_a", url="http://example.com/spa", title="SPA Home")
    state_b = SPAState(state_hash="hash_b", url="http://example.com/spa", title="Products")
    state_c = SPAState(state_hash="hash_c", url="http://example.com/spa", title="Checkout")
    state_d = SPAState(state_hash="hash_d", url="http://example.com/spa", title="Isolated")

    graph.add_state(state_a)
    graph.add_state(state_b)
    graph.add_state(state_c)
    graph.add_state(state_d)

    # Add transitions: A -> B, B -> C
    t1 = SPATransition(source_hash="hash_a", target_hash="hash_b", action_type="click", selector="button.products")
    t2 = SPATransition(source_hash="hash_b", target_hash="hash_c", action_type="click", selector="button.checkout")

    graph.add_transition(t1)
    graph.add_transition(t2)

    # Find path A -> C
    path = graph.find_path("hash_a", "hash_c")
    assert path is not None
    assert len(path) == 2
    assert path[0].selector == "button.products"
    assert path[1].selector == "button.checkout"

    # Try path to isolated node D (should be None)
    path_isolated = graph.find_path("hash_a", "hash_d")
    assert path_isolated is None

    # Try path from nonexistent state (should be None)
    assert graph.find_path("hash_nonexistent", "hash_c") is None


def test_compute_python_dom_hash():
    html_a = """
    <div>
        <button id="btn1" class="btn">Click me</button>
        <input type="text" id="username">
    </div>
    """

    html_b = """
    <div>
        <button id="btn1" class="btn">Click me</button>
        <input type="text" id="username">
        <form action="/submit"></form>
    </div>
    """

    hash_a = compute_python_dom_hash(html_a)
    hash_b = compute_python_dom_hash(html_b)

    assert hash_a != hash_b
    assert len(hash_a) == 32  # MD5 length

    # Structural changes should affect hash, but order of attributes shouldn't (sorted)
    html_a_attr_order = """
    <div>
        <button class="btn" id="btn1">Click me</button>
        <input id="username" type="text">
    </div>
    """
    assert compute_python_dom_hash(html_a) == compute_python_dom_hash(html_a_attr_order)


# ─── OpenAPI Spec Tests ───


def test_parse_openapi_spec_json():
    json_spec = """
    {
      "openapi": "3.0.0",
      "info": { "title": "Test API", "version": "1.0" },
      "paths": {
        "/api/v1/users": {
          "get": { "responses": {} },
          "post": { "responses": {} }
        },
        "/api/v1/users/{id}": {
          "delete": { "responses": {} }
        }
      }
    }
    """
    routes = parse_openapi_spec_content(json_spec)
    assert len(routes) == 3
    assert ("/api/v1/users", "GET") in routes
    assert ("/api/v1/users", "POST") in routes
    assert ("/api/v1/users/{id}", "DELETE") in routes


def test_parse_openapi_spec_yaml():
    yaml_spec = """
openapi: 3.0.0
info:
  title: Test API
  version: '1.0'
paths:
  /api/v1/items:
    get:
      responses: {}
    put:
      responses: {}
"""
    routes = parse_openapi_spec_content(yaml_spec)
    assert len(routes) == 2
    assert ("/api/v1/items", "GET") in routes
    assert ("/api/v1/items", "PUT") in routes


@pytest.mark.asyncio
async def test_fetch_and_parse_spec(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """
    {
      "paths": {
        "/api/v1/metrics": {
          "get": {}
        }
      }
    }
    """
    mock_get = AsyncMock(return_value=mock_resp)
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    registry = EndpointRegistry(base_url="http://example.com")
    new_count = await fetch_and_parse_spec(
        spec_url="http://example.com/openapi.json", registry=registry, base_url="http://example.com"
    )

    assert new_count == 1
    all_eps = registry.get_all()
    assert len(all_eps) == 1
    assert all_eps[0].path == "/api/v1/metrics"
    assert all_eps[0].source == EndpointSource.OPENAPI_SPEC


@pytest.mark.asyncio
async def test_auto_discover_openapi_specs(monkeypatch):
    # Mock fallback lookup where first returns 404 and second returns 200 spec
    resp_404 = MagicMock()
    resp_404.status_code = 404

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.text = '{"paths": {"/health": {"get": {}}}}'

    def mock_get_impl(url, **kwargs):
        if url.endswith("/openapi.json"):
            return resp_404
        elif url.endswith("/swagger.json"):
            return resp_200
        return resp_404

    mock_get = AsyncMock(side_effect=mock_get_impl)
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    registry = EndpointRegistry(base_url="http://example.com")
    new_count = await auto_discover_openapi_specs("http://example.com", registry)

    assert new_count == 1
    all_eps = registry.get_all()
    assert all_eps[0].path == "/health"
