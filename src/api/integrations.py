"""Jira + Slack integrations for crawl results.

Design (production-grade, additive):
- Slack: Block Kit messages (rich) with dashboard links; plain text fallback.
- Jira: REST API v3 issues — BLOCKING/FUNCTIONAL issues become tickets,
  deduplicated per job via a fingerprint in the description so re-crawls
  don't spam duplicates (searches before creating).
- All integration calls are non-fatal: failures are logged, never block
  the crawl/report pipeline.
- Credentials via per-monitor settings (jira_url/jira_project/jira_token
  columns) or platform-wide env defaults (JIRA_URL, JIRA_PROJECT,
  JIRA_API_TOKEN). Per-monitor wins.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── Slack ───────────────────────────────────────────────────────────────────


def build_slack_payload(job: Any, issues: list[Any], dashboard_url: str) -> dict[str, Any]:
    """Build a Slack Block Kit payload summarizing a completed crawl."""
    sev_counts: dict[str, int] = {}
    top_issues: list[tuple[str, str, str]] = []  # (severity, title, url)
    for iss in issues:
        sev = str(getattr(iss, "severity", "advisory") or "advisory")
        sev_counts[sev] = sev_counts.get(sev, 0) + getattr(iss, "occurrence_count", 1)
        if len(top_issues) < 5:
            top_issues.append(
                (
                    sev,
                    str(getattr(iss, "title", "issue"))[:80],
                    str(getattr(iss, "first_seen_url", ""))[:80],
                )
            )

    status = getattr(job, "status", "completed")
    status = getattr(status, "value", status)
    verdict = "✅ Clean crawl"
    if sev_counts.get("blocking"):
        verdict = f"🔴 {sev_counts['blocking']} blocking issue(s)"
    elif sev_counts.get("functional"):
        verdict = f"🟠 {sev_counts['functional']} functional issue(s)"

    fields = [
        {"type": "mrkdwn", "text": f"*Target:*\n<{getattr(job, 'url', '')}|{str(getattr(job, 'url', ''))[:60]}>"},
        {"type": "mrkdwn", "text": f"*Pages:*\n{getattr(job, 'pages_visited', 0)} visited / {getattr(job, 'pages_discovered', 0)} discovered"},
        {"type": "mrkdwn", "text": f"*Issues:*\n🔴 {sev_counts.get('blocking', 0)} · 🟠 {sev_counts.get('functional', 0)} · 🟡 {sev_counts.get('visual', 0)} · info: {sev_counts.get('advisory', 0)}"},
    ]
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"CBTP crawl {status}: {verdict}"},
        },
        {"type": "section", "fields": fields},
    ]
    if top_issues:
        lines = [f"• *[{sev.upper()}]* {title} — `{url}`" for sev, title, url in top_issues]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
    blocks.append({"type": "actions", "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": "Open dashboard"},
         "url": dashboard_url, "style": "primary"},
    ]})
    return {"blocks": blocks, "text": f"CBTP crawl {status}: {verdict}"}


async def notify_slack(url: str, job: Any, issues: list[Any], dashboard_url: str) -> bool:
    """POST a Block Kit summary to a Slack webhook. Non-fatal."""
    payload = build_slack_payload(job, issues, dashboard_url)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            return 200 <= resp.status_code < 300
    except Exception as e:
        logger.warning("Slack notify failed: %s", e)
        return False


# ── Jira ────────────────────────────────────────────────────────────────────


def _jira_cfg(monitor: Any, settings: Any) -> tuple[str, str, str] | None:
    """Resolve (base_url, project_key, api_token) — per-monitor overrides win."""
    base = getattr(monitor, "jira_url", None) or getattr(settings, "jira_url", "")
    project = getattr(monitor, "jira_project", None) or getattr(settings, "jira_project", "")
    token = ""
    enc = getattr(monitor, "jira_token_encrypted", None)
    if enc:
        try:
            from src.core.secrets_crypto import decrypt_secret

            token = decrypt_secret(enc)
        except Exception:
            token = ""
    if not token:
        token = getattr(settings, "jira_api_token", "")
    if not (base and project and token):
        return None
    return base.rstrip("/"), project, token


def _jira_labels(issue: Any) -> list[str]:
    cat = str(getattr(issue, "category", "unknown") or "unknown")
    cat = getattr(issue.category, "value", cat) if hasattr(issue.category, "value") else cat
    return ["cbtp", f"category-{cat}"]


async def create_jira_ticket(
    client: httpx.AsyncClient,
    base_url: str,
    project: str,
    token: str,
    issue: Any,
    job: Any,
) -> str | None:
    """Create one Jira ticket for an issue. Returns key or None. Non-fatal per issue."""
    sev = getattr(issue, "severity", "advisory")
    sev = getattr(sev, "value", sev)
    sev_map = {
        "blocking": ("Highest", "Bug"),
        "functional": ("High", "Bug"),
        "visual": ("Medium", "Task"),
        "advisory": ("Low", "Task"),
    }
    priority, itype = sev_map.get(sev, ("Low", "Task"))
    fingerprint = getattr(issue, "fingerprint", "")
    title = str(getattr(issue, "title", "Crawl issue"))[:200]

    description = (
        f"Discovered by an automated cross-browser crawl.\n\n"
        f"*Target:* {getattr(job, 'url', '')}\n"
        f"*First seen on:* {getattr(issue, 'first_seen_url', '')}\n"
        f"*Occurrences:* {getattr(issue, 'occurrence_count', 1)}\n"
        f"*Severity:* {sev}\n"
        f"*Error:*\n{{code}}{str(getattr(issue, 'error_message', ''))[:2000]}{{code}}\n\n"
        f"*CBTP fingerprint:* {fingerprint}\n"
        f"*Job:* {getattr(job, 'id', '')}\n"
    )

    payload = {
        "fields": {
            "project": {"key": project},
            "summary": f"[CBTP] {title}",
            "description": description,
            "issuetype": {"name": itype},
            "labels": _jira_labels(issue),
            "priority": {"name": priority} if priority else {},
        }
    }
    resp = await client.post(
        f"{base_url}/rest/api/3/issue",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json=payload,
    )
    if resp.status_code in (200, 201):
        return resp.json().get("key")
    logger.warning("Jira create failed (%s): %s", resp.status_code, resp.text[:200])
    return None


async def jira_dedupe_key_exists(
    client: httpx.AsyncClient, base_url: str, token: str, fingerprint: str
) -> bool:
    """True when a ticket already references this CBTP fingerprint."""
    jql = f'text ~ "CBTP fingerprint: {fingerprint}"'
    try:
        resp = await client.get(
            f"{base_url}/rest/api/3/search",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"jql": jql, "maxResults": 1},
        )
        return resp.status_code == 200 and resp.json().get("total", 0) > 0
    except Exception:
        return False  # if search fails, prefer creating over silent no-op


async def notify_jira(monitor: Any, job: Any, issues: list[Any], settings: Any = None) -> list[str]:
    """Create Jira tickets for BLOCKING + FUNCTIONAL issues (deduped).

    Returns the list of created ticket keys (may be empty).
    `settings` may be injected (tests); defaults to global settings.
    """
    if settings is None:
        from src.config import settings as settings

    cfg = _jira_cfg(monitor, settings)
    if cfg is None:
        return []
    base_url, project, token = cfg

    def _sev_value(i: Any) -> str:
        sev = getattr(i, "severity", "advisory")
        return str(getattr(sev, "value", sev) or "advisory")

    ticketable = [i for i in issues if _sev_value(i) in ("blocking", "functional")]
    created: list[str] = []
    if not ticketable:
        return created
    # Injectable client factory (tests pass a mock transport; prod uses real httpx)
    factory = getattr(settings, "jira_client_factory", None) or (lambda: httpx.AsyncClient(timeout=15.0))
    async with factory() as client:
        for issue in ticketable:
            fp = str(getattr(issue, "fingerprint", ""))
            if fp and await jira_dedupe_key_exists(client, base_url, token, fp):
                continue
            key = await create_jira_ticket(client, base_url, project, token, issue, job)
            if key:
                created.append(key)
    return created
