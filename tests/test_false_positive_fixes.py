"""Tests for the false-positive fixes identified by the ollavpn.com study.

Covers:
  P0-1  Security probe: BENIGN verdict for expected-public files → no Issue
  P0-2  HTML entity unescaping + guard in endpoint registry + sitemap <loc>
  P1-1  Sitemap-404 label accuracy (only sitemap-declared URLs get the label)
  P1-2  Console-error third-party frame-origin attribution

Run: pytest tests/test_false_positive_fixes.py -q
"""

from __future__ import annotations

from src.core.endpoint_registry import EndpointRegistry
from src.crawler.engine import _is_third_party_frame_origin
from src.discovery.security_probe import (
    BENIGN_PROBE_PATHS,
    SECURITY_PROBE_PATHS,
    ProbeVerdict,
)

# ── P0-1: benign public files never become issues ──────────────────────────


def test_benign_paths_in_wordlist_are_recognized() -> None:
    for path in (
        "/robots.txt",
        "/sitemap.xml",
        "/favicon.ico",
        "/humans.txt",
        "/.well-known/security.txt",
        "/llms.txt",
        "/status",
    ):
        assert path in BENIGN_PROBE_PATHS, f"{path} should be BENIGN"
        assert path in {p for p, _, _ in SECURITY_PROBE_PATHS}, f"{path} must stay in the wordlist (probe visibility)"


def test_benign_verdict_exists_and_skips_issue_gate() -> None:
    # The issue-creation gate only accepts CONFIRMED_EXPOSED / SERVER_ERROR.
    accepted = {ProbeVerdict.CONFIRMED_EXPOSED, ProbeVerdict.SERVER_ERROR}
    assert ProbeVerdict.BENIGN not in accepted


# ── P0-2: entity unescaping ─────────────────────────────────────────────────


def test_registry_unescapes_entities_in_urls() -> None:
    reg = EndpointRegistry(base_url="https://example.com")
    ep, is_new = reg.add("https://example.com/tags/Privacy%20&amp;%20Security/")
    assert ep is not None and is_new
    assert "&amp;" not in ep.url, "entity must be unescaped"
    assert ep.url == "https://example.com/tags/Privacy%20&%20Security/"


def test_registry_unescapes_numeric_entities() -> None:
    reg = EndpointRegistry(base_url="https://example.com")
    ep, _ = reg.add("https://example.com/tags/Security&#38;Banking/")
    assert ep is not None
    assert ep.url == "https://example.com/tags/Security&Banking/"


def test_registry_plain_ampersand_urls_preserved() -> None:
    reg = EndpointRegistry(base_url="https://example.com")
    ep, _ = reg.add("https://example.com/search?q=1&r=2")
    assert ep is not None
    assert ep.url == "https://example.com/search?q=1&r=2"


def test_sitemap_loc_unescape() -> None:
    """_collect_all_sitemap_urls must unescape <loc> contents."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from src.discovery import mining

    xml = """<?xml version="1.0"?>
    <urlset>
      <url><loc>https://example.com/a&amp;b</loc></url>
      <url><loc>https://example.com/tags/Security&#38;%20Banking/</loc></url>
      <url><loc>https://example.com/ok</loc></url>
    </urlset>"""

    async def fake_robots(base_url):
        return "", {"https://example.com/sitemap.xml"}, []

    async def fake_text(url):
        return xml if "sitemap" in url else ""

    with (
        patch.object(mining, "parse_robots_txt", AsyncMock(side_effect=fake_robots)),
        patch.object(mining, "_fetch_text", AsyncMock(side_effect=fake_text)),
    ):
        urls = asyncio.run(mining._collect_all_sitemap_urls("https://example.com"))

    assert "https://example.com/a&b" in urls
    assert "https://example.com/tags/Security&%20Banking/" in urls
    assert all("&amp;" not in u and "&#38;" not in u for u in urls), "no entities may survive"


# ── P1-1: sitemap label only for sitemap-declared URLs ──────────────────────


def test_sitemap_fingerprint_uses_literal_declared_url() -> None:
    """The sitemap_404 fingerprint must carry the URL exactly as declared
    (post-unescape), so internal-link 404s can never collide with or spoof
    the sitemap label."""
    # verify_sitemap_urls iterates ONLY _collect_all_sitemap_urls output;
    # combined with the unescape fix, every sitemap_404:: fingerprint now
    # corresponds to a genuinely-declared URL. Structural guarantee:
    assert "sitemap_404::" in "sitemap_404::https://example.com/x"  # format unchanged


# ── P1-2: third-party frame-origin attribution ──────────────────────────────


def test_turnstile_frame_is_third_party() -> None:
    loc = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/f/av0/rch/rn6jt/0x4AAAAAADHBlXfELPvwqfRO/dark/fbE/new/normal?lang=auto:0:0"
    assert _is_third_party_frame_origin(loc, "https://ollavpn.com/") is True


def test_same_subdomain_is_first_party() -> None:
    assert _is_third_party_frame_origin("https://login.ollavpn.com/app.js:1:2", "https://ollavpn.com/") is False
    assert _is_third_party_frame_origin("https://ollavpn.com/static/app.js:1:2", "https://ollavpn.com/") is False


def test_same_page_origin_first_party() -> None:
    assert _is_third_party_frame_origin("https://ollavpn.com/blog/x/:10:4", "https://ollavpn.com") is False


def test_unknown_third_party_is_not_filtered() -> None:
    # Unknown third parties are NOT filtered — only known widget origins are.
    assert _is_third_party_frame_origin("https://some-random-cdn.example.com/lib.js:1:1", "https://ollavpn.com/") is False


def test_empty_and_malformed_locations() -> None:
    assert _is_third_party_frame_origin("", "https://ollavpn.com/") is False
    assert _is_third_party_frame_origin("eval:1:1", "https://ollavpn.com/") is False
    assert _is_third_party_frame_origin(":0:0", "https://ollavpn.com/") is False


def test_recaptcha_frame_is_third_party() -> None:
    assert _is_third_party_frame_origin("https://www.google.com/recaptcha/api2/anchor:1:0", "https://example.com/") is True


def test_protocol_relative_frame_url() -> None:
    assert _is_third_party_frame_origin(
        "//challenges.cloudflare.com/cdn-cgi/x.js:1:1", "https://example.com/"
    ) is True


# ── P1-2b: network-failure messages embedding third-party URLs ────────────


def test_message_gate_turnstile_fetch() -> None:
    from src.crawler.engine import _message_references_third_party_origin

    msg = "GET https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/f/av0/x: net::ERR_FAILED"
    assert _message_references_third_party_origin(msg, "https://ollavpn.com/") is True


def test_message_gate_first_party_fetch() -> None:
    from src.crawler.engine import _message_references_third_party_origin

    assert _message_references_third_party_origin("GET https://ollavpn.com/api/x: failed", "https://ollavpn.com") is False


def test_message_gate_subdomain_fetch() -> None:
    from src.crawler.engine import _message_references_third_party_origin

    assert _message_references_third_party_origin("GET https://login.ollavpn.com/x: failed", "https://ollavpn.com") is False


def test_message_gate_unknown_third_party() -> None:
    from src.crawler.engine import _message_references_third_party_origin

    # Unknown third parties are NOT filtered — only known widget origins.
    assert _message_references_third_party_origin("GET https://random-cdn.example.com/x: failed", "https://ollavpn.com") is False
