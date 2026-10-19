"""Tests for the visual-diff gate in scripts/ci_gate.py.

Uses a stub httpx client — no live API. Verifies project resolution, the
compare call, and the regression gate math.
"""

from __future__ import annotations

import pytest
from scripts.ci_gate import _resolve_project_id, print_visual_summary, run_visual_gate


class _Resp:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = ""

    def json(self) -> dict:
        return self._body


class StubClient:
    def __init__(self, projects: list[dict], compare: dict, compare_status: int = 201) -> None:
        self.projects = projects
        self.compare = compare
        self.compare_status = compare_status
        self.last_post = None

    def get(self, path: str) -> _Resp:
        if path == "/api/projects":
            return _Resp(200, {"projects": self.projects})
        return _Resp(404, {})

    def post(self, path: str, json=None) -> _Resp:
        self.last_post = (path, json)
        return _Resp(self.compare_status, self.compare)


def test_resolve_project_id() -> None:
    c = StubClient(
        [{"id": "p1", "slug": "storefront"}, {"id": "p2", "slug": "blog"}],
        {},
    )
    assert _resolve_project_id(c, "storefront") == "p1"
    assert _resolve_project_id(c, "blog") == "p2"
    assert _resolve_project_id(c, "missing") is None


def test_run_visual_gate_success() -> None:
    c = StubClient(
        [{"id": "p1", "slug": "storefront"}],
        {
            "compare_id": "c1",
            "branch": "main",
            "regressions": 2,
            "matches": 5,
            "new_baselines": 0,
            "total": 7,
            "results": [],
        },
    )
    vg = run_visual_gate(c, "storefront", "job-1", "main", 0)
    assert vg["regressions"] == 2
    assert vg["project_id"] == "p1"
    path, payload = c.last_post
    assert path == "/api/projects/p1/compare"
    assert payload == {"job_id": "job-1", "branch": "main"}


def test_run_visual_gate_project_missing_exits() -> None:
    c = StubClient([], {})
    with pytest.raises(SystemExit) as exc:
        run_visual_gate(c, "nope", "job-1", None, 0)
    assert exc.value.code == 2


def test_print_visual_summary_noop(capsys) -> None:
    print_visual_summary(
        {"project_id": "p1", "branch": "main", "regressions": 1, "matches": 3, "new_baselines": 0, "results": []}
    )
    out = capsys.readouterr().out
    assert "Regressions: 1" in out
    assert "Matches:     3" in out
