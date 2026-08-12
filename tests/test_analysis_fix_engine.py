"""Unit tests for the source-aware analysis, OpenRouter fix engine, and PR preparer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analysis.fix_engine import (
    OpenRouterFixer,
    PRPreparer,
    SourceContext,
    StaticAnalyzer,
    VerificationEngine,
)
from src.models.database import Issue, IssueCategory, IssueSeverity


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository folder with a mock python file."""
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()

    file_path = repo_dir / "math_utils.py"
    code = """# Math utility functions
def add(a, b):
    return a + b

def divide(a, b):
    # Bug: Division by zero risk
    return a / b

def multiply(a, b):
    return a * b
"""
    file_path.write_text(code, encoding="utf-8")
    return str(repo_dir)


def test_static_analyzer_extracts_context(temp_repo):
    analyzer = StaticAnalyzer(repo_dir=temp_repo)

    # Simulate a stack trace referencing math_utils.py on line 7
    stack_trace = """
    Traceback (most recent call last):
      File "main.py", line 2, in <module>
        divide(10, 0)
      File "math_utils.py", line 7, in divide
        return a / b
    ZeroDivisionError: division by zero
    """

    context = analyzer.extract_source_context(stack_trace, "division by zero")

    assert context is not None
    assert context.line_number == 7
    assert "divide" in context.code_snippet
    assert "division by zero risk" in context.code_snippet.lower()
    assert context.start_line == 2
    assert context.end_line == 10


def test_static_analyzer_returns_none_on_missing_file(temp_repo):
    analyzer = StaticAnalyzer(repo_dir=temp_repo)

    # Stack trace referencing a non-existent file
    stack_trace = 'File "non_existent.py", line 15, in run'
    context = analyzer.extract_source_context(stack_trace, "error")

    assert context is None


@pytest.mark.asyncio
async def test_openrouter_fixer_mock_fallback(monkeypatch):
    monkeypatch.setattr("src.config.settings.openrouter_api_key", "")
    # If no api_key is configured, it fallback generates a mock fix suggestion
    fixer = OpenRouterFixer(api_key="")

    issue = Issue(
        title="JS_ERROR: TypeError",
        error_message="Cannot read properties of null (reading 'property')",
        stack_trace="at index.html:15:10",
        category=IssueCategory.JS_ERROR,
        severity=IssueSeverity.BLOCKING,
    )

    result = await fixer.generate_fix(issue)

    # Placeholder fixes must not masquerade as trustworthy: zero confidence
    assert result.confidence == 0.0
    assert "Simulated fix" in result.explanation
    assert "PLACEHOLDER" in result.explanation
    assert "diff --git" in result.patch


@pytest.mark.asyncio
async def test_openrouter_fixer_api_call(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(
        return_value={
            "choices": [
                {"message": {"content": '{"explanation": "Add zero check", "patch": "diff...", "confidence": 0.95}'}}
            ]
        }
    )
    mock_post = AsyncMock(return_value=mock_resp)
    monkeypatch.setattr("httpx.AsyncClient.post", mock_post)

    fixer = OpenRouterFixer(api_key="sk-or-test-key")
    issue = Issue(
        title="ZeroDivisionError",
        error_message="division by zero",
        stack_trace="at math_utils.py:7",
        category=IssueCategory.JS_ERROR,
        severity=IssueSeverity.FUNCTIONAL,
    )

    result = await fixer.generate_fix(issue)

    assert result.explanation == "Add zero check"
    assert result.patch == "diff..."
    assert result.confidence == 0.95


def test_verification_engine_python_valid():
    patch = """diff --git a/math_utils.py b/math_utils.py
--- a/math_utils.py
+++ b/math_utils.py
@@ -7,3 +7,5 @@
-    return a / b
+    if b == 0:
+        return 0
+    return a / b"""

    ctx = SourceContext(
        file_path="math_utils.py",
        line_number=7,
        context_lines=["def divide(a, b):", "    return a / b"],
        start_line=6,
        end_line=8,
        code_snippet="def divide(a, b):\n    return a / b",
    )

    passed, log = VerificationEngine.verify_patch(patch, ctx)
    assert passed is True
    assert "passed" in log


def test_verification_engine_python_invalid():
    # Introduce syntax error (missing colon)
    patch = """diff --git a/math_utils.py b/math_utils.py
--- a/math_utils.py
+++ b/math_utils.py
@@ -7,3 +7,5 @@
-    return a / b
+    if b == 0
+        return 0
+    return a / b"""

    ctx = SourceContext(
        file_path="math_utils.py",
        line_number=7,
        context_lines=["def divide(a, b):", "    return a / b"],
        start_line=6,
        end_line=8,
        code_snippet="def divide(a, b):\n    return a / b",
    )

    passed, log = VerificationEngine.verify_patch(patch, ctx)
    assert passed is False
    assert "SyntaxError" in log


def test_pr_preparer():
    issue = Issue(title="ZeroDivisionError", category=IssueCategory.JS_ERROR, severity=IssueSeverity.FUNCTIONAL)

    from src.analysis.fix_engine import FixResult

    fix = FixResult(
        explanation="Bug fixed",
        patch="diff...",
        confidence=0.85,
        verified=True,
        verification_log="Passed tests",
        tests_passed=3,
        tests_failed=0,
        tests_total=3,
    )

    pr = PRPreparer.format_pull_request(issue, fix)

    assert pr["branch"].startswith("fix/js_error-")
    assert "ZeroDivisionError" in pr["title"]
    assert "Bug fixed" in pr["body"]
    assert "Verified: True" in pr["body"]
    assert "git checkout -b" in pr["git_commands"]
    assert "git apply" in pr["git_commands"]
