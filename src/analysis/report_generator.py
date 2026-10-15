import base64
import html
import json
import os
from typing import Any
from urllib.parse import urlparse

from src.core.endpoint_registry import EndpointStatus


def _render_security_probe(issues: list[Any]) -> str:
    """Render the Security / Common-Endpoint Probe section.

    Shows findings from the post-crawl security probe (IssueCategory.SECURITY)
    in a dedicated table, separate from console/JS/network issues. Each row
    shows the path, HTTP status, severity, and description.
    """
    security_issues = [iss for iss in issues if str(getattr(iss.category, "value", iss.category)).lower() == "security"]
    if not security_issues:
        return ""

    # Actual probed-path count (lazy import avoids a module cycle).
    try:
        from src.discovery.security_probe import SECURITY_PROBE_PATHS

        probe_count = len(SECURITY_PROBE_PATHS)
    except Exception:  # pragma: no cover — defensive
        probe_count = 130

    rows = ""
    for iss in security_issues:
        sev = str(getattr(iss.severity, "value", iss.severity)).lower()
        sev_color = get_severity_color(sev)
        rows += f"""
        <tr>
            <td><a href="{html.escape(iss.first_seen_url)}" target="_blank">{html.escape(iss.first_seen_url)}</a></td>
            <td><span class="badge {sev}" style="background-color: {sev_color}; color: {"black" if sev == "visual" else "white"};">{sev.upper()}</span></td>
            <td>{html.escape(iss.error_message or "")}</td>
            <td>{html.escape(iss.description or "")}</td>
        </tr>
        """

    return f"""
        <h2 style="margin-top: 3rem;">🔒 Security / Common-Endpoint Probe</h2>
        <p style="color: var(--muted); font-size: 0.85rem; margin-bottom: 1rem;">
            Post-crawl probe of ~{probe_count} common security-relevant paths (.env, .git/config, wp-admin, robots.txt, api/health…).
            Exposed sensitive files are <span class="badge blocking" style="background-color: #ef4444; color: white;">BLOCKING</span>,
            admin/debug panels are <span class="badge functional" style="background-color: #f97316; color: white;">FUNCTIONAL</span>,
            benign common files are <span class="badge advisory" style="background-color: #3b82f6; color: white;">ADVISORY</span>.
        </p>
        <table>
            <thead>
                <tr>
                    <th style="width: 30%">Path Probed</th>
                    <th style="width: 10%">Severity</th>
                    <th style="width: 15%">Response</th>
                    <th style="width: 45%">Description</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    """


def _render_fix_suggestions(issues: list[Any]) -> str:
    """Render the AI Fix Suggestions section showing patches + E2B verification results."""
    fixable = [iss for iss in issues if getattr(iss, "fix_diff", None)]
    if not fixable:
        return ""

    rows = ""
    for iss in fixable:
        status = getattr(iss, "fix_status", "suggested") or "suggested"
        confidence = getattr(iss, "fix_confidence", None)
        source_file = getattr(iss, "source_file", None) or "unknown"
        source_line = getattr(iss, "source_line", None)

        status_color = "#059669" if status == "verified" else "#eab308"
        status_label = "✓ Verified" if status == "verified" else "⚠ Suggested"
        # A non-verified fix was never proven in a sandbox (Daytona/E2B key
        # missing or check inconclusive) — say so visibly (master prompt §3.7).
        if status != "verified":
            status_label += " · ⚠ simulated — not verified"

        confidence_html = f"{confidence * 100:.0f}%" if confidence is not None else "N/A"
        location_html = f"{html.escape(source_file)}:{source_line}" if source_line else html.escape(source_file)

        # Extract verification log from description (appended by engine.py)
        desc = getattr(iss, "description", "") or ""
        ver_log = ""
        if "AI Fix Explanation:" in desc:
            ver_log = desc.split("AI Fix Explanation:", 1)[1].strip()

        patch_escaped = html.escape(iss.fix_diff or "")
        ver_log_escaped = html.escape(ver_log)

        rows += f"""
        <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                <strong>{html.escape(iss.title)}</strong>
                <span style="color: {status_color}; font-weight: bold;">{status_label}</span>
            </div>
            <div style="color: var(--muted); font-size: 0.8rem; margin-bottom: 0.5rem;">
                📍 {location_html} &nbsp;•&nbsp; 🎯 Confidence: {confidence_html}
            </div>
            {f'<div style="color: var(--muted); font-size: 0.85rem; margin-bottom: 0.5rem;">{ver_log_escaped}</div>' if ver_log_escaped else ""}
            <pre style="background: rgba(0,0,0,0.3); padding: 0.75rem; border-radius: 4px; font-size: 0.75rem; overflow-x: auto; color: #a3e635; white-space: pre-wrap;">{patch_escaped}</pre>
        </div>
        """

    return f"""
        <h2 style="margin-top: 3rem;">AI Fix Suggestions</h2>
        <p style="color: var(--muted); font-size: 0.85rem; margin-bottom: 1rem;">
            Patches verified in an isolated E2B sandbox via pytest before being recommended.
        </p>
        {rows}
    """


def get_severity_color(severity: str) -> str:
    if severity == "blocking":
        return "#ef4444"
    if severity == "functional":
        return "#f97316"
    if severity == "visual":
        return "#eab308"
    return "#3b82f6"


# Discovery sources that prove the app itself references a URL — a real
# user can reach it by clicking/navigating. Probe/wordlist sources (security
# probe, common-path brute force) are deliberately excluded: a SPA shell
# returned for those paths is a false positive, not a broken page.
_ORGANIC_LINK_SOURCES = {
    "dom_link",
    "redirect",
    "header_link",
    "sitemap_xml",
    "html_comment",
    "hidden_input",
}


def _is_organically_linked(ep: Any) -> bool:
    """True if the endpoint was discovered from the app's own content.

    A URL found in the page DOM (``href="/broken"``), a sitemap, a redirect
    chain, or an HTML comment is a real navigation target — unlike a
    wordlist/probe path the app never references. Tolerates duck-typed
    endpoints (tests) where ``source`` may be None or a plain string.
    """
    source = getattr(ep, "source", None)
    source_value = getattr(source, "value", source)
    if source_value in _ORGANIC_LINK_SOURCES:
        return True
    # Registry dedup records secondary discovery sources as
    # extra["also_found_via_<source>"] = <discovered_on_page>.
    extra = getattr(ep, "extra", None) or {}
    return any(
        key.startswith("also_found_via_") and key[len("also_found_via_") :] in _ORGANIC_LINK_SOURCES and bool(value)
        for key, value in extra.items()
    )


def _is_spa_fallback_only(ep: Any) -> bool:
    """True if the endpoint's only problem is a SPA route error on a path
    the app itself never references.

    The HTTP shell returned a valid (<400) status; only the client-side
    router failed to match the path (``ep.extra["effective_status_code"]``).
    For probe/wordlist-discovered paths these are SPA_FALLBACK endpoints —
    secure, not broken — and must not count against the health score.

    Exception: when the endpoint is organically linked (found in the app's
    own DOM/sitemap/redirects), the SPA route error is a genuine broken
    internal link — a user clicking it hits a dead route — so it DOES
    count against the score and this returns False.
    """
    extra = getattr(ep, "extra", None) or {}
    return (
        bool(extra.get("effective_status_code"))
        and ep.last_status_code is not None
        and ep.last_status_code < 400
        and not _is_organically_linked(ep)
    )


def _is_broken_internal_link(ep: Any) -> bool:
    """True if the endpoint is an organically-linked SPA route error.

    The server returned the SPA shell (HTTP 200) but the client-side
    router could not match a path the app itself links to (e.g.
    ``href="/broken"``). Transport is fine; for real users the page is
    effectively a 404. This is the case the plain HTTP-status check
    structurally cannot see on SPAs.
    """
    extra = getattr(ep, "extra", None) or {}
    return (
        bool(extra.get("effective_status_code"))
        and (ep.last_status_code is None or ep.last_status_code < 400)
        and _is_organically_linked(ep)
    )


def _health_score_breakdown(registry: Any, issues: list[Any]) -> dict[str, Any]:
    """Compute the full health-score breakdown (counts + score + grade).

    Weighted formula:
      - Only endpoints the crawler actually fetched (``last_status_code``
        set) count toward the score. Discovered-but-never-visited URLs say
        nothing about site health; including them in the denominator would
        inflate the "valid pages" percentage (the HTTP-200 trap).
      - Broken endpoints (real HTTP 4xx/5xx) reduce the score
        proportionally.
      - Broken internal links (HTTP 200 SPA shell + client-side router
        error on an organically-linked path) also reduce the score
        proportionally — they are effective 404s for real users.
      - Probe-only SPA fallbacks (wordlist paths the app never links to)
        are excluded: secure, not broken.
      - Blocking issues penalize 5 points each
      - Functional issues penalize 2 points each
      - Advisory issues penalize 0.5 points each
    """
    endpoints = registry.get_all()
    total = len(endpoints)
    tested = [ep for ep in endpoints if ep.last_status_code is not None]
    base = len(tested) if tested else total
    if base == 0:
        return {
            "total": total,
            "tested": 0,
            "broken_http": 0,
            "broken_links": 0,
            "spa_fallback_exempt": 0,
            "blocking": 0,
            "functional": 0,
            "advisory": 0,
            "grade": "N/A",
            "score": 0,
        }

    broken_http = sum(1 for ep in tested if ep.last_status_code >= 400)
    broken_links = sum(1 for ep in tested if _is_broken_internal_link(ep))
    spa_fallback_exempt = sum(1 for ep in tested if _is_spa_fallback_only(ep))
    broken = broken_http + broken_links

    blocking = sum(1 for i in issues if str(getattr(i.severity, "value", i.severity)).lower() == "blocking")
    functional = sum(1 for i in issues if str(getattr(i.severity, "value", i.severity)).lower() == "functional")
    advisory = sum(1 for i in issues if str(getattr(i.severity, "value", i.severity)).lower() == "advisory")

    broken_penalty = (broken / base) * 100
    issue_penalty = (blocking * 5) + (functional * 2) + (advisory * 0.5)
    score = max(0, int(100 - broken_penalty - issue_penalty))

    if score >= 95:
        grade = "A"
    elif score >= 90:
        grade = "A-"
    elif score >= 85:
        grade = "B+"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    return {
        "total": total,
        "tested": len(tested),
        "broken_http": broken_http,
        "broken_links": broken_links,
        "spa_fallback_exempt": spa_fallback_exempt,
        "blocking": blocking,
        "functional": functional,
        "advisory": advisory,
        "grade": grade,
        "score": score,
    }


def compute_health_score(registry: Any, issues: list[Any]) -> tuple[str, int]:
    """Compute an overall site health score and letter grade.

    Thin wrapper over :func:`_health_score_breakdown` — see its docstring
    for the weighted formula. Returns ``(grade, score)``.
    """
    breakdown = _health_score_breakdown(registry, issues)
    return breakdown["grade"], breakdown["score"]


def _render_health_score(registry: Any, issues: list[Any]) -> str:
    """Render the overall health score card (DeepSeek-parity).

    The caption states exactly what the score measures so a high grade can
    no longer be mistaken for "the server returned HTTP 200 a lot".
    """
    b = _health_score_breakdown(registry, issues)
    grade, score = b["grade"], b["score"]
    grade_color = "#059669" if score >= 85 else ("#eab308" if score >= 70 else "#ef4444")
    if b["total"] == 0:
        detail = "No endpoints were discovered."
    else:
        parts = [
            f"{b['tested']} of {b['total']} discovered endpoints were actually tested;",
            f"{b['broken_http']} returned HTTP errors,",
            f"{b['broken_links']} broken internal link(s) (HTTP 200 shell, but the client-side router cannot match a linked route).",
        ]
        if b["spa_fallback_exempt"]:
            parts.append(
                f"{b['spa_fallback_exempt']} probe-only SPA fallback(s) excluded — the app never links to them."
            )
        parts.append(
            f"{b['blocking']} blocking issues, {b['functional']} functional issues, {b['advisory']} advisory issues."
        )
        detail = " ".join(parts)
    return f"""
        <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin: 1.5rem 0;">
            <h2 style="margin-top: 0;">📊 Overall Health Score</h2>
            <div style="display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem;">
                <span style="font-size: 3rem; font-weight: bold; color: {grade_color};">{grade}</span>
                <span style="color: var(--muted);">{detail}</span>
            </div>
            <div style="height: 8px; background: var(--border); border-radius: 4px; overflow: hidden;">
                <div style="height: 100%; width: {score}%; background: {grade_color}; border-radius: 4px;"></div>
            </div>
        </div>
    """


def _render_external_services(job: Any) -> str:
    """Render the external services health table (DeepSeek-parity).

    Reads the ``external_services`` list from ``job.config`` (populated by
    ``CrawlEngine._aggregate_external_services``) and renders a table showing
    which third-party integrations are broken vs. working.
    """
    config = getattr(job, "config", None) or {}
    services = config.get("external_services", [])
    if not services:
        return ""

    status_colors = {
        "broken": "#ef4444",
        "blocked-by-csp": "#f97316",
        "failing": "#eab308",
        "issues": "#3b82f6",
        "unknown": "#a1a1aa",
    }

    rows = ""
    for svc in services:
        status = svc.get("status", "unknown")
        color = status_colors.get(status, "#a1a1aa")
        categories = ", ".join(svc.get("categories", []))
        rows += f"""
            <tr>
                <td><strong>{html.escape(svc.get("domain", ""))}</strong></td>
                <td><span style="color: {color}; font-weight: bold;">{html.escape(status)}</span></td>
                <td>{svc.get("issue_count", 0)}</td>
                <td style="font-size: 0.85rem; color: var(--muted);">{html.escape(categories)}</td>
                <td style="font-size: 0.85rem; color: var(--muted);">{html.escape(svc.get("sample_issue", "")[:150])}</td>
            </tr>
        """

    return f"""
        <h2 style="margin-top: 3rem;">🔌 External Service Health</h2>
        <p style="color: var(--muted); font-size: 0.85rem; margin-bottom: 1rem;">
            Third-party integrations detected via CSP violations and network failures.
        </p>
        <table style="margin-top: 1rem;">
            <thead>
                <tr>
                    <th>Service Domain</th>
                    <th>Status</th>
                    <th>Issue Count</th>
                    <th>Categories</th>
                    <th>Sample Issue</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    """


def _render_sitemap_verification(job: Any) -> str:
    """Render the sitemap verification summary (DeepSeek-parity)."""
    config = getattr(job, "config", None) or {}
    sitemap_data = config.get("sitemap_verification")
    if not sitemap_data:
        return ""

    total = sitemap_data.get("total_sitemap_urls", 0)
    verified = sitemap_data.get("verified", 0)
    missing = sitemap_data.get("missing", 0)
    missing_urls = sitemap_data.get("missing_urls", [])

    missing_rows = ""
    for item in missing_urls[:50]:  # Show up to 50 missing URLs
        missing_rows += f"""
            <tr>
                <td style="word-break: break-all; font-size: 0.85rem;">{html.escape(item.get("url", ""))}</td>
                <td><span style="color: #ef4444; font-weight: bold;">HTTP {item.get("status_code", "")}</span></td>
            </tr>
        """

    return f"""
        <h2 style="margin-top: 3rem;">🗺️ Sitemap Verification</h2>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin: 1rem 0;">
            <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; text-align: center;">
                <div style="font-size: 1.8rem; font-weight: bold; color: #3b82f6;">{total}</div>
                <div style="color: var(--muted); font-size: 0.85rem;">Total Sitemap URLs</div>
            </div>
            <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; text-align: center;">
                <div style="font-size: 1.8rem; font-weight: bold; color: #059669;">{verified}</div>
                <div style="color: var(--muted); font-size: 0.85rem;">Verified (2xx/3xx)</div>
            </div>
            <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; text-align: center;">
                <div style="font-size: 1.8rem; font-weight: bold; color: #ef4444;">{missing}</div>
                <div style="color: var(--muted); font-size: 0.85rem;">Missing (4xx/5xx)</div>
            </div>
        </div>
        {
        f'''
        <table style="margin-top: 1rem;">
            <thead>
                <tr><th>Missing URL</th><th>Status</th></tr>
            </thead>
            <tbody>{missing_rows}</tbody>
        </table>
        '''
        if missing_urls
        else ""
    }
    """


# ---------------------------------------------------------------------------
# Probe verdict + display-severity badges (Gaps 1 & 2)
# ---------------------------------------------------------------------------

_VERDICT_LABELS: dict[str, str] = {
    "confirmed_exposed": "CONFIRMED EXPOSED",
    "false_positive": "FALSE POSITIVE",
    "spa_fallback": "SPA FALLBACK",
    "broken_link": "BROKEN LINK",
    "not_found": "NOT FOUND",
    "protected": "PROTECTED",
    "server_error": "SERVER ERROR",
    "network_error": "NETWORK ERROR",
}


def _verdict_badge(verdict: str) -> str:
    """Render a semantic probe-verdict badge (Gap 1)."""
    slug = str(verdict).lower().replace("_", "-")
    label = _VERDICT_LABELS.get(str(verdict).lower(), str(verdict).upper())
    return f'<span class="badge verdict-{slug}">{html.escape(label)}</span>'


def _severity_badge(level: str) -> str:
    """Render a 4-level display-severity badge (Gap 2)."""
    slug = str(level).lower()
    return f'<span class="badge sev-{slug}">{html.escape(slug.upper())}</span>'


def _stat_card(value: Any, label: str, color: str) -> str:
    """One summary-stat card (markup mirrors _render_sitemap_verification)."""
    return f"""
            <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; text-align: center;">
                <div style="font-size: 1.8rem; font-weight: bold; color: {color};">{value}</div>
                <div style="color: var(--muted); font-size: 0.85rem;">{html.escape(label)}</div>
            </div>"""


def _render_summary_stats(registry: Any, job: Any) -> str:
    """Render the top-line summary stats grid (Gap 3).

    Seven cards: Total Tested, OK (2xx), Redirects (3xx), Client Errors (4xx),
    Server Errors (5xx), Network Errors, SPA Route 404s. Counts come from the
    endpoint registry plus the additive ``job.config`` keys
    (``security_probe_results``, ``spa_route_404s``); old jobs without those
    keys simply show 0 for the probe-derived cards.
    """
    config = getattr(job, "config", None) or {}
    endpoints = registry.get_all()

    def _count(lo: int, hi: int) -> int:
        return sum(1 for ep in endpoints if ep.last_status_code is not None and lo <= ep.last_status_code < hi)

    total = len(endpoints)
    ok = _count(200, 300)
    redirects = _count(300, 400)
    client_errors = _count(400, 500)
    server_errors = _count(500, 600)

    probe_results = config.get("security_probe_results") or []
    network_errors = sum(1 for r in probe_results if r.get("verdict") == "network_error")
    # Only endpoints whose visit actually failed count as network errors.
    # Discovered-but-never-visited assets have last_status_code=None — that
    # means "not fetched", not "network error".
    network_errors += sum(1 for ep in endpoints if ep.status == EndpointStatus.FAILED)

    spa_route_404s = len({e.get("url") for e in (config.get("spa_route_404s") or []) if e.get("url")})

    cards = [
        (total, "Total Tested", "#3b82f6"),
        (ok, "OK (2xx)", "#059669"),
        (redirects, "Redirects (3xx)", "#22d3ee"),
        (client_errors, "Client Errors (4xx)", "#f97316"),
        (server_errors, "Server Errors (5xx)", "#ef4444"),
        (network_errors, "Network Errors", "#eab308"),
        (spa_route_404s, "SPA Route 404s", "#60a5fa"),
    ]
    cards_html = "".join(_stat_card(value, label, color) for value, label, color in cards)
    return f"""
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin: 1rem 0;">
            {cards_html}
        </div>
    """


def _render_key_findings(job: Any, registry: Any) -> str:
    """Render the Key Findings card (Gap 4).

    Summarizes platform type, SPA routing health, authentication surface,
    missing static files and overall security posture. Every field degrades
    to "N/A" for old jobs without the additive ``job.config`` keys.
    """
    config = getattr(job, "config", None) or {}
    probe_results = config.get("security_probe_results")
    spa_type = config.get("spa_type")
    spa_404s = config.get("spa_route_404s")
    endpoints = registry.get_all()

    spa_platform_labels = {
        "angular": "Angular SPA with client-side routing",
        "react": "React SPA with client-side routing",
        "vue": "Vue SPA with client-side routing",
        "nextjs": "Next.js SPA with client-side routing",
        "generic_spa": "Single Page Application (catch-all routing)",
        "traditional_server": "Traditional server (real 404s for missing paths)",
    }
    platform = spa_platform_labels.get(spa_type, "N/A") if spa_type else "N/A"

    if spa_404s is None:
        spa_routing = "N/A"
    elif spa_404s:
        n_urls = len({e.get("url") for e in spa_404s if e.get("url")})
        n_broken_links = sum(1 for ep in endpoints if _is_broken_internal_link(ep))
        if n_broken_links:
            probe_only = max(0, n_urls - n_broken_links)
            spa_routing = (
                f"⚠ {n_broken_links} broken internal link(s) — linked by the app but "
                f"not matched by the client router (+{probe_only} probe-only SPA shell(s))"
            )
        else:
            spa_routing = f"{n_urls} endpoint(s) returned the SPA shell — route not matched by client router"
    else:
        spa_routing = "No SPA routing errors detected"

    urls = [ep.url for ep in endpoints]
    has_login = any("/login" in u or "/signin" in u for u in urls)
    has_register = any("/register" in u or "/signup" in u for u in urls)
    if has_register and has_login:
        auth = "Token-based, registration open (login + register pages detected)"
    elif has_register:
        auth = "Registration open (register/signup page detected)"
    elif has_login:
        auth = "Login page detected; registration not detected"
    else:
        auth = "Not detected"

    if probe_results is None:
        missing_static = "N/A"
        security_posture = "N/A"
    else:
        # A static file counts as "missing" unless the probe CONFIRMED real
        # file content. not_found / false_positive / spa_fallback all mean
        # the server does not actually serve the file (SPA shell ≠ file).
        missing = [
            r.get("path", "")
            for r in probe_results
            if r.get("category") == "Security/Static" and r.get("verdict") != "confirmed_exposed"
        ]
        missing_static = ", ".join(missing) if missing else "None — all expected static files present"
        exposed = [r.get("path", "") for r in probe_results if r.get("verdict") == "confirmed_exposed"]
        fp_count = sum(1 for r in probe_results if r.get("verdict") in ("false_positive", "spa_fallback"))
        if exposed:
            security_posture = f"⚠ {len(exposed)} confirmed exposure(s): {', '.join(exposed[:10])}"
        else:
            security_posture = f"No confirmed file exposures — {fp_count} probes returned SPA shell (false positives)"

    rows = [
        ("Platform", platform),
        ("SPA Routing", spa_routing),
        ("Authentication", auth),
        ("Missing Static Files", missing_static),
        ("Security Posture", security_posture),
    ]
    rows_html = "".join(
        f'<tr><td style="color: var(--muted); width: 220px; padding: 0.4rem 1rem 0.4rem 0;"><strong>{html.escape(label)}</strong></td>'
        f'<td style="padding: 0.4rem 0;">{html.escape(value)}</td></tr>'
        for label, value in rows
    )
    return f"""
        <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin: 1.5rem 0;">
            <h2 style="margin-top: 0;">🔍 Key Findings</h2>
            <table style="margin-top: 0.5rem; font-size: 0.9rem;">
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    """


def _render_probe_results(job: Any) -> str:
    """Render the full Security Probe Results table (Gaps 1/2/5).

    Shows every probed path with its HTTP status, semantic verdict badge,
    4-level display severity (only meaningful for CONFIRMED_EXPOSED rows),
    category (Security/File | Route | Static) and validation reason. Old
    jobs without the ``security_probe_results`` config key omit the section.
    """
    config = getattr(job, "config", None) or {}
    results = config.get("security_probe_results")
    if not results:
        return ""

    rows = ""
    for r in results:
        verdict = str(r.get("verdict", ""))
        http_status = r.get("http_status")
        http_str = str(http_status) if http_status is not None else "ERR"
        sev_cell = (
            _severity_badge(r.get("display_severity", "low"))
            if verdict == "confirmed_exposed"
            else '<span style="color: var(--muted);">—</span>'
        )
        rows += f"""
            <tr>
                <td><code>{html.escape(str(r.get("path", "")))}</code></td>
                <td>{html.escape(http_str)}</td>
                <td>{_verdict_badge(verdict)}</td>
                <td>{sev_cell}</td>
                <td>{html.escape(str(r.get("category", "")))}</td>
                <td style="font-size: 0.8rem; color: var(--muted);">{html.escape(str(r.get("validation_reason") or ""))}</td>
            </tr>
        """

    return f"""
        <h2 style="margin-top: 3rem;">🧪 Security Probe Results</h2>
        <p style="color: var(--muted); font-size: 0.85rem; margin-bottom: 1rem;">
            Full verdict for every probed path after SPA false-positive elimination.
            Severity is shown for confirmed exposures only.
        </p>
        <table style="margin-top: 1rem;">
            <thead>
                <tr>
                    <th style="width: 20%">Path</th>
                    <th style="width: 8%">HTTP</th>
                    <th style="width: 14%">Verdict</th>
                    <th style="width: 10%">Severity</th>
                    <th style="width: 13%">Category</th>
                    <th style="width: 35%">Reason</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    """


def _render_results_by_category(job: Any, registry: Any) -> str:
    """Render per-category progress bars with OK / issue counts.

    Categories: Security/File, Security/Route, Security/Static (from probe
    results — omitted when the job has no probe data), plus Pages and API
    (from the endpoint registry).
    """
    config = getattr(job, "config", None) or {}
    probe_results = config.get("security_probe_results") or []
    endpoints = registry.get_all()

    categories: list[tuple[str, int, int]] = []  # (label, ok_count, issue_count)

    for cat in ("Security/File", "Security/Route", "Security/Static"):
        rows = [r for r in probe_results if r.get("category") == cat]
        if not rows:
            continue
        issue_count = sum(1 for r in rows if r.get("verdict") in ("confirmed_exposed", "server_error"))
        categories.append((cat, len(rows) - issue_count, issue_count))

    def _split_ok_issue(eps: list[Any]) -> tuple[int, int]:
        bad = sum(1 for ep in eps if ep.last_status_code is not None and ep.last_status_code >= 400)
        return len(eps) - bad, bad

    api_eps = [ep for ep in endpoints if categorize_discovery_source(ep)[1] == "api"]
    pages = [ep for ep in endpoints if categorize_discovery_source(ep)[1] != "api"]
    if pages:
        categories.append(("Pages", *_split_ok_issue(pages)))
    if api_eps:
        categories.append(("API", *_split_ok_issue(api_eps)))

    if not categories:
        return ""

    bars = ""
    for label, ok, issue_count in categories:
        total = ok + issue_count
        pct = int(ok / total * 100) if total else 100
        bar_color = "#059669" if issue_count == 0 else "#f97316"
        bars += f"""
        <div style="margin-bottom: 0.75rem;">
            <div style="display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 2px;">
                <strong>{html.escape(label)}</strong>
                <span style="color: var(--muted);">{ok} OK / {issue_count} issue(s)</span>
            </div>
            <div style="height: 8px; background: var(--border); border-radius: 4px; overflow: hidden;">
                <div style="height: 100%; width: {pct}%; background: {bar_color}; border-radius: 4px;"></div>
            </div>
        </div>
        """

    return f"""
        <h2 style="margin-top: 3rem;">📦 Results by Category</h2>
        {bars}
    """


def _render_recommendations(registry: Any, issues: list[Any], job: Any) -> str:
    """Render holistic recommendations grouped by theme.

    Groups: Critical Fixes (blocking issues), Critical Security (confirmed
    probe exposures), SPA Routing (informational — unmatched client-side
    routes), QA (functional improvements) and Positive Findings.
    """
    config = getattr(job, "config", None) or {}
    services = config.get("external_services", [])

    critical_fixes: list[str] = []
    improvements: list[str] = []
    positives: list[str] = []

    # Critical fixes: blocking issues + broken external services
    for iss in issues:
        sev = str(getattr(iss.severity, "value", iss.severity)).lower()
        if sev == "blocking":
            critical_fixes.append(
                f"Fix {getattr(iss.category, 'value', iss.category)} on {iss.first_seen_url or 'multiple pages'}: {iss.title}"
            )
    for svc in services:
        if svc.get("status") in ("broken", "blocked-by-csp"):
            critical_fixes.append(f"Whitelist {svc.get('domain', '')} in CSP or remove the broken integration")

    # Critical security: confirmed probe exposures (Gap 2). Only
    # CONFIRMED_EXPOSED verdicts land here — false positives / SPA fallback
    # shells never generate recommendations.
    probe_results = config.get("security_probe_results") or []
    critical_security: list[str] = []
    for r in probe_results:
        if r.get("verdict") == "confirmed_exposed":
            reason = r.get("validation_reason") or "confirmed real file"
            critical_security.append(
                f"Restrict or remove {r.get('path', '')} ({r.get('display_severity', 'low')} severity — {reason})"
            )

    # SPA routing: client-side routes the router could not match. Split by
    # how the path was discovered:
    # - Organically-linked paths (the app's own DOM/sitemap references
    #   them) are genuine broken internal links → QA fixes.
    # - Probe/wordlist-only paths are informational — the HTTP shell
    #   returned 200; the dead routes should be added to the router or
    #   served as real 404s.
    spa_routing: list[str] = []
    spa_404s = config.get("spa_route_404s") or []
    if spa_404s:
        router_label = {
            "angular": "Angular router",
            "react": "React Router",
            "vue": "Vue Router",
            "nextjs": "Next.js router",
        }.get(config.get("spa_type", ""), "client-side router")
        broken_link_urls = {ep.url for ep in registry.get_all() if _is_broken_internal_link(ep)}
        for url in sorted({e.get("url", "") for e in spa_404s if e.get("url")})[:10]:
            if url in broken_link_urls:
                improvements.append(
                    f"Fix broken internal link: {url} — the app links to it, "
                    f"but the {router_label} cannot match the route"
                )
            else:
                spa_routing.append(f"Add route to {router_label} or serve a catch-all 404 for: {url}")

    # Improvements: functional issues + form anti-patterns + 404s
    for iss in issues:
        sev = str(getattr(iss.severity, "value", iss.severity)).lower()
        cat = str(getattr(iss.category, "value", iss.category)).lower()
        if sev == "functional":
            improvements.append(f"Fix {cat}: {iss.title}")
        elif "antipattern" in str(iss.title).lower():
            improvements.append("Change sensitive forms (contact/login/register) from GET to POST method")

    # Deduplicate improvements
    improvements = list(dict.fromkeys(improvements))[:10]

    # Positives: pages with good a11y, valid sitemaps, robots.txt present
    endpoints = registry.get_all()
    a11y_perfect = sum(
        1
        for ep in endpoints
        if hasattr(ep, "a11y_report") and ep.a11y_report and getattr(ep.a11y_report, "score", 0) >= 100
    )
    if a11y_perfect > 0:
        positives.append(f"{a11y_perfect} pages achieved 100/100 accessibility score")

    sitemap_data = config.get("sitemap_verification", {})
    if sitemap_data and sitemap_data.get("verified", 0) > 0:
        positives.append(f"{sitemap_data['verified']} sitemap URLs verified as valid")

    robots_endpoints = [ep for ep in endpoints if "robots.txt" in ep.url]
    if robots_endpoints:
        positives.append("robots.txt is present and parseable")

    llms_endpoints = [ep for ep in endpoints if "llms.txt" in ep.url]
    if llms_endpoints:
        positives.append("llms.txt is present (AI crawler instructions)")

    total_endpoints = len(endpoints)
    if total_endpoints > 100:
        positives.append(f"Comprehensive crawl coverage: {total_endpoints} endpoints discovered")

    def _render_list(items: list[str], color: str) -> str:
        if not items:
            return '<p style="color: var(--muted); font-size: 0.85rem;">None found.</p>'
        items_html = "".join(f"<li>{html.escape(item)}</li>" for item in items)
        return f'<ul style="margin-left: 1.5rem;">{items_html}</ul>'

    return f"""
        <h2 style="margin-top: 3rem;">📋 Recommendations</h2>

        <div style="background: var(--card); border: 1px solid var(--border); border-left: 3px solid #ef4444; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem;">
            <h3 style="color: #ef4444; margin-top: 0;">🔴 Critical Fixes ({len(critical_fixes)})</h3>
            {_render_list(critical_fixes, "#ef4444")}
        </div>

        <div style="background: var(--card); border: 1px solid var(--border); border-left: 3px solid #ef4444; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem;">
            <h3 style="color: #ef4444; margin-top: 0;">🚨 Critical Security ({len(critical_security)})</h3>
            {_render_list(critical_security, "#ef4444")}
        </div>

        <div style="background: var(--card); border: 1px solid var(--border); border-left: 3px solid #3b82f6; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem;">
            <h3 style="color: #3b82f6; margin-top: 0;">🔵 SPA Routing — informational ({len(spa_routing)})</h3>
            {_render_list(spa_routing, "#3b82f6")}
        </div>

        <div style="background: var(--card); border: 1px solid var(--border); border-left: 3px solid #f97316; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem;">
            <h3 style="color: #f97316; margin-top: 0;">🟠 QA ({len(improvements)})</h3>
            {_render_list(improvements, "#f97316")}
        </div>

        <div style="background: var(--card); border: 1px solid var(--border); border-left: 3px solid #059669; border-radius: 8px; padding: 1.5rem;">
            <h3 style="color: #059669; margin-top: 0;">🟢 Positive Findings ({len(positives)})</h3>
            {_render_list(positives, "#059669")}
        </div>
    """


# ---------------------------------------------------------------------------
# DeepSeek-parity: Page categorization + technical findings rendering
# ---------------------------------------------------------------------------

# Category → list of path-substring matchers (case-insensitive).
# Order matters: first match wins. Mirrors DeepSeek's page grouping.
# Note: Core matchers are exact/specific to avoid matching every path.
_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    (
        "Assets",
        [".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".ttf", ".ico", ".webp", ".mp4", ".pdf"],
    ),
    (
        "Auth",
        [
            "/login",
            "/signin",
            "/signup",
            "/register",
            "/auth",
            "/dashboard",
            "/account",
            "/profile",
            "/logout",
            "/reset",
            "/password",
            "/wp-admin",
            "/wp-login",
        ],
    ),
    ("API", ["/api", "/graphql", "/webhook", "/callback", "/oauth"]),
    (
        "Courses",
        ["/course", "/training", "/class", "/lesson", "/curriculum", "/module", "/tutorial", "/learn", "/academy"],
    ),
    ("City-Specific", ["/city/", "/location/", "/region/", "/in-", "-city", "/near-", "/local/"]),
    (
        "AI-ML",
        [
            "/ai",
            "/ml",
            "/machine-learning",
            "/artificial-intelligence",
            "/data-science",
            "/deep-learning",
            "/neural",
            "/llm",
        ],
    ),
    ("Fundamentals", ["/fundamental", "/basics", "/foundation", "/intro", "/beginner", "/starter", "/prerequisite"]),
    (
        "Core",
        [
            "/home",
            "/about",
            "/contact",
            "/privacy",
            "/terms",
            "/legal",
            "/cookie",
            "/sitemap",
            "/robots",
            "/llms.txt",
            "/ads.txt",
            "/humans.txt",
        ],
    ),
]


def categorize_url(url: str) -> str:
    """Classify a URL into a DeepSeek-style page category.

    Returns one of: Core, Courses, City-Specific, AI-ML, Fundamentals,
    Auth, API, Assets, or Other. The root path ``/`` is classified as Core.
    """
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return "Other"
    if path == "" or path == "/":
        return "Core"
    for category, matchers in _CATEGORY_RULES:
        if any(m in path for m in matchers):
            return category
    return "Other"


def categorize_discovery_source(ep: Any) -> tuple[str, str]:
    """Classify how an endpoint was discovered for filterable report views.

    DeepSeek report Step 5: filterable category views (Seed, Discovered,
    Security/Common, Assets, Sitemap, API).

    Returns a (label, filter_value) tuple. The filter_value is a lowercase
    slug used as the ``data-source`` attribute on the table row so the
    report's JS filter buttons can show/hide rows by source.
    """
    # Lazy import to avoid a circular dependency at module load time.
    try:
        from src.core.endpoint_registry import EndpointSource, EndpointType
    except Exception:
        EndpointSource = None  # type: ignore[assignment]
        EndpointType = None  # type: ignore[assignment]

    source = getattr(ep, "source", None)
    etype = getattr(ep, "endpoint_type", None)

    # Static assets → Assets bucket
    if EndpointType is not None and etype == EndpointType.STATIC_ASSET:
        return "Assets", "assets"

    # Security probe endpoints (probed, not linked from any page)
    if EndpointSource is not None and source == EndpointSource.USER_PROVIDED:
        # The security probe registers paths as USER_PROVIDED with the
        # target base URL; treat them as Security/Common.
        return "Security/Common", "security"
    if EndpointSource is not None and source == EndpointSource.API_BRUTE_FORCE:
        return "Security/Common", "security"

    # Sitemap-discovered
    if EndpointSource is not None and source in (EndpointSource.SITEMAP_XML, EndpointSource.ROBOTS_TXT):
        return "Sitemap", "sitemap"

    # API endpoints (network capture, GraphQL, OpenAPI)
    if EndpointSource is not None and source in (
        EndpointSource.NETWORK_CAPTURE,
        EndpointSource.GRAPHQL_INTROSPECTION,
        EndpointSource.OPENAPI_SPEC,
    ):
        return "API", "api"
    if EndpointType is not None and etype in (EndpointType.API, EndpointType.GRAPHQL):
        return "API", "api"

    # Seed endpoints (the user-provided start URL + DOM links from the seed)
    if EndpointSource is not None and source in (EndpointSource.DOM_LINK, EndpointSource.DOM_RESOURCE):
        return "Discovered", "discovered"

    # JS analysis, source maps, HTML comments, hidden inputs → Discovered
    if EndpointSource is not None and source in (
        EndpointSource.JS_ANALYSIS,
        EndpointSource.SOURCE_MAP,
        EndpointSource.HTML_COMMENT,
        EndpointSource.HIDDEN_INPUT,
        EndpointSource.REDIRECT,
        EndpointSource.HEADER_LINK,
    ):
        return "Discovered", "discovered"

    # Fallback
    return "Seed", "seed"


def _render_technical_findings(job: Any) -> str:
    """Render the Technical Findings section (CMS, robots.txt, sitemaps, forms).

    Mirrors DeepSeek's "Technical Findings" cards. Reads from
    ``job.config["technical_findings"]`` populated by
    ``src.analysis.technical_analysis.run_technical_analysis``.
    """
    config = getattr(job, "config", None) or {}
    findings = config.get("technical_findings") or {}
    if not findings:
        return ""

    cards: list[str] = []

    # CMS detection card
    cms = findings.get("cms") or {}
    if cms:
        detected = html.escape(str(cms.get("detected", "Unknown")))
        version = cms.get("version")
        version_str = f" <span style='color: var(--muted);'>(v{html.escape(str(version))})</span>" if version else ""
        evidence = cms.get("evidence") or []
        evidence_html = ""
        if evidence:
            evidence_items = "".join(f"<li>{html.escape(str(e))}</li>" for e in evidence[:5])
            evidence_html = (
                f"<ul style='margin: 0.5rem 0 0 1.5rem; font-size: 0.8rem; color: var(--muted);'>{evidence_items}</ul>"
            )
        cards.append(f"""
            <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
                <h3 style="margin-top: 0; color: #60a5fa;">🖥️ CMS / Platform Detection</h3>
                <p style="margin: 0.5rem 0;"><strong>Detected:</strong> {detected}{version_str}</p>
                {evidence_html}
            </div>
        """)

    # robots.txt card
    robots = findings.get("robots") or {}
    if robots:
        present = robots.get("present", False)
        status = "✅ Present" if present else "❌ Not found"
        allowed = robots.get("ai_crawlers_allowed") or []
        disallowed = robots.get("ai_crawlers_disallowed") or []
        disallowed_paths = robots.get("disallowed_paths") or []
        sitemaps_declared = robots.get("sitemaps_declared") or []

        allowed_html = (
            ", ".join(html.escape(a) for a in allowed)
            if allowed
            else "<span style='color: var(--muted);'>None explicitly allowed</span>"
        )
        disallowed_html = (
            ", ".join(html.escape(d) for d in disallowed) if disallowed else "<span style='color: #059669;'>None</span>"
        )
        paths_html = (
            ", ".join(html.escape(p) for p in disallowed_paths[:10])
            if disallowed_paths
            else "<span style='color: var(--muted);'>None</span>"
        )
        sitemaps_html = (
            "".join(f"<li>{html.escape(s)}</li>" for s in sitemaps_declared)
            if sitemaps_declared
            else "<li style='color: var(--muted);'>None declared</li>"
        )

        cards.append(f"""
            <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
                <h3 style="margin-top: 0; color: #60a5fa;">🤖 robots.txt Analysis</h3>
                <p style="margin: 0.5rem 0;"><strong>Status:</strong> {status}</p>
                <p style="margin: 0.25rem 0; font-size: 0.85rem;"><strong>AI Crawlers Allowed:</strong> {allowed_html}</p>
                <p style="margin: 0.25rem 0; font-size: 0.85rem;"><strong>AI Crawlers Disallowed:</strong> {disallowed_html}</p>
                <p style="margin: 0.25rem 0; font-size: 0.85rem;"><strong>Disallowed Paths ({len(disallowed_paths)}):</strong> {paths_html}</p>
                <p style="margin: 0.5rem 0 0.25rem; font-size: 0.85rem;"><strong>Sitemaps Declared:</strong></p>
                <ul style="margin: 0 0 0 1.5rem; font-size: 0.8rem;">{sitemaps_html}</ul>
            </div>
        """)

    # Sitemap catalog card
    sitemaps = findings.get("sitemaps") or {}
    if sitemaps:
        total = sitemaps.get("total", 0)
        entries = sitemaps.get("entries") or []
        entries_html = ""
        for entry in entries[:10]:
            name = html.escape(str(entry.get("name", "")))
            url = html.escape(str(entry.get("url", "")))
            etype = html.escape(str(entry.get("type", "")))
            url_count = entry.get("url_count")
            count_str = f" ({url_count} URLs)" if url_count else ""
            entries_html += f"<tr><td style='padding: 4px 8px;'>{name}</td><td style='padding: 4px 8px; font-size: 0.8rem; color: var(--muted);'>{url}</td><td style='padding: 4px 8px;'><span class='badge advisory'>{etype}</span>{count_str}</td></tr>"
        cards.append(f"""
            <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
                <h3 style="margin-top: 0; color: #60a5fa;">🗺️ Sitemap Structure ({total} sitemap{"s" if total != 1 else ""})</h3>
                {"<table style='width: 100%; font-size: 0.85rem; margin-top: 0.5rem;'><thead><tr><th style='text-align: left; padding: 4px 8px;'>Name</th><th style='text-align: left; padding: 4px 8px;'>URL</th><th style='text-align: left; padding: 4px 8px;'>Type</th></tr></thead><tbody>" + entries_html + "</tbody></table>" if entries else "<p style='color: var(--muted); font-size: 0.85rem;'>No sitemaps discovered.</p>"}
            </div>
        """)

    # Form analysis card
    forms = findings.get("forms") or {}
    if forms:
        total_forms = forms.get("total_forms", 0)
        csrf = forms.get("with_csrf_token", 0)
        honeypot = forms.get("with_honeypot", 0)
        antipattern = forms.get("antipattern_forms", 0)
        avg_fields = forms.get("avg_fields", 0.0)
        csrf_color = "#059669" if csrf > 0 else "#ef4444"
        cards.append(f"""
            <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
                <h3 style="margin-top: 0; color: #60a5fa;">📝 Contact Form Analysis</h3>
                <p style="margin: 0.5rem 0; font-size: 0.85rem;"><strong>Total Forms:</strong> {total_forms}</p>
                <p style="margin: 0.25rem 0; font-size: 0.85rem;"><strong>CSRF Protected:</strong> <span style="color: {csrf_color};">{csrf} / {total_forms}</span></p>
                <p style="margin: 0.25rem 0; font-size: 0.85rem;"><strong>Honeypot Fields:</strong> {honeypot}</p>
                <p style="margin: 0.25rem 0; font-size: 0.85rem;"><strong>GET-method Antipatterns:</strong> {antipattern}</p>
                <p style="margin: 0.25rem 0; font-size: 0.85rem;"><strong>Avg Fields per Form:</strong> {avg_fields:.1f}</p>
            </div>
        """)

    if not cards:
        return ""

    return f"""
        <h2 style="margin-top: 3rem;">🔬 Technical Findings</h2>
        {"".join(cards)}
    """


def _render_lighthouse_summary(registry: Any, job: Any) -> str:
    """Lighthouse-style performance summary: seed score + per-page averages.

    Sources: job.config['lighthouse_seed'] (real CLI when enabled) and each
    endpoint's performance_data[...]['lighthouse'] (computed, per browser).
    """
    lh_seed = (getattr(job, "config", None) or {}).get("lighthouse_seed") or {}
    per_page = []
    for ep in registry.get_all():
        pdata = getattr(ep, "performance_data", None) or {}
        for _browser, data in pdata.items():
            lh = (data or {}).get("lighthouse") or {}
            if lh:
                per_page.append(lh)

    if not lh_seed and not per_page:
        return ""

    def _badge(score: int, label: str, note: str = "") -> str:
        color = "#059669" if score >= 90 else ("#eab308" if score >= 50 else "#ef4444")
        return (
            f'<div style="background: var(--card); border: 1px solid var(--border); '
            f'border-radius: 8px; padding: 1rem; min-width: 160px;">'
            f'<div style="color: {color}; font-size: 2rem; font-weight: 700;">{score}</div>'
            f'<div style="font-size: 0.85rem; font-weight: 600; color: var(--muted);">{label}</div>'
            f'<div style="font-size: 0.7rem; color: var(--muted);">{note}</div></div>'
        )

    cards = ""
    if lh_seed:
        src = "Lighthouse CLI" if lh_seed.get("source") == "lighthouse" else "computed"
        cards += _badge(int(lh_seed.get("score") or 0), "Seed page", f"source: {src}")
    if per_page:
        avg = round(sum(int(p.get("score") or 0) for p in per_page) / len(per_page))
        worst = min(per_page, key=lambda p: int(p.get("score") or 0))
        cards += _badge(avg, "All pages (avg)", f"{len(per_page)} samples")
        cards += _badge(int(worst.get("score") or 0), "Worst page", "optimization target")

    # metric legend with grades
    legend = ""
    sample = lh_seed or (per_page[0] if per_page else {})
    for name, m in (sample.get("metrics") or {}).items():
        if m.get("value") is None:
            continue
        pretty = name.replace("_", " ").title()
        g = m.get("grade", "")
        g_color = {"good": "#059669", "needs-improvement": "#eab308", "poor": "#ef4444"}.get(g, "#3b82f6")
        legend += (
            f'<span style="font-size:0.75rem; color: var(--muted); margin-right: 0.9rem;">'
            f"{pretty}: <strong>{m['value']}</strong>"
            f'<span style="color:{g_color};"> ({g})</span></span>'
        )

    src_note = (
        "Lighthouse CLI audit"
        if lh_seed.get("source") == "lighthouse"
        else "Computed from live crawl metrics (Lighthouse v10 curve) — set "
        "LIGHTHOUSE_ENABLED + install the lighthouse CLI for a full audit"
    )
    return f"""
        <h2 style="margin-top: 3rem;">&amp;#9889; Performance (Lighthouse-style)</h2>
        <p style="color: var(--muted); font-size: 0.85rem;">{src_note}</p>
        <div style="display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.8rem;">{cards}</div>
        <div>{legend}</div>
    """


def generate_static_html_report(job_id: str, job: Any, registry: Any, issues: list[Any]) -> str:
    """Generate a static HTML report presenting crawled endpoints in a master table with statuses, screenshots, and errors."""

    table_rows = ""
    endpoints = registry.get_all()

    # Probe verdict lookup for SPA-fallback endpoints (Gap 1): both the probe
    # path and its full URL map to the stored verdict, so crawled endpoints
    # covered by the security probe reuse it in the status cell.
    _cfg = getattr(job, "config", None) or {}
    probe_verdicts: dict[str, str] = {}
    for _r in _cfg.get("security_probe_results") or []:
        if _r.get("path"):
            probe_verdicts[_r["path"]] = _r.get("verdict", "")
        if _r.get("url"):
            probe_verdicts[_r["url"]] = _r.get("verdict", "")

    for ep in endpoints:
        # Determine Working status
        status_text = "Working"
        status_color = "#059669"
        code_desc = ""

        if ep.last_status_code is not None:
            code_desc = f" (HTTP {ep.last_status_code})"
            if ep.last_status_code >= 400:
                status_text = "Broken"
                status_color = "#ef4444"
        elif ep.status.name if hasattr(ep.status, "name") else ep.status == "failed":
            status_text = "Broken"
            status_color = "#ef4444"

        # DeepSeek report Step 5: Effective status display.
        # The HTTP status may be 200 (SPA shell loaded) but the client-side
        # router could not match the path — an effective 404. When the path
        # is organically linked (the app's own DOM/sitemap references it),
        # this is a genuine broken internal link: the row renders "Broken"
        # with a red BROKEN LINK badge. Probe-only paths keep their real
        # HTTP status and get a semantic verdict badge (Gap 1): the probe's
        # stored verdict when the path was probed, else a plain SPA_FALLBACK
        # badge (secure — not a broken page). Real HTTP ≥400 still renders
        # "Broken".
        effective_code = ep.extra.get("effective_status_code") if hasattr(ep, "extra") else None
        effective_reason = ep.extra.get("effective_status_reason") if hasattr(ep, "extra") else None
        effective_detail = ep.extra.get("effective_status_detail") if hasattr(ep, "extra") else None
        effective_html = ""
        if effective_code:
            ep_path = urlparse(ep.url).path or "/"
            if _is_broken_internal_link(ep):
                verdict = "broken_link"
                status_text = "Broken"
                status_color = "#ef4444"
            else:
                verdict = probe_verdicts.get(ep.url) or probe_verdicts.get(ep_path) or "spa_fallback"
            eff_text = f"Effective {effective_code}"
            if effective_reason:
                eff_text = f"Effective {effective_code} ({effective_reason})"
            effective_html = (
                f'<div style="margin-top: 4px; padding: 2px 6px; '
                f"background: rgba(59, 130, 246, 0.12); border-left: 3px solid #3b82f6; "
                f'border-radius: 3px; font-size: 0.75rem; color: #93c5fd;" '
                f'title="{html.escape(effective_detail or effective_reason or "")}">'
                f"{_verdict_badge(verdict)} {html.escape(eff_text)}</div>"
            )

        status_html = (
            f'<span style="color: {status_color}; font-weight: bold;">{status_text}{code_desc}</span>{effective_html}'
        )

        # Load Screenshots — size-optimized: embed small JPEG thumbnail as
        # base64 (single embedding, ~15-40KB each) and link to the full PNG
        # on disk via relative path. This reduces report HTML from ~250MB
        # to a few MB without losing any screenshot functionality.
        snapshots_html = ""
        if hasattr(ep, "snapshots") and ep.snapshots:
            thumbnails = getattr(ep, "thumbnails", {}) or {}
            for browser, path in ep.snapshots.items():
                if not os.path.exists(path):
                    continue
                # Prefer the small JPEG thumbnail for embedding
                thumb_path = thumbnails.get(browser, "")
                embed_path = thumb_path if (thumb_path and os.path.exists(thumb_path)) else path
                # Compute relative path from report output dir to the full screenshot
                try:
                    full_rel = os.path.relpath(path, "reports")
                    full_rel = full_rel.replace("\\", "/")
                except Exception:
                    full_rel = path
                with open(embed_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
                mime = "image/jpeg" if embed_path.lower().endswith((".jpg", ".jpeg")) else "image/png"
                snapshots_html += f"""
                        <div style="margin-bottom: 8px;">
                            <span style="font-size: 0.7rem; color: var(--muted); text-transform: uppercase; font-weight: bold;">{browser}</span><br/>
                            <a href="{full_rel}" target="_blank">
                                <img src="data:{mime};base64,{encoded_string}" style="max-width: 200px; border: 1px solid var(--border); border-radius: 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.5); cursor: pointer;" title="Click to view full image" />
                            </a>
                        </div>
                        """
        if not snapshots_html:
            snapshots_html = '<span style="color: var(--muted); font-size: 0.85rem;">No screenshot captured</span>'

        # Get Console Errors for this specific endpoint URL
        errors_html = ""
        ep_issues = [iss for iss in issues if iss.first_seen_url == ep.url]
        if ep_issues:
            for iss in ep_issues:
                severity_class = str(iss.severity.value if hasattr(iss.severity, "value") else iss.severity).lower()
                errors_html += f"""
                <div style="margin-bottom: 12px; padding: 8px; border-left: 3px solid {get_severity_color(severity_class)}; background: rgba(255,255,255,0.02); border-radius: 4px;">
                    <span class="badge {severity_class}">{severity_class.upper()}</span> <strong>{iss.title}</strong><br/>
                    <div style="margin-top: 4px; color: var(--muted); font-size: 0.8rem;">{iss.description.split("AI Fix Explanation:")[0].strip()}</div>
                    <pre style="margin: 8px 0 0 0; background: rgba(0,0,0,0.3); padding: 6px; border-radius: 3px; font-size: 0.75rem; word-break: break-all; white-space: pre-wrap; font-family: monospace; color: #ff5555;">{iss.error_message}</pre>
                </div>
                """
        else:
            errors_html = '<span style="color: #059669; font-weight: 500;">✓ No console errors detected</span>'

        # Build Accessibility column
        a11y_html = '<span style="color: var(--muted); font-size: 0.85rem;">Not scanned</span>'
        a11y_report = getattr(ep, "a11y_report", None)
        if a11y_report:
            score_color = (
                "#059669" if a11y_report.score >= 80 else ("#eab308" if a11y_report.score >= 50 else "#ef4444")
            )
            a11y_html = f'<div style="margin-bottom: 8px;"><strong style="color: {score_color}; font-size: 1.2rem;">{a11y_report.score:.0f}</strong><span style="color: var(--muted); font-size: 0.75rem;"> / 100</span></div>'
            if a11y_report.violations:
                a11y_html += f'<div style="font-size: 0.8rem; color: var(--muted); margin-bottom: 4px;">{a11y_report.total_violations} violation(s)</div>'
                for v in a11y_report.violations[:3]:  # Show top 3
                    impact_color = {
                        "critical": "#ef4444",
                        "serious": "#f97316",
                        "moderate": "#eab308",
                        "minor": "#3b82f6",
                    }.get(v.impact, "#a1a1aa")
                    a11y_html += f'<div style="padding: 4px 0; font-size: 0.75rem;"><span style="color: {impact_color}; font-weight: bold;">[{v.impact.upper()}]</span> {v.rule_id} ({v.affected_nodes} nodes)</div>'
            else:
                a11y_html += '<span style="color: #059669; font-size: 0.8rem;">✓ No violations</span>'

        # Build Performance column
        perf_html = '<span style="color: var(--muted); font-size: 0.85rem;">No data</span>'
        perf_data = getattr(ep, "performance_data", None)
        if perf_data:
            for _browser_name, pdata in perf_data.items():
                nav = pdata.get("nav_timing", {})
                cdp = pdata.get("cdp_metrics", {})
                ttfb = nav.get("responseStart", 0)
                dom_complete = nav.get("domComplete", 0)
                load_time = nav.get("loadEventEnd", 0)
                heap_mb = cdp.get("JSHeapUsedSize", 0) / (1024 * 1024) if cdp.get("JSHeapUsedSize") else 0

                ttfb_color = "#059669" if ttfb < 800 else ("#eab308" if ttfb < 1800 else "#ef4444")
                load_color = "#059669" if load_time < 3000 else ("#eab308" if load_time < 5000 else "#ef4444")

                # Lighthouse-style score badge (from src.analysis.lighthouse_scorer)
                lh = pdata.get("lighthouse") or {}
                lh_html = ""
                if lh:
                    score = int(lh.get("score") or 0)
                    lh_color = "#059669" if score >= 90 else ("#eab308" if score >= 50 else "#ef4444")
                    src_note = "LH" if lh.get("source") == "lighthouse" else "LH*"
                    lh_html = (
                        f'<div style="margin-bottom:4px;">{src_note} Perf: '
                        f'<strong style="color:{lh_color};">{score}</strong>'
                        f'<span style="color:var(--muted);font-size:0.7rem;">/100</span>'
                        f"</div>"
                    )

                perf_html = f"""
                <div style="font-size: 0.8rem;">
                    {lh_html}
                    <div style="margin-bottom: 4px;">TTFB: <strong style="color: {ttfb_color};">{ttfb:.0f}ms</strong></div>
                    <div style="margin-bottom: 4px;">DOM Complete: <strong>{dom_complete:.0f}ms</strong></div>
                    <div style="margin-bottom: 4px;">Load: <strong style="color: {load_color};">{load_time:.0f}ms</strong></div>
                    <div>JS Heap: <strong>{heap_mb:.1f} MB</strong></div>
                </div>
                """
                break  # Show first browser only

        # DeepSeek-parity: page title + category for the endpoint table
        page_title = ep.extra.get("page_title", "") if hasattr(ep, "extra") else ""
        title_html = (
            f'<div style="color: var(--muted); font-size: 0.75rem; margin-top: 2px;">{html.escape(page_title)}</div>'
            if page_title
            else ""
        )
        category = categorize_url(ep.url)
        category_colors = {
            "Core": "#60a5fa",
            "Courses": "#a78bfa",
            "City-Specific": "#f472b6",
            "AI-ML": "#34d399",
            "Fundamentals": "#fbbf24",
            "Auth": "#f97316",
            "API": "#22d3ee",
            "Assets": "#a1a1aa",
            "Other": "#a1a1aa",
        }
        cat_color = category_colors.get(category, "#a1a1aa")

        # DeepSeek report Step 5: Discovery source badge for filterable views.
        # Classify how the endpoint was discovered so the report can be filtered
        # by Seed / Discovered / Security-Common / Assets.
        source_label, source_filter = categorize_discovery_source(ep)
        source_colors = {
            "Seed": "#60a5fa",
            "Discovered": "#a78bfa",
            "Security/Common": "#f97316",
            "Assets": "#a1a1aa",
            "Sitemap": "#34d399",
            "API": "#22d3ee",
        }
        src_color = source_colors.get(source_label, "#a1a1aa")

        table_rows += f"""
        <tr data-source="{source_filter}" data-category="{category.lower()}" data-status="{status_text.lower().replace(" ", "-")}">
            <td>
                <span class="method-badge {ep.method}">{ep.method}</span>
                <strong style="word-break: break-all; font-size: 0.9rem;">{ep.url}</strong>
                {title_html}
            </td>
            <td>{status_html}</td>
            <td><span class="badge" style="background-color: {cat_color}; color: {"black" if category in ("Fundamentals", "Assets", "Other") else "white"};">{category}</span></td>
            <td><span class="badge" style="background-color: {src_color}; color: {"black" if source_label in ("Assets",) else "white"};">{source_label}</span></td>
            <td>
                <div style="display: flex; flex-direction: column; gap: 8px;">
                    {snapshots_html}
                </div>
            </td>
            <td>{errors_html}</td>
            <td>{a11y_html}</td>
            <td>{perf_html}</td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Crawl Testing Report - Job {job_id}</title>
        <style>
            :root {{
                --bg: #09090b;
                --card: #18181b;
                --text: #fafafa;
                --muted: #a1a1aa;
                --border: #27272a;
                --primary: #3b82f6;
            }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                background-color: var(--bg);
                color: var(--text);
                margin: 0;
                padding: 2rem;
                line-height: 1.5;
            }}
            h1, h2, h3 {{ font-weight: 600; margin-top: 0; }}
            .header {{
                margin-bottom: 2rem;
                padding-bottom: 1rem;
                border-bottom: 1px solid var(--border);
            }}

            /* Table Styling */
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 2rem;
                font-size: 0.9rem;
            }}
            th, td {{
                padding: 1rem;
                vertical-align: top;
                text-align: left;
                border-bottom: 1px solid var(--border);
            }}
            th {{
                background-color: var(--card);
                color: var(--muted);
                font-weight: 600;
                text-transform: uppercase;
                font-size: 0.75rem;
                letter-spacing: 0.05em;
            }}
            tr:hover {{
                background-color: rgba(255, 255, 255, 0.01);
            }}

            /* Badges */
            .method-badge {{
                display: inline-block;
                padding: 0.2rem 0.4rem;
                border-radius: 4px;
                font-size: 0.75rem;
                font-weight: bold;
                background-color: var(--border);
                margin-right: 0.5rem;
            }}
            .method-badge.GET {{ background-color: rgba(5, 150, 105, 0.2); color: #059669; border: 1px solid rgba(5, 150, 105, 0.3); }}
            .method-badge.POST {{ background-color: rgba(37, 99, 235, 0.2); color: #3b82f6; border: 1px solid rgba(37, 99, 235, 0.3); }}

            .badge {{
                display: inline-block;
                padding: 0.125rem 0.375rem;
                border-radius: 4px;
                font-size: 0.7rem;
                font-weight: bold;
            }}
            .badge.blocking {{ background-color: #ef4444; color: white; }}
            .badge.functional {{ background-color: #f97316; color: white; }}
            .badge.visual {{ background-color: #eab308; color: black; }}
            .badge.advisory {{ background-color: #3b82f6; color: white; }}

            /* Probe verdict badges (Gap 1) — semantic colors */
            .badge.verdict-false-positive {{ background-color: #059669; color: white; }}
            .badge.verdict-spa-fallback {{ background-color: #3b82f6; color: white; }}
            .badge.verdict-broken-link {{ background-color: #ef4444; color: white; }}
            .badge.verdict-not-found {{ background-color: #6b7280; color: white; }}
            .badge.verdict-confirmed-exposed {{ background-color: #ef4444; color: white; }}
            .badge.verdict-network-error {{ background-color: #eab308; color: black; }}
            .badge.verdict-protected {{ background-color: #6b7280; color: white; }}
            .badge.verdict-server-error {{ background-color: #f97316; color: white; }}

            /* 4-level display severity badges (Gap 2) */
            .badge.sev-critical {{ background-color: #ef4444; color: white; }}
            .badge.sev-high {{ background-color: #f97316; color: white; }}
            .badge.sev-medium {{ background-color: #eab308; color: black; }}
            .badge.sev-low {{ background-color: #6b7280; color: white; }}

            /* Filter + Export buttons (DeepSeek report Step 5) */
            .filter-btn, .export-btn {{
                padding: 0.3rem 0.7rem;
                border-radius: 4px;
                font-size: 0.8rem;
                font-weight: 600;
                cursor: pointer;
                border: 1px solid var(--border);
                background-color: var(--card);
                color: var(--text);
                transition: all 0.15s;
            }}
            .filter-btn:hover, .export-btn:hover {{
                border-color: var(--primary);
                color: #60a5fa;
            }}
            .filter-btn.active {{
                background-color: var(--primary);
                color: white;
                border-color: var(--primary);
            }}
            .export-btn {{
                background-color: rgba(72, 187, 120, 0.15);
                color: #34d399;
                border-color: rgba(72, 187, 120, 0.3);
            }}
            @media print {{
                .filter-btn, .export-btn {{ display: none; }}
                body {{ padding: 0; background: white; color: black; }}
                :root {{ --bg: white; --card: #f5f5f5; --text: black; --muted: #555; --border: #ccc; }}
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Crawl Testing Report</h1>
            <p><strong>Job ID:</strong> {job_id}</p>
            <p><strong>Target:</strong> {job.url}</p>
            <p><strong>Status:</strong> Completed</p>
        </div>

        {_render_summary_stats(registry, job)}
        {_render_health_score(registry, issues)}
        {_render_lighthouse_summary(registry, job)}
        {_render_key_findings(job, registry)}
        {_render_sitemap_verification(job)}
        {_render_external_services(job)}

        <h2>Crawled Endpoints Overview</h2>
        <div style="margin: 1rem 0; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
            <strong style="color: var(--muted); font-size: 0.85rem;">Filter by source:</strong>
            <button onclick="filterBySource('all')" class="filter-btn active" data-filter="all">All</button>
            <button onclick="filterBySource('seed')" class="filter-btn" data-filter="seed">Seed</button>
            <button onclick="filterBySource('discovered')" class="filter-btn" data-filter="discovered">Discovered</button>
            <button onclick="filterBySource('security')" class="filter-btn" data-filter="security">Security/Common</button>
            <button onclick="filterBySource('sitemap')" class="filter-btn" data-filter="sitemap">Sitemap</button>
            <button onclick="filterBySource('api')" class="filter-btn" data-filter="api">API</button>
            <button onclick="filterBySource('assets')" class="filter-btn" data-filter="assets">Assets</button>
            <span style="flex: 1;"></span>
            <strong style="color: var(--muted); font-size: 0.85rem;">Export:</strong>
            <button onclick="exportReport('json')" class="export-btn">JSON</button>
            <button onclick="exportReport('txt')" class="export-btn">TXT</button>
            <button onclick="window.print()" class="export-btn">PDF (Print)</button>
        </div>
        <table id="endpoints-table">
            <thead>
                <tr>
                    <th style="width: 22%">Endpoint URL</th>
                    <th style="width: 10%">Status</th>
                    <th style="width: 9%">Category</th>
                    <th style="width: 9%">Source</th>
                    <th style="width: 12%">Screenshots</th>
                    <th style="width: 16%">Console Errors</th>
                    <th style="width: 11%">Accessibility</th>
                    <th style="width: 11%">Performance</th>
                </tr>
            </thead>
            <tbody>
                {table_rows if table_rows else '<tr><td colspan="8" style="text-align: center; color: var(--muted); padding: 3rem;">No endpoints were crawled.</td></tr>'}
            </tbody>
        </table>

        {_render_fix_suggestions(issues)}
        {_render_security_probe(issues)}
        {_render_probe_results(job)}
        {_render_results_by_category(job, registry)}
        {_render_recommendations(registry, issues, job)}
        {_render_technical_findings(job)}

        <script>
            // DeepSeek report Step 5: filterable category views + multi-format export.
            (function() {{
                const rows = document.querySelectorAll('#endpoints-table tbody tr[data-source]');
                const filterBtns = document.querySelectorAll('.filter-btn');

                window.filterBySource = function(source) {{
                    filterBtns.forEach(b => b.classList.toggle('active', b.dataset.filter === source));
                    rows.forEach(row => {{
                        const match = (source === 'all' || row.dataset.source === source);
                        row.style.display = match ? '' : 'none';
                    }});
                }};

                // Build a JSON-serializable model of the endpoints table for export.
                function buildExportModel() {{
                    const out = [];
                    rows.forEach(row => {{
                        const cells = row.querySelectorAll('td');
                        if (cells.length < 4) return;
                        out.push({{
                            url: cells[0] ? cells[0].innerText.trim() : '',
                            status: cells[1] ? cells[1].innerText.trim() : '',
                            category: cells[2] ? cells[2].innerText.trim() : '',
                            source: cells[3] ? cells[3].innerText.trim() : '',
                            console_errors: cells[5] ? cells[5].innerText.trim() : '',
                            accessibility: cells[6] ? cells[6].innerText.trim() : '',
                            performance: cells[7] ? cells[7].innerText.trim() : '',
                        }});
                    }});
                    return out;
                }}

                window.exportReport = function(format) {{
                    const model = buildExportModel();
                    const meta = {{
                        job_id: {html.escape(json.dumps(job_id))},
                        target: {html.escape(json.dumps(getattr(job, "url", "")))},
                        generated_at: new Date().toISOString(),
                        total_endpoints: model.length,
                    }};
                    let blob, filename, mime;
                    if (format === 'json') {{
                        const payload = JSON.stringify({{ meta: meta, endpoints: model }}, null, 2);
                        blob = new Blob([payload], {{ type: 'application/json' }});
                        filename = 'report_{job_id}.json';
                        mime = 'application/json';
                    }} else {{  // txt
                        const lines = [
                            'Crawl Testing Report',
                            'Job ID: ' + meta.job_id,
                            'Target: ' + meta.target,
                            'Generated: ' + meta.generated_at,
                            'Total Endpoints: ' + meta.total_endpoints,
                            '',
                            'URL\\tStatus\\tCategory\\tSource\\tConsole Errors\\tAccessibility\\tPerformance',
                        ];
                        model.forEach(e => {{
                            lines.push([e.url, e.status, e.category, e.source, e.console_errors, e.accessibility, e.performance].join('\\t'));
                        }});
                        blob = new Blob([lines.join('\\n')], {{ type: 'text/plain' }});
                        filename = 'report_{job_id}.txt';
                        mime = 'text/plain';
                    }}
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                }};
            }})();
        </script>
    </body>
    </html>
    """

    # Save the report
    reports_dir = "reports"
    os.makedirs(reports_dir, exist_ok=True)
    report_path = f"{reports_dir}/report_{job_id}.html"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return report_path
