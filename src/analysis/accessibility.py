"""Accessibility auditing via axe-core injection.

Injects the axe-core library into the browser page and runs a full
accessibility audit. Results are parsed into structured violation records
that can be attached to endpoint reports.

Uses the axe-core CDN bundle — no pip dependency needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.browser.factory import InstrumentedPage

logger = logging.getLogger(__name__)

# axe-core CDN URL (pinned to stable release)
AXE_CORE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"


@dataclass
class A11yViolation:
    """A single accessibility violation found by axe-core."""

    rule_id: str  # e.g. "color-contrast", "image-alt"
    impact: str  # "critical", "serious", "moderate", "minor"
    description: str  # Human-readable description of the rule
    help_url: str  # Link to axe docs for the rule
    wcag_tags: list[str]  # e.g. ["wcag2a", "wcag2aa", "wcag21a"]
    affected_nodes: int  # Number of DOM nodes affected
    html_snippets: list[str]  # First 3 failing HTML snippets


@dataclass
class A11yReport:
    """Accessibility audit result for a single page."""

    url: str
    total_violations: int = 0
    total_passes: int = 0
    total_incomplete: int = 0
    violations: list[A11yViolation] = field(default_factory=list)
    score: float = 100.0  # 0-100, lower = worse

    @property
    def has_critical(self) -> bool:
        return any(v.impact == "critical" for v in self.violations)

    @property
    def has_serious(self) -> bool:
        return any(v.impact == "serious" for v in self.violations)


class AccessibilityAuditor:
    """Runs axe-core accessibility audits on Playwright pages.

    Usage:
        auditor = AccessibilityAuditor()
        report = await auditor.audit(page, url="https://example.com")
        print(f"Found {report.total_violations} violations")
    """

    def __init__(self) -> None:
        self._axe_injected: set[str] = set()  # Track pages where axe is loaded

    async def _inject_axe(self, page: InstrumentedPage) -> bool:
        """Inject axe-core library into the page if not already present."""
        try:
            # Check if axe is already available
            has_axe = await page.page.evaluate("typeof window.axe !== 'undefined'")
            if has_axe:
                return True

            # Inject axe-core from CDN
            await page.page.add_script_tag(url=AXE_CORE_CDN)
            # Wait for it to load
            await page.page.wait_for_function(
                "typeof window.axe !== 'undefined'",
                timeout=10000,
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to inject axe-core: {e}")
            return False

    async def audit(self, page: InstrumentedPage, url: str = "") -> A11yReport:
        """Run a full axe-core accessibility audit on the current page.

        Returns an A11yReport with all violations, passes, and incomplete checks.
        """
        report = A11yReport(url=url or page.page.url)

        # Inject axe-core
        if not await self._inject_axe(page):
            logger.warning(f"Skipping a11y audit for {url} — axe-core injection failed")
            return report

        try:
            # Run axe.run() with default rules
            raw_results = await page.page.evaluate("""
                async () => {
                    try {
                        const results = await axe.run(document, {
                            resultTypes: ['violations', 'passes', 'incomplete'],
                        });
                        return {
                            violations: results.violations,
                            passes: results.passes.length,
                            incomplete: results.incomplete.length,
                        };
                    } catch (e) {
                        return { error: e.message };
                    }
                }
            """)

            if not raw_results or "error" in raw_results:
                logger.warning(f"axe.run() failed for {url}: {raw_results.get('error', 'unknown')}")
                return report

            # Parse violations
            violations_data = raw_results.get("violations", [])
            report.total_violations = len(violations_data)
            report.total_passes = raw_results.get("passes", 0)
            report.total_incomplete = raw_results.get("incomplete", 0)

            for v in violations_data:
                nodes = v.get("nodes", [])
                html_snippets = [
                    n.get("html", "")[:200]
                    for n in nodes[:3]  # Cap at 3 snippets
                ]

                violation = A11yViolation(
                    rule_id=v.get("id", "unknown"),
                    impact=v.get("impact", "minor"),
                    description=v.get("description", ""),
                    help_url=v.get("helpUrl", ""),
                    wcag_tags=[t for t in v.get("tags", []) if t.startswith("wcag")],
                    affected_nodes=len(nodes),
                    html_snippets=html_snippets,
                )
                report.violations.append(violation)

            # Calculate score: 100 minus weighted penalty per violation
            penalty = 0
            for v in report.violations:
                weight = {"critical": 25, "serious": 15, "moderate": 5, "minor": 2}.get(v.impact, 1)
                penalty += weight
            report.score = max(0.0, 100.0 - penalty)

            logger.info(
                f"A11y audit for {url}: "
                f"score={report.score:.0f}, "
                f"violations={report.total_violations}, "
                f"passes={report.total_passes}"
            )

        except Exception as e:
            logger.error(f"Accessibility audit failed for {url}: {e}")

        return report
