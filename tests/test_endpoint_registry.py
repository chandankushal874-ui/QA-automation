"""Tests for the endpoint registry module."""

from __future__ import annotations

from src.core.endpoint_registry import (
    COMMON_PATHS,
    DiscoveredEndpoint,
    EndpointRegistry,
    EndpointSource,
    EndpointStatus,
    EndpointType,
    classify_endpoint_type,
)

# ─── classify_endpoint_type tests ───


class TestClassifyEndpointType:
    """Test endpoint type classification from URL patterns."""

    def test_api_endpoint(self) -> None:
        assert classify_endpoint_type("https://example.com/api/v1/users") == EndpointType.API

    def test_graphql_endpoint(self) -> None:
        assert classify_endpoint_type("https://example.com/graphql") == EndpointType.GRAPHQL

    def test_graphiql_endpoint(self) -> None:
        assert classify_endpoint_type("https://example.com/graphiql") == EndpointType.GRAPHQL

    def test_websocket_endpoint(self) -> None:
        assert classify_endpoint_type("wss://example.com/ws") == EndpointType.WEBSOCKET

    def test_admin_endpoint(self) -> None:
        assert classify_endpoint_type("https://example.com/admin") == EndpointType.ADMIN

    def test_internal_admin(self) -> None:
        assert classify_endpoint_type("https://example.com/internal/admin") == EndpointType.ADMIN

    def test_debug_endpoint(self) -> None:
        assert classify_endpoint_type("https://example.com/debug/") == EndpointType.DEBUG

    def test_actuator_endpoint(self) -> None:
        assert classify_endpoint_type("https://example.com/actuator/health") == EndpointType.DEBUG

    def test_swagger_spec(self) -> None:
        assert classify_endpoint_type("https://example.com/swagger.json") == EndpointType.SPEC

    def test_login_endpoint(self) -> None:
        assert classify_endpoint_type("https://example.com/login") == EndpointType.AUTH

    def test_oauth_endpoint(self) -> None:
        assert classify_endpoint_type("https://example.com/oauth/callback") == EndpointType.AUTH

    def test_static_asset_css(self) -> None:
        assert classify_endpoint_type("https://example.com/styles/main.css") == EndpointType.STATIC_ASSET

    def test_static_asset_js(self) -> None:
        assert classify_endpoint_type("https://example.com/bundle.js") == EndpointType.STATIC_ASSET

    def test_download_pdf(self) -> None:
        assert classify_endpoint_type("https://example.com/report.pdf") == EndpointType.DOWNLOAD

    def test_post_method_is_api(self) -> None:
        assert classify_endpoint_type("https://example.com/submit", method="POST") == EndpointType.API

    def test_plain_page(self) -> None:
        assert classify_endpoint_type("https://example.com/about") == EndpointType.PAGE


# ─── DiscoveredEndpoint tests ───


class TestDiscoveredEndpoint:
    """Test the DiscoveredEndpoint data class."""

    def test_path_extraction(self) -> None:
        ep = DiscoveredEndpoint(url="https://example.com/api/v1/users?limit=10")
        assert ep.path == "/api/v1/users"

    def test_domain_extraction(self) -> None:
        ep = DiscoveredEndpoint(url="https://example.com:8080/api/v1/users")
        assert ep.domain == "https://example.com:8080"

    def test_fingerprint_collapses_numeric_ids(self) -> None:
        ep1 = DiscoveredEndpoint(url="https://example.com/users/123")
        ep2 = DiscoveredEndpoint(url="https://example.com/users/456")
        assert ep1.fingerprint() == ep2.fingerprint()

    def test_fingerprint_collapses_uuids(self) -> None:
        ep1 = DiscoveredEndpoint(url="https://example.com/items/a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        ep2 = DiscoveredEndpoint(url="https://example.com/items/11111111-2222-3333-4444-555555555555")
        assert ep1.fingerprint() == ep2.fingerprint()

    def test_fingerprint_different_methods(self) -> None:
        ep1 = DiscoveredEndpoint(url="https://example.com/api/data", method="GET")
        ep2 = DiscoveredEndpoint(url="https://example.com/api/data", method="POST")
        assert ep1.fingerprint() != ep2.fingerprint()

    def test_fingerprint_different_paths(self) -> None:
        ep1 = DiscoveredEndpoint(url="https://example.com/users")
        ep2 = DiscoveredEndpoint(url="https://example.com/products")
        assert ep1.fingerprint() != ep2.fingerprint()


# ─── EndpointRegistry tests ───


class TestEndpointRegistry:
    """Test the centralized endpoint registry."""

    def test_add_new_endpoint(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        ep, is_new = registry.add("https://example.com/api/users")
        assert is_new is True
        assert ep.url == "https://example.com/api/users"
        assert ep.endpoint_type == EndpointType.API
        assert registry.total_count == 1

    def test_add_duplicate_endpoint_is_not_new(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("https://example.com/api/users")
        _, is_new = registry.add("https://example.com/api/users")
        assert is_new is False
        assert registry.total_count == 1

    def test_add_similar_endpoints_deduplicated(self) -> None:
        """Endpoints with same template should be deduplicated."""
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("https://example.com/users/123")
        _, is_new = registry.add("https://example.com/users/456")
        assert is_new is False  # Same template after fingerprinting
        assert registry.total_count == 1

    def test_add_relative_url_resolved(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        ep, _ = registry.add("/api/v1/data")
        assert ep.url == "https://example.com/api/v1/data"

    def test_add_many_returns_only_new(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("https://example.com/existing")

        new = registry.add_many(
            ["https://example.com/existing", "https://example.com/new1", "https://example.com/new2"],
            source=EndpointSource.JS_ANALYSIS,
        )
        assert len(new) == 2
        assert registry.total_count == 3

    def test_get_by_status(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("https://example.com/page1")
        registry.add("https://example.com/page2")
        registry.mark_visited("https://example.com/page1", status_code=200)

        discovered = registry.get_by_status(EndpointStatus.DISCOVERED)
        visited = registry.get_by_status(EndpointStatus.VISITED)
        assert len(discovered) == 1
        assert len(visited) == 1

    def test_get_by_type(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("https://example.com/api/v1/data")
        registry.add("https://example.com/about")
        registry.add("https://example.com/graphql")

        apis = registry.get_by_type(EndpointType.API)
        pages = registry.get_by_type(EndpointType.PAGE)
        graphql = registry.get_by_type(EndpointType.GRAPHQL)
        assert len(apis) == 1
        assert len(pages) == 1
        assert len(graphql) == 1

    def test_get_by_source(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("/page1", source=EndpointSource.DOM_LINK)
        registry.add("/api/hidden", source=EndpointSource.JS_ANALYSIS)
        registry.add("/admin", source=EndpointSource.API_BRUTE_FORCE)

        js_found = registry.get_by_source(EndpointSource.JS_ANALYSIS)
        assert len(js_found) == 1
        assert js_found[0].path == "/api/hidden"

    def test_get_unvisited(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("/page1")
        registry.add("/page2")
        registry.mark_visited("https://example.com/page1", status_code=200)

        unvisited = registry.get_unvisited()
        assert len(unvisited) == 1

    def test_mark_visited_updates_state(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("https://example.com/page1")
        registry.mark_visited("https://example.com/page1", status_code=200)

        visited = registry.get_by_status(EndpointStatus.VISITED)
        assert len(visited) == 1
        assert visited[0].visit_count == 1
        assert visited[0].last_status_code == 200
        assert visited[0].last_visited_at is not None

    def test_mark_failed(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("https://example.com/broken")
        registry.mark_failed("https://example.com/broken", status_code=500)

        failed = registry.get_by_status(EndpointStatus.FAILED)
        assert len(failed) == 1
        assert failed[0].last_status_code == 500

    def test_discovery_rounds_and_diff(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("/page1")
        registry.add("/page2")

        round_1 = registry.start_new_round()
        assert round_1 == 1

        # Add new endpoints after round 1
        registry.add("/page3")
        registry.add("/api/new")

        new_since_1 = registry.get_new_since_round(round_1)
        assert len(new_since_1) == 2

    def test_generate_brute_force_urls(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        urls = registry.generate_brute_force_urls()

        assert len(urls) > 0
        assert all(url.startswith("https://example.com") for url in urls)
        assert "https://example.com/admin" in urls
        assert "https://example.com/.env" in urls
        assert "https://example.com/swagger.json" in urls

    def test_generate_brute_force_excludes_existing(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("https://example.com/admin")

        urls = registry.generate_brute_force_urls()
        # /admin should be excluded since it's already discovered
        assert "https://example.com/admin" not in urls

    def test_summary(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("/api/data")
        registry.add("/about")
        registry.mark_visited("https://example.com/api/data", status_code=200)

        summary = registry.summary()
        assert summary["total"] == 2
        assert "api" in summary["by_type"]
        assert "visited" in summary["by_status"]

    def test_get_requiring_auth(self) -> None:
        registry = EndpointRegistry(base_url="https://example.com")
        registry.add("/public", requires_auth=False)
        registry.add("/private", requires_auth=True)

        auth_required = registry.get_requiring_auth()
        assert len(auth_required) == 1
        assert auth_required[0].path == "/private"

    def test_common_paths_is_populated(self) -> None:
        """Ensure the COMMON_PATHS list has a reasonable number of entries."""
        assert len(COMMON_PATHS) >= 30
        assert "/api" in COMMON_PATHS
        assert "/admin" in COMMON_PATHS
        assert "/.env" in COMMON_PATHS
        assert "/graphql" in COMMON_PATHS
