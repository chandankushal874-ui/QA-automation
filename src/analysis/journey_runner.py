"""Declarative multi-step journey executor.

Runs a list of typed steps against a target across the browser matrix,
capturing per-step status + errors and (optionally) screenshots. Reuses the
existing browser factory (console/network capture) and AuthBypassEngine for the
``login`` step so a journey can traverse authenticated flows.

Step actions (v1):
    goto        { url }
    fill        { selector, value }
    click       { selector }
    wait        { ms } | { selector } (wait for visible)
    assert      { selector, expect: visible|hidden|text, text? }
    login       { username, password, username_selector?, password_selector?,
                  submit_selector?, login_url? }
    screenshot  { label }

Every step result: { index, action, status: passed|failed|error|skipped,
                     error, duration_ms, screenshot_key? }.

A failed step is non-fatal by default (continue) unless the journey declares
``stop_on_failure``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"goto", "fill", "click", "wait", "assert", "login", "screenshot"}


@dataclass
class StepResult:
    index: int
    action: str
    status: str = "passed"  # passed | failed | error | skipped
    error: str = ""
    duration_ms: int = 0
    screenshot_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "action": self.action,
            "status": self.status,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "screenshot_key": self.screenshot_key,
        }


@dataclass
class JourneyOutcome:
    browser: str
    passed: bool = False
    step_results: list[StepResult] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "browser": self.browser,
            "passed": self.passed,
            "error": self.error,
            "steps": [s.to_dict() for s in self.step_results],
        }


async def _resolve_login_credentials(step: dict[str, Any], base_url: str) -> tuple[str, str]:
    """Pull username/password from the login step (may be inline or empty → error)."""
    username = step.get("username") or ""
    password = step.get("password") or ""
    if not username:
        raise ValueError("login step requires 'username'")
    return username, password


async def _run_login_step(page: Page, step: dict[str, Any], base_url: str) -> None:
    """Execute a login step using FormLoginStrategy semantics directly.

    We drive the form explicitly (rather than the full AuthBypassEngine) so the
    step result is deterministic and selectors are author-provided when given.
    """
    username = step.get("username") or ""
    password = step.get("password") or ""
    user_sel = step.get("username_selector") or "input[type=text], input[name=username], input[id=username]"
    pass_sel = step.get("password_selector") or "input[type=password]"
    submit_sel = step.get("submit_selector") or "button[type=submit], form button, input[type=submit]"
    login_url = step.get("login_url") or base_url

    if page.url != login_url:
        await page.goto(login_url, wait_until="domcontentloaded")

    await page.fill(user_sel, username)
    await page.fill(pass_sel, password)
    await page.click(submit_sel)
    # Allow the app to perform the round-trip before the next step asserts.
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(500)


async def execute_journey(
    page: Page,
    steps: list[dict[str, Any]],
    *,
    base_url: str,
    browser: str,
    stop_on_failure: bool = False,
    screenshot_dir: str | None = None,
) -> JourneyOutcome:
    """Run all steps on a single page and return the outcome.

    ``page`` must be an already-created InstrumentedPage.page (or any Playwright
    Page). Auth state persistence across steps is the caller's responsibility
    (same context); this function only sequences the steps.

    When ``screenshot_dir`` is provided, ``screenshot`` steps write a PNG there
    and store its path in ``screenshot_key``.
    """
    outcome = JourneyOutcome(browser=browser)
    step_results: list[StepResult] = []

    for i, step in enumerate(steps):
        action = str(step.get("action", "")).lower()
        sr = StepResult(index=i, action=action)
        start = time.monotonic()

        if action not in VALID_ACTIONS:
            sr.status = "skipped"
            sr.error = f"unknown action '{action}'"
            step_results.append(sr)
            continue

        try:
            if action == "goto":
                await page.goto(step.get("url") or base_url, wait_until="domcontentloaded")
            elif action == "fill":
                await page.fill(step["selector"], step.get("value", ""))
            elif action == "click":
                await page.click(step["selector"])
            elif action == "wait":
                if "selector" in step and step.get("selector"):
                    await page.wait_for_selector(step["selector"], state="visible")
                else:
                    await page.wait_for_timeout(int(step.get("ms", 1000)))
            elif action == "assert":
                await _run_assert(page, step)
            elif action == "login":
                await _run_login_step(page, step, base_url)
            elif action == "screenshot":
                label = step.get("label") or f"step-{i}"
                if screenshot_dir:
                    import os

                    os.makedirs(screenshot_dir, exist_ok=True)
                    safe = label.replace("/", "_").replace("\\", "_").replace(":", "_")
                    path = os.path.join(screenshot_dir, f"{safe}.png")
                    await page.screenshot(path=path, full_page=False)
                    sr.screenshot_key = path
                else:
                    sr.screenshot_key = label
            sr.status = "passed"
        except Exception as e:
            sr.status = "error"
            sr.error = f"{type(e).__name__}: {e}"
            logger.warning("journey step %d (%s) failed on %s: %s", i, action, browser, e)

        sr.duration_ms = int((time.monotonic() - start) * 1000)
        step_results.append(sr)

        if sr.status != "passed" and stop_on_failure:
            idx = len(step_results)
            for remaining in steps[i + 1 :]:
                step_results.append(
                    StepResult(index=idx, action=remaining.get("action", ""), status="skipped")
                )
                idx += 1
            break

    outcome.step_results = step_results
    outcome.passed = all(s.status == "passed" for s in step_results) and bool(step_results)
    return outcome


async def _run_assert(page: Page, step: dict[str, Any]) -> None:
    selector = step["selector"]
    expect = step.get("expect", "visible")
    loc = page.locator(selector).first

    if expect == "hidden":
        count = await loc.count()
        if count > 0 and await loc.is_visible():
            raise AssertionError(f"expected '{selector}' to be hidden but it is visible")
        return

    if expect == "text":
        text = step.get("text", "")
        count = await loc.count()
        if count == 0:
            raise AssertionError(f"expected '{selector}' to contain text '{text}' but not found")
        content = await loc.inner_text()
        if text not in content:
            raise AssertionError(f"expected '{selector}' to contain '{text}', got '{content[:120]}'")
        return

    # default: visible
    count = await loc.count()
    if count == 0 or not await loc.is_visible():
        raise AssertionError(f"expected '{selector}' to be visible")


__all__ = ["VALID_ACTIONS", "JourneyOutcome", "StepResult", "execute_journey"]
