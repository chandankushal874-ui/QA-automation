"""Direct integration test for the Daytona sandbox pytest verification path.

This test spins up a remote Daytona sandbox using the configured
``DAYTONA_API_KEY``, uploads a tiny project, applies a patch, and runs
pytest inside the sandbox. It is skipped automatically when no Daytona key
is set so it does not break CI/offline runs.

Daytona is the PREFERRED sandbox (sub-90ms cold starts, stateful) — this
test guards the preferred path. The E2B equivalent lives in
``test_e2b_sandbox_integration.py``.

Run explicitly:
    pytest tests/test_daytona_sandbox_integration.py -v -s
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from src.analysis.fix_engine import SourceContext, VerificationEngine
from src.config import settings


def _daytona_org_has_region() -> bool:
    """Probe whether the configured Daytona org has a default region set.

    The Daytona API returns ``[]`` from ``/regions`` when the org has no
    default region configured in the dashboard, and ``sandbox.create()``
    fails with a validation error in that case. We probe once at import
    time so the live integration tests skip cleanly instead of failing.
    """
    import json
    import urllib.request

    if not settings.daytona_api_key:
        return False
    api_url = (settings.daytona_api_url or "https://app.daytona.io/api").rstrip("/")
    if not api_url.endswith("/api"):
        api_url = api_url + "/api"
    req = urllib.request.Request(
        f"{api_url}/regions",
        headers={"Authorization": f"Bearer {settings.daytona_api_key}"},
    )
    try:
        body = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
        regions = json.loads(body)
        return bool(regions)
    except Exception:
        return False


_HAS_DAYTONA_REGION = _daytona_org_has_region()


# Live tests can run if EITHER the org has a default region (create() works)
# OR a long-lived sandbox ID is configured (reuse via daytona_sandbox_id).
_CAN_RUN_DAYTONA = _HAS_DAYTONA_REGION or bool(settings.daytona_sandbox_id)


pytestmark = [
    pytest.mark.skipif(
        not settings.daytona_api_key,
        reason="DAYTONA_API_KEY not configured — skipping live sandbox integration test.",
    ),
    pytest.mark.skipif(
        not _CAN_RUN_DAYTONA,
        reason="Daytona org has no default region and no daytona_sandbox_id set — "
        "open the Daytona Dashboard to set a default region, or set "
        "DAYTONA_SANDBOX_ID to reuse an existing started sandbox.",
    ),
]


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


def test_daytona_sandbox_runs_pytest_and_catches_failure():
    """Patch that does NOT fix the bug → pytest must report 1 failed."""
    tmp_root = tempfile.mkdtemp(prefix="cbtp_daytona_")
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

        # Patch that does nothing useful — divide() still raises on b=0.
        bad_patch = """diff --git a/src/math_utils.py b/src/math_utils.py
--- a/src/math_utils.py
+++ b/src/math_utils.py
@@ -5,3 +5,3 @@
-    return a / b
+    return a / b  # still broken
"""

        passed, log = VerificationEngine.verify_patch(bad_patch, ctx, repo_dir=tmp_root, test_path=test_path)

        print(f"\n[DAYTONA FAIL-CASE] passed={passed} log={log!r}")
        assert passed is False, "Expected pytest to fail because the bug is not fixed"
        assert "Daytona sandbox" in log
        assert "failed" in log
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_daytona_sandbox_runs_pytest_and_confirms_fix():
    """Patch that DOES fix the bug → pytest must report all passed."""
    tmp_root = tempfile.mkdtemp(prefix="cbtp_daytona_")
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

        print(f"\n[DAYTONA PASS-CASE] passed={passed} log={log!r}")
        assert passed is True, "Expected pytest to pass because the bug is fixed"
        assert "Daytona sandbox" in log
        assert "passed" in log
        assert "failed" not in log or "0 failed" in log
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_daytona_sandbox_syntax_only_check():
    """When no repo_dir/test_path is given, Daytona falls back to py_compile."""
    # A syntactically valid Python snippet
    valid_code = "x = 1\nprint(x)\n"
    ctx = SourceContext(
        file_path="snippet.py",
        line_number=1,
        context_lines=valid_code.splitlines(),
        start_line=1,
        end_line=2,
        code_snippet=valid_code,
    )

    # Empty patch (no changes) — verify_patch will reconstruct the snippet
    empty_patch = ""
    passed, log = VerificationEngine.verify_patch(empty_patch, ctx)

    print(f"\n[DAYTONA SYNTAX-CHECK] passed={passed} log={log!r}")
    # Either Daytona ran it (preferred) or fell back to local compile.
    assert passed is True
    assert "syntax" in log.lower() or "passed" in log.lower()


def test_daytona_sandbox_detects_syntax_error():
    """A patch that introduces a SyntaxError must be caught by Daytona."""
    broken_code = "def broken(\n"  # missing closing paren
    ctx = SourceContext(
        file_path="broken.py",
        line_number=1,
        context_lines=broken_code.splitlines(),
        start_line=1,
        end_line=1,
        code_snippet=broken_code,
    )

    # Patch that replaces the line with broken syntax
    bad_patch = """diff --git a/broken.py b/broken.py
--- a/broken.py
+++ b/broken.py
@@ -1,1 +1,1 @@
-def broken(
+def broken(
"""
    passed, log = VerificationEngine.verify_patch(bad_patch, ctx)

    print(f"\n[DAYTONA SYNTAX-ERROR] passed={passed} log={log!r}")
    assert passed is False
    assert "SyntaxError" in log or "syntax" in log.lower() or "compile" in log.lower()
