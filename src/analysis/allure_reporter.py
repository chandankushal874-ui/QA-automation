"""Allure-compatible JSON report generator.

Converts our crawl results into Allure's result JSON format, enabling
the full Allure reporting dashboard with charts, timelines, and drill-downs.

Each crawled endpoint becomes an Allure "test case" with:
- Status (passed/failed/broken)
- Attachments (screenshots, trace files, diff images)
- Parameters (URL, method, browser, depth)
- Labels (suite, feature, severity)
- Steps (navigation, scrolling, a11y audit, visual comparison)

Output: allure-results/ directory with JSON + attachment files.
Use `allure serve allure-results` to view the dashboard.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def _ms_to_ns(ms: float) -> int:
    """Convert milliseconds to nanoseconds (Allure uses epoch ms)."""
    return int(ms)


class AllureReporter:
    """Generates Allure-compatible result files from crawl data.

    Usage:
        reporter = AllureReporter(output_dir="allure-results")
        reporter.generate(job, registry, issues, a11y_results, api_summary)
    """

    def __init__(self, output_dir: str = "allure-results") -> None:
        self.output_dir = output_dir
        self._attachments_dir = os.path.join(output_dir, "attachments")
        os.makedirs(self._attachments_dir, exist_ok=True)

    def _write_result(self, result: dict[str, Any]) -> None:
        """Write a single test result JSON file."""
        result_id = result.get("uuid", str(uuid.uuid4()))
        path = os.path.join(self.output_dir, f"{result_id}-result.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)

    def _copy_attachment(self, source_path: str, name: str) -> dict[str, str] | None:
        """Copy an attachment file and return Allure attachment metadata."""
        if not source_path or not os.path.exists(source_path):
            return None

        ext = os.path.splitext(source_path)[1] or ".png"
        attachment_name = f"{uuid.uuid4()}{ext}"
        dest = os.path.join(self._attachments_dir, attachment_name)

        try:
            shutil.copy2(source_path, dest)
            mime_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".zip": "application/zip",
                ".json": "application/json",
                ".html": "text/html",
            }
            return {
                "name": name,
                "source": attachment_name,
                "type": mime_map.get(ext.lower(), "application/octet-stream"),
            }
        except Exception as e:
            logger.warning(f"Failed to copy attachment {source_path}: {e}")
            return None

    def generate(
        self,
        job: Any,
        registry: Any,
        issues: list[Any],
        a11y_results: dict[str, Any] | None = None,
        api_summary: Any | None = None,
    ) -> str:
        """Generate Allure result files for all crawled endpoints.

        Returns the output directory path.
        """
        # Clean previous results
        if os.path.exists(self.output_dir):
            for f in os.listdir(self.output_dir):
                if f.endswith("-result.json"):
                    os.remove(os.path.join(self.output_dir, f))

        endpoints = registry.get_all()
        start_epoch_ms = int(time.time() * 1000)

        logger.info(f"Generating Allure results for {len(endpoints)} endpoints...")

        for idx, ep in enumerate(endpoints):
            test_uuid = str(uuid.uuid4())

            # Determine status
            status = "passed"
            status_details = {}
            if ep.last_status_code and ep.last_status_code >= 400:
                status = "failed"
                status_details = {
                    "message": f"HTTP {ep.last_status_code}",
                    "trace": f"Endpoint returned status code {ep.last_status_code}",
                }
            elif (
                hasattr(ep, "status") and (ep.status.name if hasattr(ep.status, "name") else str(ep.status)) == "failed"
            ):
                status = "broken"

            # Check for console errors
            ep_issues = [iss for iss in issues if iss.first_seen_url == ep.url]
            if ep_issues and status == "passed":
                has_critical = any(
                    (iss.severity.value if hasattr(iss.severity, "value") else str(iss.severity))
                    in ("blocking", "critical")
                    for iss in ep_issues
                )
                if has_critical:
                    status = "failed"
                    status_details = {
                        "message": f"{len(ep_issues)} console error(s) detected",
                        "trace": "\n".join(iss.error_message for iss in ep_issues[:5]),
                    }

            # Build steps
            steps = [
                {
                    "name": "Navigate to page",
                    "status": "passed" if ep.last_status_code and ep.last_status_code < 400 else "failed",
                    "stage": "finished",
                    "parameters": [{"name": "url", "value": ep.url}],
                },
                {
                    "name": "Scroll page (5 steps)",
                    "status": "passed",
                    "stage": "finished",
                },
                {
                    "name": "Console error check",
                    "status": "passed" if not ep_issues else "failed",
                    "stage": "finished",
                    "parameters": [{"name": "errors_found", "value": str(len(ep_issues))}],
                },
            ]

            # A11y step
            a11y_report = getattr(ep, "a11y_report", None)
            if a11y_report:
                a11y_status = (
                    "passed" if a11y_report.score >= 80 else ("failed" if a11y_report.score < 50 else "broken")
                )
                steps.append(
                    {
                        "name": "Accessibility audit (axe-core)",
                        "status": a11y_status,
                        "stage": "finished",
                        "parameters": [
                            {"name": "score", "value": f"{a11y_report.score:.0f}/100"},
                            {"name": "violations", "value": str(a11y_report.total_violations)},
                        ],
                    }
                )

            # Visual regression step
            vr = getattr(ep, "visual_diff", None)
            if vr:
                vr_status = "passed" if vr.is_match else "failed"
                steps.append(
                    {
                        "name": "Visual regression check",
                        "status": vr_status,
                        "stage": "finished",
                        "parameters": [
                            {"name": "mismatch", "value": f"{vr.mismatch_percentage:.2f}%"},
                            {"name": "is_new_baseline", "value": str(vr.is_new_baseline)},
                        ],
                    }
                )

            # Build attachments
            attachments = []
            snapshots = getattr(ep, "snapshots", {})
            if snapshots:
                for browser, path in snapshots.items():
                    att = self._copy_attachment(path, f"Screenshot ({browser})")
                    if att:
                        attachments.append(att)

            # Visual diff attachment
            if vr and vr.diff_path:
                att = self._copy_attachment(vr.diff_path, "Visual Diff (changed pixels)")
                if att:
                    attachments.append(att)

            # Build labels
            labels = [
                {"name": "suite", "value": f"Crawl: {job.url}"},
                {"name": "feature", "value": "Endpoint Testing"},
                {"name": "story", "value": ep.url},
                {"name": "severity", "value": "critical" if status == "failed" else "normal"},
                {"name": "host", "value": job.url},
            ]

            # Build parameters
            parameters = [
                {"name": "URL", "value": ep.url},
                {"name": "Method", "value": ep.method},
                {"name": "HTTP Status", "value": str(ep.last_status_code or "N/A")},
            ]

            # Performance data
            perf_data = getattr(ep, "performance_data", None)
            if perf_data:
                for _browser, pdata in perf_data.items():
                    nav = pdata.get("nav_timing", {})
                    parameters.append({"name": "TTFB (ms)", "value": f"{nav.get('responseStart', 0):.0f}"})
                    parameters.append({"name": "Load Time (ms)", "value": f"{nav.get('loadEventEnd', 0):.0f}"})
                    break

            # Assemble the Allure result
            result = {
                "uuid": test_uuid,
                "historyId": f"crawl_{ep.url}_{ep.method}",
                "name": f"[{ep.method}] {ep.url}",
                "fullName": f"crawl.{ep.method}.{ep.url}",
                "status": status,
                "statusDetails": status_details,
                "stage": "finished",
                "steps": steps,
                "attachments": attachments,
                "parameters": parameters,
                "labels": labels,
                "start": start_epoch_ms + (idx * 100),
                "stop": start_epoch_ms + (idx * 100) + 1000,
            }

            self._write_result(result)

        # Generate API test results as separate test cases
        if api_summary and api_summary.results:
            for api_result in api_summary.results:
                test_uuid = str(uuid.uuid4())
                api_status = "passed"
                if api_result.status_code and api_result.status_code >= 400:
                    api_status = "failed"
                elif api_result.error_message:
                    api_status = "broken"

                result = {
                    "uuid": test_uuid,
                    "historyId": f"api_{api_result.url}_{api_result.method}",
                    "name": f"[API] [{api_result.method}] {api_result.url}",
                    "fullName": f"api.{api_result.method}.{api_result.url}",
                    "status": api_status,
                    "statusDetails": {"message": api_result.error_message} if api_result.error_message else {},
                    "stage": "finished",
                    "steps": [
                        {
                            "name": f"Send {api_result.method} request",
                            "status": api_status,
                            "stage": "finished",
                        }
                    ],
                    "parameters": [
                        {"name": "URL", "value": api_result.url},
                        {"name": "Method", "value": api_result.method},
                        {"name": "Status Code", "value": str(api_result.status_code or "N/A")},
                        {"name": "Response Time", "value": f"{api_result.response_time_ms:.0f}ms"},
                        {"name": "Content-Type", "value": api_result.content_type},
                    ],
                    "labels": [
                        {"name": "suite", "value": "API Tests"},
                        {"name": "feature", "value": "API Validation"},
                        {"name": "severity", "value": "critical" if api_status == "failed" else "normal"},
                    ],
                    "start": start_epoch_ms,
                    "stop": start_epoch_ms + int(api_result.response_time_ms),
                }
                self._write_result(result)

        total = len(endpoints) + (len(api_summary.results) if api_summary else 0)
        logger.info(f"Allure results generated: {total} test cases → {self.output_dir}/")

        return self.output_dir
