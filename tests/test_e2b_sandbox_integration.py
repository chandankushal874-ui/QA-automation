"""Direct integration test for the E2B sandbox pytest verification path.

This test actually spins up a remote E2B sandbox using the configured
``E2B_API_KEY``, uploads a tiny project, applies a patch, and runs pytest
inside the sandbox. It is skipped automatically when no E2B key is set so
it does not break CI/offline runs.

Run explicitly:
    pytest tests/test_e2b_sandbox_integration.py -v -s
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from src.analysis.fix_engine import SourceContext, VerificationEngine
from src.config import settings

pytestmark = pytest.mark.skipif(
    not settings.e2b_api_key,
    reason="E2B_API_KEY not configured — skipping live sandbox integration test.",
)


def _build_mini_project(root: str) -> tuple[str, str]:
    """Create a tiny project with a buggy module + matching pytest file.

    Returns ``(source_file_path, test_file_path)``.
    """
    src_dir = os.path.join(root, "src")
    tests_dir = os.path.join(root, "tests")
    os.makedirs(src_dir, exist_ok=True)
    os.makedirs(tests_dir, exist_ok=True)

    # Buggy source: divide() crashes on b=0
    source_code = '''"""Tiny module with a divide-by-zero bug."""


def divide(a: int, b: int) -> float:
    return a / b
'''
    source_path = os.path.join(src_dir, "math_utils.py")
    with open(source_path, "w", encoding="utf-8") as f:
        f.write(source_code)

    # Pytest file that exercises divide()
    test_code = """from src.math_utils import divide


def test_divide_normal():
    assert divide(10, 2) == 5.0


def test_divide_by_zero_returns_zero():
    # After the fix, divide(10, 0) should return 0 instead of raising.
    assert divide(10, 0) == 0
"""
    test_path = os.path.join(tests_dir, "test_math_utils.py")
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(test_code)

    # Minimal pyproject so `pip install -e .` works in the sandbox
    pyproject = """[project]
name = "mini-project"
version = "0.1.0"
requires-python = ">=3.10"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["src"]
"""
    with open(os.path.join(root, "pyproject.toml"), "w", encoding="utf-8") as f:
        f.write(pyproject)

    return source_path, test_path


def test_e2b_sandbox_runs_pytest_and_catches_failure():
    """Patch that does NOT fix the bug → pytest must report 1 failed."""
    tmp_root = tempfile.mkdtemp(prefix="cbtp_e2b_")
    try:
        source_path, test_path = _build_mini_project(tmp_root)

        # Read the original snippet (matches what StaticAnalyzer would extract)
        with open(source_path, encoding="utf-8") as f:
            original = f.read()

        ctx = SourceContext(
            file_path=source_path,
            line_number=5,
            context_lines=original.splitlines(),
            start_line=1,
            end_line=len(original.splitlines()),
            code_snippet=original,
        )

        # Patch that does nothing useful — divide() still raises on b=0.
        bad_patch = """diff --git a/src/math_utils.py b/src/math_utils.py
--- a/src/math_utils.py
+++ b/src/math_utils.py
@@ -5,3 +5,3 @@
-    return a / b
+    return a / b  # still broken
"""

        passed, log = VerificationEngine.verify_patch(bad_patch, ctx, repo_dir=tmp_root, test_path=test_path)

        print(f"\n[E2B FAIL-CASE] passed={passed} log={log!r}")
        assert passed is False, "Expected pytest to fail because the bug is not fixed"
        # Verification chain prefers Daytona → falls back to E2B; accept either.
        assert "Daytona sandbox" in log or "E2B sandbox" in log
        assert "failed" in log
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_e2b_sandbox_runs_pytest_and_confirms_fix():
    """Patch that DOES fix the bug → pytest must report all passed."""
    tmp_root = tempfile.mkdtemp(prefix="cbtp_e2b_")
    try:
        source_path, test_path = _build_mini_project(tmp_root)

        with open(source_path, encoding="utf-8") as f:
            original = f.read()

        ctx = SourceContext(
            file_path=source_path,
            line_number=5,
            context_lines=original.splitlines(),
            start_line=1,
            end_line=len(original.splitlines()),
            code_snippet=original,
        )

        # Patch that actually fixes divide-by-zero.
        good_patch = """diff --git a/src/math_utils.py b/src/math_utils.py
--- a/src/math_utils.py
+++ b/src/math_utils.py
@@ -5,3 +5,5 @@
-    return a / b
+    if b == 0:
+        return 0
+    return a / b
"""

        passed, log = VerificationEngine.verify_patch(good_patch, ctx, repo_dir=tmp_root, test_path=test_path)

        print(f"\n[E2B PASS-CASE] passed={passed} log={log!r}")
        assert passed is True, "Expected pytest to pass because the bug is fixed"
        # Verification chain prefers Daytona → falls back to E2B; accept either.
        assert "Daytona sandbox" in log or "E2B sandbox" in log
        assert "passed" in log
        assert "failed" not in log or "0 failed" in log
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
