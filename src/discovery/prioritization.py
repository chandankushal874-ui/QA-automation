"""Crawl path prioritization and weight assignment engine.

Assigns priority weights (scores) to frontier nodes.
Priority ordering:
- Lower score = higher priority (processed first).
- High priority (score 0.1 - 0.5): Auth paths, checkout, cart, settings, admin panels, form actions.
- Medium priority (score 1.0 - 2.0): Regular endpoints, user pages, dashboard sections.
- Low priority (score 5.0 - 10.0): Static documentation, about pages, contact, terms, help, resources.

Spec reference: master-build-prompt §3, Step N
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Path pattern lists for priority classification
HIGH_PRIORITY_PATTERNS: list[str] = [
    r"/login",
    r"/signin",
    r"/signup",
    r"/register",
    r"/logout",
    r"/signout",
    r"/session",
    r"/auth",
    r"/admin",
    r"/dashboard/settings",
    r"/checkout",
    r"/cart",
    r"/pay",
    r"/billing",
    r"/settings",
    r"/profile",
    r"/account",
    r"/api/auth",
    r"/api/admin",
]

LOW_PRIORITY_PATTERNS: list[str] = [
    r"/about",
    r"/contact",
    r"/terms",
    r"/privacy",
    r"/help",
    r"/faq",
    r"/docs",
    r"/documentation",
    r"/support",
    r"/license",
    r"/resources",
    r"\.(?:css|js|png|jpe?g|gif|ico|svg|woff2?|ttf|pdf|zip|tar\.gz)$",
]

CONSOLE_DEBUG_PATTERNS: list[str] = [
    r"/console(?:/|$)",
    r"/_console(?:/|$)",
    r"/debug(?:/|$)",
    r"/_debug(?:/|$)",
    r"/actuator(?:/|$)",
    r"/metrics(?:/|$)",
    r"/logs(?:/|$)",
    r"/error-log(?:/|$)",
    r"(?:^|[\?&])console=",
]


def calculate_priority_score(
    url: str,
    method: str = "GET",
    depth: int = 0,
    has_forms: bool = False,
    endpoint_type: str = "unknown",
    source: str = "unknown",
) -> float:
    """Calculate the priority score for a crawl frontier node.

    Lower score = higher priority.
    To enforce a strict Breadth-First Search (BFS) sequence, the depth
    is the mathematically dominant factor. Heuristic overrides (high/low terms)
    are bounded so they cannot jump depth boundaries.
    """
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parsed.query.lower()

    # Base heuristic score
    h_score = 2.0

    # HTTP Method weighting (mutating methods take higher priority)
    if method.upper() in ("POST", "PUT", "DELETE", "PATCH"):
        h_score -= 0.5

    # Explicit user-provided seed URLs take high priority
    if source == "user_provided":
        h_score = 0.2
    else:
        # Check high priority patterns
        for pattern in HIGH_PRIORITY_PATTERNS:
            if re.search(pattern, path) or re.search(pattern, query):
                h_score = 0.2
                break

    # Check console & debug patterns (low priority for background/network captures, unless explicit page link)
    is_console_debug = any(re.search(pattern, path) or re.search(pattern, query) for pattern in CONSOLE_DEBUG_PATTERNS)
    is_explicit_page = source in ("dom_link", "sitemap_xml", "user_provided", "redirect") or endpoint_type == "page"

    if (is_console_debug or endpoint_type in ("debug", "console")) and not is_explicit_page:
        h_score = 4.5

    # Check low priority patterns
    elif h_score > 0.5:  # If not already high priority
        for pattern in LOW_PRIORITY_PATTERNS:
            if re.search(pattern, path):
                h_score = 4.0
                break

    # Additional heuristics for frontend pages and forms
    if has_forms and h_score > 0.5:
        h_score -= 0.5

    # Boost frontend HTML pages discovered via DOM/sitemap/user
    if (
        source in ("dom_link", "sitemap_xml", "user_provided", "redirect") or endpoint_type == "page"
    ) and h_score > 0.3:
        h_score -= 0.3

    # Bounded heuristic score: always between 0.1 and 5.0
    h_score = max(0.1, min(5.0, h_score))

    # BFS Math: Depth is the dominant component (spaced by 10.0)
    score = (depth * 10.0) + h_score

    return score
