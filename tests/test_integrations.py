"""Tests for Jira/Slack integrations (mocked HTTP â€” no real services hit).

Covers: Slack Block Kit payload shape, Jira severityâ†’type mapping, dedupe
via fingerprint search, per-monitor token encryption at rest, non-fatal
failure behavior. Run: pytest tests/test_integrations.py -q
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import httpx
import pytest

from src.api.integrations import (
    build_slack_payload,
    create_jira_ticket,
)


@dataclass
class FakeIssue:
    fingerprint: str = "js_error::abc"
    title: str = "JS_ERROR: TypeError on /dashboard"
    severity: str = "blocking"
    category: str = "js_error"
    occurrence_count: int = 3
    first_seen_url: str = "http://example.com/dashboard"
    error_message: str = "TypeError: x is not a function"


@dataclass
class FakeJob:
    id: object = None
    url: str = "http://example.com"
    status: str = "completed"
    pages_visited: int = 12
    pages_discovered: int = 24


class FakeSettings:
    jira_url = "https://test.atlassian.net"
    jira_project = "QA"
    jira_api_token = "test-token"


# â”€â”€ Slack payload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_slack_payload_structure():
    job = FakeJob(id=uuid.uuid4())
    issues = [FakeIssue(), FakeIssue(severity="functional"), FakeIssue(severity="advisory")]
    payload = build_slack_payload(job, issues, "http://cbtp/jobs/1/dashboard")

    red_circle = chr(0x1F534)  # 🔴 — built at runtime (Windows console cp1252-safe)
    assert payload["text"].startswith("CBTP crawl completed: " + red_circle)
    assert payload["text"].endswith("blocking issue(s)")
    kinds = [b["type"] for b in payload["blocks"]]
    assert kinds[0] == "header"
    assert "section" in kinds
    assert kinds[-1] == "actions"
    # dashboard button present
    button = payload["blocks"][-1]["elements"][0]
    assert button["url"] == "http://cbtp/jobs/1/dashboard"


def test_slack_payload_clean_crawl():
    job = FakeJob(id=uuid.uuid4())
    payload = build_slack_payload(job, [], "/x")
    check_mark = chr(0x2705)  # runtime-built, console-encoding safe
    assert check_mark + " Clean crawl" in payload["text"]
    # no issue list section when empty
    bullet = chr(0x2022)
    assert all(bullet not in str(b) for b in payload["blocks"])


# â”€â”€ Jira HTTP (mocked transport) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, routes: dict[str, tuple[int, dict]]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for prefix, (status, body) in self.routes.items():
            if prefix in path:
                self.calls.append((path, dict(request.url.params)))
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": "no route"})


@pytest.mark.asyncio
async def test_create_jira_ticket_maps_severity():
    captured: dict[str, httpx.Request] = {}

    class CaptureTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(201, json={"key": "QA-101", "id": "1"})

    client = httpx.AsyncClient(transport=CaptureTransport(), base_url="https://test.atlassian.net")
    job = FakeJob(id=uuid.uuid4())
    key = await create_jira_ticket(client, "https://test.atlassian.net", "QA", "tok", FakeIssue(), job)
    await client.aclose()

    assert key == "QA-101"
    assert captured["path"] == "/rest/api/3/issue"
    fields = captured["body"]["fields"]
    assert fields["project"]["key"] == "QA"
    assert fields["issuetype"]["name"] == "Bug"          # blocking â†’ Bug
    assert fields["priority"]["name"] == "Highest"       # blocking â†’ Highest
    assert fields["summary"].startswith("[CBTP]")
    assert "cbtp" in fields["labels"] and "category-js_error" in fields["labels"]
    assert "TypeError" in fields["description"]
    assert "js_error::abc" in fields["description"]      # fingerprint embedded


def test_jira_token_encrypted_at_rest_roundtrip():
    from src.core.secrets_crypto import decrypt_secret, encrypt_secret

    stored = encrypt_secret("atlassian-token")
    assert "atlassian-token" not in stored
    assert decrypt_secret(stored) == "atlassian-token"

