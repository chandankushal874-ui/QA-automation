"""Tests for the journey runner (step executor logic).

Uses a fake Playwright Page (async) to exercise step sequencing, assertion
semantics, and stop_on_failure without launching a browser.

Run: pytest tests/test_journey_runner.py -q
"""

from __future__ import annotations

from typing import Any

from src.analysis.journey_runner import execute_journey


class FakeLocator:
    def __init__(self, *, count: int = 1, visible: bool = True, text: str = "") -> None:
        self._count = count
        self._visible = visible
        self._text = text

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible

    async def inner_text(self) -> str:
        return self._text


class FakePage:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []
        self.locators: dict[str, FakeLocator] = {}

    def locator(self, selector: str):
        return self.locators.get(selector, FakeLocator())

    async def goto(self, url: str, **kwargs) -> None:
        self.log.append(("goto", url))

    async def fill(self, selector: str, value: str) -> None:
        self.log.append(("fill", selector, value))

    async def click(self, selector: str) -> None:
        self.log.append(("click", selector))

    async def wait_for_selector(self, selector: str, **kwargs) -> None:
        self.log.append(("wait_for_selector", selector))

    async def wait_for_timeout(self, ms: int) -> None:
        self.log.append(("wait", ms))

    async def wait_for_load_state(self, state: str) -> None:
        self.log.append(("load", state))


def test_all_steps_pass() -> None:
    page = FakePage()
    steps = [
        {"action": "goto", "url": "http://x"},
        {"action": "fill", "selector": "#e", "value": "v"},
        {"action": "click", "selector": "#b"},
    ]
    import asyncio

    outcome = asyncio.run(execute_journey(page, steps, base_url="http://x", browser="chromium"))
    assert outcome.passed is True
    assert [s.status for s in outcome.step_results] == ["passed", "passed", "passed"]


def test_assert_visible_failure() -> None:
    page = FakePage()
    page.locators["#missing"] = FakeLocator(count=0)
    steps = [{"action": "assert", "selector": "#missing", "expect": "visible"}]
    import asyncio

    outcome = asyncio.run(execute_journey(page, steps, base_url="http://x", browser="chromium"))
    assert outcome.passed is False
    assert outcome.step_results[0].status == "error"
    assert "visible" in outcome.step_results[0].error


def test_assert_text_matches() -> None:
    page = FakePage()
    page.locators["#t"] = FakeLocator(text="Hello World")
    steps = [{"action": "assert", "selector": "#t", "expect": "text", "text": "Hello"}]
    import asyncio

    outcome = asyncio.run(execute_journey(page, steps, base_url="http://x", browser="chromium"))
    assert outcome.passed is True


def test_assert_text_mismatch() -> None:
    page = FakePage()
    page.locators["#t"] = FakeLocator(text="Nope")
    steps = [{"action": "assert", "selector": "#t", "expect": "text", "text": "Hello"}]
    import asyncio

    outcome = asyncio.run(execute_journey(page, steps, base_url="http://x", browser="chromium"))
    assert outcome.passed is False
    assert "contain" in outcome.step_results[0].error


def test_stop_on_failure_skips_remaining() -> None:
    page = FakePage()
    page.locators["#missing"] = FakeLocator(count=0)
    steps = [
        {"action": "assert", "selector": "#missing", "expect": "visible"},
        {"action": "goto", "url": "http://never"},
    ]
    import asyncio

    outcome = asyncio.run(
        execute_journey(page, steps, base_url="http://x", browser="chromium", stop_on_failure=True)
    )
    assert len(outcome.step_results) == 2
    assert outcome.step_results[0].status == "error"
    assert outcome.step_results[1].status == "skipped"
    assert outcome.passed is False


def test_non_fatal_default_continues() -> None:
    page = FakePage()
    page.locators["#missing"] = FakeLocator(count=0)
    steps = [
        {"action": "assert", "selector": "#missing", "expect": "visible"},
        {"action": "goto", "url": "http://next"},
    ]
    import asyncio

    outcome = asyncio.run(execute_journey(page, steps, base_url="http://x", browser="chromium"))
    assert len(outcome.step_results) == 2
    assert outcome.step_results[0].status == "error"
    assert outcome.step_results[1].status == "passed"
    assert outcome.passed is False  # any error → not all passed


def test_unknown_action_skipped() -> None:
    page = FakePage()
    steps = [{"action": "explode"}]
    import asyncio

    outcome = asyncio.run(execute_journey(page, steps, base_url="http://x", browser="chromium"))
    assert outcome.step_results[0].status == "skipped"
    assert "unknown action" in outcome.step_results[0].error
