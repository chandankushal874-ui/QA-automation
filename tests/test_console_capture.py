"""Tests for the console error capture and classification module."""

from __future__ import annotations

from src.core.console_capture import (
    ConsoleCapture,
    ConsoleCategory,
    ConsoleReport,
    ConsoleSeverity,
    classify_message,
)

# ─── classify_message tests ───


class TestClassifyMessage:
    """Test the message classification engine."""

    def test_cors_error_detected(self) -> None:
        severity, category = classify_message(
            "Access to XMLHttpRequest at 'https://api.example.com' from origin "
            "'https://example.com' has been blocked by CORS policy: "
            "No 'Access-Control-Allow-Origin' header is present",
            "error",
        )
        assert category == ConsoleCategory.CORS_ERROR
        assert severity == ConsoleSeverity.WARNING

    def test_csp_violation_detected(self) -> None:
        severity, category = classify_message(
            "Refused to load the script 'https://evil.com/track.js' "
            "because it violates the following Content Security Policy directive",
            "error",
        )
        assert category == ConsoleCategory.CSP_VIOLATION
        assert severity == ConsoleSeverity.WARNING

    def test_mixed_content_detected(self) -> None:
        severity, category = classify_message(
            "Mixed Content: The page at 'https://example.com' was loaded over HTTPS, "
            "but requested an insecure stylesheet 'http://example.com/style.css'",
            "warning",
        )
        assert category == ConsoleCategory.MIXED_CONTENT
        assert severity == ConsoleSeverity.WARNING

    def test_network_failure_detected(self) -> None:
        severity, category = classify_message(
            "GET https://api.example.com/data net::ERR_CONNECTION_REFUSED",
            "error",
        )
        assert category == ConsoleCategory.NETWORK_FAILURE
        assert severity == ConsoleSeverity.ERROR

    def test_failed_to_fetch_detected(self) -> None:
        severity, category = classify_message("TypeError: Failed to fetch", "error")
        assert category == ConsoleCategory.NETWORK_FAILURE
        assert severity == ConsoleSeverity.ERROR

    def test_websocket_error_detected(self) -> None:
        severity, category = classify_message(
            "WebSocket connection to 'ws://localhost:8000/feed' failed: connection closed",
            "error",
        )
        assert category == ConsoleCategory.NETWORK_FAILURE
        assert severity == ConsoleSeverity.ERROR

    def test_resource_load_failure_detected(self) -> None:
        severity, category = classify_message(
            "Failed to load resource: the server responded with a status of 404 (Not Found)",
            "error",
        )
        assert category == ConsoleCategory.RESOURCE_LOAD_FAILURE
        assert severity == ConsoleSeverity.ERROR

    def test_react_error_detected(self) -> None:
        severity, category = classify_message(
            "Warning: React does not recognize the `testProp` prop on a DOM element",
            "warning",
        )
        assert category == ConsoleCategory.FRAMEWORK_ERROR
        assert severity == ConsoleSeverity.WARNING

    def test_react_error_boundary_detected(self) -> None:
        severity, category = classify_message(
            "Error boundary caught an error in component MyComponent",
            "error",
        )
        assert category == ConsoleCategory.FRAMEWORK_ERROR
        assert severity == ConsoleSeverity.ERROR

    def test_deprecation_warning_detected(self) -> None:
        severity, category = classify_message(
            "DeprecationWarning: Buffer() is deprecated. Use Buffer.alloc() instead.",
            "warning",
        )
        assert category == ConsoleCategory.DEPRECATION
        assert severity == ConsoleSeverity.WARNING

    def test_unhandled_rejection_is_critical(self) -> None:
        severity, category = classify_message(
            "Unhandled promise rejection: TypeError: Cannot read properties of null",
            "error",
        )
        assert category == ConsoleCategory.UNHANDLED_REJECTION
        assert severity == ConsoleSeverity.CRITICAL

    def test_uncaught_exception_is_critical(self) -> None:
        severity, category = classify_message(
            "Uncaught TypeError: Cannot read properties of undefined (reading 'map')",
            "error",
        )
        assert category == ConsoleCategory.JS_ERROR
        assert severity == ConsoleSeverity.CRITICAL

    def test_generic_error_fallback(self) -> None:
        severity, category = classify_message("Something went wrong", "error")
        assert category == ConsoleCategory.GENERAL_ERROR
        assert severity == ConsoleSeverity.ERROR

    def test_generic_warning_fallback(self) -> None:
        severity, category = classify_message("This is a warning", "warning")
        assert category == ConsoleCategory.GENERAL_WARNING
        assert severity == ConsoleSeverity.WARNING

    def test_generic_log_fallback(self) -> None:
        severity, category = classify_message("Hello debug world", "log")
        assert category == ConsoleCategory.GENERAL_LOG
        assert severity == ConsoleSeverity.INFO


# ─── ConsoleCapture tests ───


class TestConsoleCapture:
    """Test the ConsoleCapture utility."""

    def test_add_entry_classifies_correctly(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        entry = capture.add_entry(
            message="Uncaught TypeError: x is not a function",
            message_type="error",
        )
        assert entry.severity == ConsoleSeverity.CRITICAL
        assert entry.category == ConsoleCategory.JS_ERROR
        assert entry.browser == "chromium"
        assert entry.source_url == "https://example.com"

    def test_add_page_error_always_critical(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="firefox")
        entry = capture.add_page_error(
            error_message="ReferenceError: foo is not defined",
            stack_trace="at bar (app.js:42:5)",
        )
        assert entry.severity == ConsoleSeverity.CRITICAL
        assert entry.category == ConsoleCategory.JS_ERROR
        assert entry.stack_trace == "at bar (app.js:42:5)"

    def test_add_network_failure_500_is_critical(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="webkit")
        entry = capture.add_network_failure(
            url="https://api.example.com/data",
            method="POST",
            status_code=500,
            status_text="Internal Server Error",
        )
        assert entry.severity == ConsoleSeverity.CRITICAL
        assert entry.category == ConsoleCategory.NETWORK_FAILURE
        assert entry.extra["status_code"] == 500

    def test_add_network_failure_4xx_is_error(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        entry = capture.add_network_failure(
            url="https://api.example.com/missing",
            status_code=404,
            status_text="Not Found",
        )
        assert entry.severity == ConsoleSeverity.ERROR

    def test_add_resource_failure(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        entry = capture.add_resource_failure(
            resource_url="https://example.com/broken-image.png",
            resource_type="image",
        )
        assert entry.severity == ConsoleSeverity.ERROR
        assert entry.category == ConsoleCategory.RESOURCE_LOAD_FAILURE

    def test_finalize_produces_complete_report(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        capture.add_entry("Warning: something", "warning")
        capture.add_page_error("Crash!")
        capture.add_network_failure(url="/api/fail", status_code=503)

        report = capture.finalize()
        assert report.finished_at is not None
        assert len(report.entries) == 3
        assert report.critical_count == 2  # page error + 5xx network failure
        assert report.warning_count == 1
        assert report.has_blocking_errors is True


# ─── ConsoleReport tests ───


class TestConsoleReport:
    """Test the ConsoleReport data class."""

    def test_empty_report_has_no_errors(self) -> None:
        report = ConsoleReport(url="https://example.com", browser="chromium")
        assert report.has_errors is False
        assert report.has_blocking_errors is False
        assert report.critical_count == 0
        assert report.error_count == 0

    def test_summary_counts_correctly(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        capture.add_entry("info log", "log")
        capture.add_entry("a warning", "warning")
        capture.add_entry("an error", "error")
        capture.add_page_error("crash!")

        report = capture.finalize()
        summary = report.summary()
        assert summary["info"] == 1
        assert summary["warning"] == 1
        assert summary["error"] == 1
        assert summary["critical"] == 1
        assert summary["total"] == 4

    def test_get_actionable_entries(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        capture.add_entry("just info", "log")
        capture.add_entry("just a warning", "warning")
        capture.add_entry("real error", "error")
        capture.add_page_error("critical crash")

        report = capture.finalize()
        actionable = report.get_actionable_entries()
        assert len(actionable) == 2  # error + critical

    def test_get_entries_by_category(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        capture.add_network_failure(url="/api/a", status_code=500)
        capture.add_network_failure(url="/api/b", status_code=404)
        capture.add_entry("some error", "error")

        report = capture.finalize()
        network_entries = report.get_entries_by_category(ConsoleCategory.NETWORK_FAILURE)
        assert len(network_entries) == 2


# ─── ConsoleEntry tests ───


class TestConsoleEntry:
    """Test the ConsoleEntry data class."""

    def test_fingerprint_normalizes_numbers(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        entry1 = capture.add_network_failure(url="/api/users/123", status_code=500)
        entry2 = capture.add_network_failure(url="/api/users/456", status_code=500)
        assert entry1.fingerprint() == entry2.fingerprint()

    def test_fingerprint_normalizes_urls(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        entry1 = capture.add_entry("Failed to load https://cdn.example.com/bundle.js", "error")
        entry2 = capture.add_entry("Failed to load https://cdn.other.com/bundle.js", "error")
        assert entry1.fingerprint() == entry2.fingerprint()

    def test_is_actionable(self) -> None:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        info_entry = capture.add_entry("hello", "log")
        error_entry = capture.add_entry("bad thing", "error")
        critical_entry = capture.add_page_error("crash!")

        assert info_entry.is_actionable() is False
        assert error_entry.is_actionable() is True
        assert critical_entry.is_actionable() is True
