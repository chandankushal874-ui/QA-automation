"""Unit tests for the crawl path prioritization module."""

from __future__ import annotations

from src.discovery.prioritization import calculate_priority_score


def test_priority_score_auth_and_high_paths():
    # Login url should have high priority (low score)
    assert calculate_priority_score("http://example.com/login") == 0.2
    assert calculate_priority_score("http://example.com/api/auth/token") == 0.2
    assert calculate_priority_score("http://example.com/checkout/pay") == 0.2
    assert calculate_priority_score("http://example.com/settings") == 0.2


def test_priority_score_static_and_low_paths():
    # About / terms should have low priority (high score)
    assert calculate_priority_score("http://example.com/about-us") == 4.0
    assert calculate_priority_score("http://example.com/terms") == 4.0
    assert calculate_priority_score("http://example.com/static/style.css") == 4.0
    assert calculate_priority_score("http://example.com/logo.png") == 4.0


def test_priority_score_method_influence():
    # Mutating methods should decrease score (increase priority)
    get_score = calculate_priority_score("http://example.com/items/123", method="GET")
    post_score = calculate_priority_score("http://example.com/items/123", method="POST")

    assert post_score < get_score
    assert post_score == 1.5
    assert get_score == 2.0


def test_priority_score_has_forms():
    # Pages with forms should have higher priority
    no_form = calculate_priority_score("http://example.com/items/123", has_forms=False)
    has_form = calculate_priority_score("http://example.com/items/123", has_forms=True)

    assert has_form < no_form
    assert has_form == 1.5


def test_priority_score_depth_penalty():
    # Deeper paths should have higher scores (lower priority)
    depth_0 = calculate_priority_score("http://example.com/products", depth=0)
    depth_3 = calculate_priority_score("http://example.com/products", depth=3)

    assert depth_3 == depth_0 + 30.0
    assert depth_3 > depth_0


def test_priority_score_console_urls_deprioritized():
    # Console and debug URLs should be assigned low priority (high score 4.5)
    assert calculate_priority_score("http://example.com/console") == 4.5
    assert calculate_priority_score("http://example.com/_console/events") == 4.5
    assert calculate_priority_score("http://example.com/debug/logs") == 4.5
    assert calculate_priority_score("http://example.com/metrics") == 4.5
    assert calculate_priority_score("http://example.com/api/stream?console=true") == 4.5


def test_priority_score_frontend_page_boost():
    # Frontend HTML pages should have higher priority (lower score) than console or debug endpoints
    frontend_page = calculate_priority_score("http://example.com/products", endpoint_type="page", source="dom_link")
    console_url = calculate_priority_score(
        "http://example.com/console", endpoint_type="debug", source="network_capture"
    )

    assert frontend_page < console_url
    assert console_url == 4.5
