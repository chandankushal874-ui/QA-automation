"""Unit tests for the static/seed discovery and DOM mining module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.endpoint_registry import EndpointRegistry, EndpointSource
from src.discovery.mining import (
    discover_static_seed_endpoints,
    is_same_scope,
    mine_html_dom,
    normalize_and_resolve_url,
    parse_robots_txt,
    parse_sitemap_xml,
)


def test_is_same_scope():
    base_url = "https://example.com"

    # same-domain checks
    assert is_same_scope("https://example.com/about", base_url, "same-domain") is True
    assert is_same_scope("https://api.example.com/v1", base_url, "same-domain") is True
    assert is_same_scope("https://google.com", base_url, "same-domain") is False

    # same-origin checks
    assert is_same_scope("https://example.com/about", base_url, "same-origin") is True
    assert is_same_scope("https://api.example.com/v1", base_url, "same-origin") is False
    assert is_same_scope("http://example.com/about", base_url, "same-origin") is False


def test_normalize_and_resolve_url():
    base_url = "https://example.com"
    page_url = "https://example.com/users/profile"

    assert normalize_and_resolve_url("/about", page_url, base_url) == "https://example.com/about"
    assert normalize_and_resolve_url("edit", page_url, base_url) == "https://example.com/users/edit"
    assert normalize_and_resolve_url("../items", page_url, base_url) == "https://example.com/items"

    # Absolute URL
    assert normalize_and_resolve_url("https://google.com/search", page_url, base_url) == "https://google.com/search"

    # Invalid schemes
    assert normalize_and_resolve_url("javascript:void(0)", page_url, base_url) is None
    assert normalize_and_resolve_url("mailto:test@example.com", page_url, base_url) is None
    assert normalize_and_resolve_url("#section", page_url, base_url) is None


@pytest.mark.asyncio
async def test_parse_robots_txt(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """
User-agent: *
Disallow: /internal/
Disallow: /admin-panel
Sitemap: http://example.com/sitemap.xml
# Comment line
"""

    mock_get = AsyncMock(return_value=mock_resp)
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    disallowed, sitemaps, _ = await parse_robots_txt("http://example.com")
    assert "/internal/" in disallowed
    assert "/admin-panel" in disallowed
    assert "http://example.com/sitemap.xml" in sitemaps


@pytest.mark.asyncio
async def test_parse_sitemap_xml(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>http://example.com/</loc>
  </url>
  <url>
    <loc>http://example.com/about</loc>
  </url>
</urlset>
"""

    mock_get = AsyncMock(return_value=mock_resp)
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    urls = await parse_sitemap_xml("http://example.com/sitemap.xml")
    assert "http://example.com/" in urls
    assert "http://example.com/about" in urls


@pytest.mark.asyncio
async def test_discover_static_seed_endpoints(monkeypatch):
    # Mock robots.txt response
    mock_robots = MagicMock()
    mock_robots.status_code = 200
    mock_robots.text = "Disallow: /disallowed-path\nSitemap: http://example.com/custom_sitemap.xml"

    # Mock sitemap.xml response
    mock_sitemap = MagicMock()
    mock_sitemap.status_code = 200
    mock_sitemap.text = "<loc>http://example.com/sitemap-path</loc>"

    def mock_get_impl(url, **kwargs):
        resp = MagicMock()
        if "robots.txt" in url:
            return mock_robots
        elif "custom_sitemap.xml" in url:
            return mock_sitemap
        resp.status_code = 404
        return resp

    mock_get = AsyncMock(side_effect=mock_get_impl)
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    registry = EndpointRegistry(base_url="http://example.com")
    new_count, _crawl_delay = await discover_static_seed_endpoints("http://example.com", registry)

    assert new_count == 2

    disallowed_ep = registry.get_by_source(EndpointSource.ROBOTS_TXT)
    sitemap_ep = registry.get_by_source(EndpointSource.SITEMAP_XML)

    assert len(disallowed_ep) == 1
    assert disallowed_ep[0].url == "http://example.com/disallowed-path"

    assert len(sitemap_ep) == 1
    assert sitemap_ep[0].url == "http://example.com/sitemap-path"


def test_mine_html_dom():
    html = """
    <html>
      <head>
        <link rel="stylesheet" href="/static/main.css">
        <script src="/static/bundle.js"></script>
      </head>
      <body>
        <a href="/about">About Us</a>
        <a href="https://google.com">Google</a>

        <form action="/submit-form" method="POST">
          <input type="hidden" name="next_page" value="/api/v1/redirect-target">
          <input type="text" name="username">
        </form>

        <div data-url="/api/v1/lazy-data" data-ignore="hello">Lazy Content</div>

        <!-- Leaked internal endpoint in comment: /api/internal/test-path -->
        <!-- Internal complete link: http://example.com/api/internal/metrics -->
      </body>
    </html>
    """

    registry = EndpointRegistry(base_url="http://example.com")
    mine_html_dom(
        html, page_url="http://example.com/home", base_url="http://example.com", registry=registry, scope="same-domain"
    )

    # Total unique endpoints:
    # 1. /static/main.css (dom_resource)
    # 2. /static/bundle.js (dom_resource)
    # 3. /about (dom_link)
    # 4. /submit-form (dom_link, POST)
    # 5. /api/v1/redirect-target (hidden_input)
    # 6. /api/v1/lazy-data (dom_link via data-url)
    # 7. /api/internal/test-path (html_comment)
    # 8. http://example.com/api/internal/metrics (html_comment)
    # Note: https://google.com is out of scope since base is http://example.com and scope="same-domain"

    all_eps = registry.get_all()

    # Check absolute URL and scope exclusion
    assert not any(ep.domain == "https://google.com" for ep in all_eps)

    # Check findings
    assert any(ep.path == "/static/main.css" for ep in all_eps)
    assert any(ep.path == "/static/bundle.js" for ep in all_eps)
    assert any(ep.path == "/about" for ep in all_eps)
    assert any(ep.path == "/submit-form" and ep.method == "POST" for ep in all_eps)
    assert any(ep.path == "/api/v1/redirect-target" for ep in all_eps)
    assert any(ep.path == "/api/v1/lazy-data" for ep in all_eps)
    assert any(ep.path == "/api/internal/test-path" for ep in all_eps)
    assert any(ep.path == "/api/internal/metrics" for ep in all_eps)

    # Ensure they are classified with correct sources
    hidden_eps = registry.get_by_source(EndpointSource.HIDDEN_INPUT)
    assert len(hidden_eps) == 1
    assert hidden_eps[0].path == "/api/v1/redirect-target"

    comment_eps = registry.get_by_source(EndpointSource.HTML_COMMENT)
    assert len(comment_eps) == 2
