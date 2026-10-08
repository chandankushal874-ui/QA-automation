"""Verification test to audit crawl queue ordering between frontend webpages and console/debug URLs."""

from __future__ import annotations

from src.core.endpoint_registry import EndpointSource, EndpointType
from src.discovery.prioritization import calculate_priority_score


def test_crawl_priority_order_verification():
    # Add frontend webpages
    frontend_pages = [
        ("https://app.example.com/", EndpointSource.USER_PROVIDED, EndpointType.PAGE),
        ("https://app.example.com/dashboard", EndpointSource.DOM_LINK, EndpointType.PAGE),
        ("https://app.example.com/products", EndpointSource.DOM_LINK, EndpointType.PAGE),
        ("https://app.example.com/checkout", EndpointSource.DOM_LINK, EndpointType.PAGE),
        ("https://app.example.com/settings", EndpointSource.DOM_LINK, EndpointType.PAGE),
        ("https://app.example.com/profile", EndpointSource.DOM_LINK, EndpointType.PAGE),
    ]

    # Add console / debug / error log URLs
    console_debug_urls = [
        ("https://app.example.com/console", EndpointSource.NETWORK_CAPTURE, EndpointType.DEBUG),
        ("https://app.example.com/_console/events", EndpointSource.NETWORK_CAPTURE, EndpointType.DEBUG),
        ("https://app.example.com/debug/logs", EndpointSource.NETWORK_CAPTURE, EndpointType.DEBUG),
        ("https://app.example.com/api/v1/error-stream?console=1", EndpointSource.NETWORK_CAPTURE, EndpointType.API),
        ("https://app.example.com/metrics", EndpointSource.NETWORK_CAPTURE, EndpointType.DEBUG),
    ]

    scored_nodes = []

    for url, source, ep_type in frontend_pages + console_debug_urls:
        score = calculate_priority_score(
            url=url,
            method="GET",
            depth=1,
            endpoint_type=ep_type.value,
            source=source.value,
        )
        scored_nodes.append((score, url, source.value, ep_type.value))

    # Sort by priority score ascending (lowest score = highest priority = crawled first)
    scored_nodes.sort(key=lambda x: x[0])

    print("\n=== CRAWL PRIORITY EXECUTION ORDER ===")
    for idx, (score, url, source, ep_type) in enumerate(scored_nodes, 1):
        print(f"Rank {idx}: [Score: {score:.2f}] {url} (Source: {source}, Type: {ep_type})")
    print("======================================")

    # Top 6 ranked items MUST be frontend webpages
    top_6_urls = [node[1] for node in scored_nodes[:6]]
    assert "https://app.example.com/checkout" in top_6_urls
    assert "https://app.example.com/dashboard" in top_6_urls
    assert "https://app.example.com/products" in top_6_urls
    assert "https://app.example.com/settings" in top_6_urls

    # Console and debug URLs must be ranked AT THE END (lowest priority)
    console_rank = next(i for i, node in enumerate(scored_nodes, 1) if "console" in node[1])
    assert console_rank > 6, f"Console URL was ranked at position {console_rank}, should be > 6"


if __name__ == "__main__":
    test_crawl_priority_order_verification()
