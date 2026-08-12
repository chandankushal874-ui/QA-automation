from unittest.mock import AsyncMock, MagicMock

import pytest

from src.discovery.mining import parse_robots_txt


@pytest.mark.asyncio
async def test_parse_robots_txt_with_crawl_delay(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """
User-agent: *
Disallow: /admin
Crawl-delay: 5.5
Sitemap: http://example.com/sitemap.xml
"""
    mock_get = AsyncMock(return_value=mock_resp)
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    disallowed, sitemaps, crawl_delay = await parse_robots_txt("http://example.com")
    assert "/admin" in disallowed
    assert "http://example.com/sitemap.xml" in sitemaps
    assert crawl_delay == 5.5
