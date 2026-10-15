"""Tests for Lighthouse-style performance scoring.

Covers: log-normal curve behavior, metric extraction fallbacks, weighted
aggregation with missing metrics (never guessed), CLS scaling, real
Lighthouse JSON normalization, merge precedence, report card rendering.
Run: pytest tests/test_lighthouse.py -q
"""

from __future__ import annotations

from src.analysis.lighthouse_scorer import (
    WEIGHTS,
    _normalize_lighthouse_json,
    compute_lighthouse_style_scores,
    merge_lighthouse_sections,
    score_metric,
)

# ── Pure scoring math ────────────────────────────────────────────────────────


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_score_at_median_is_half():
    # Lighthouse convention: value == median → score 0.5
    for metric in WEIGHTS:
        median = {"first_contentful_paint": 1800, "speed_index": 3387,
                  "largest_contentful_paint": 2500, "total_blocking_time": 300,
                  "cumulative_layout_shift": 0.1}[metric]
        s = score_metric(metric, median)
        assert abs(s - 0.5) < 0.02, f"{metric}: {s}"


def test_score_extremes():
    assert score_metric("first_contentful_paint", 0) == 1.0
    assert score_metric("first_contentful_paint", 10) > 0.99
    assert score_metric("first_contentful_paint", 60000) < 0.05
    assert score_metric("first_contentful_paint", None) == 0.0


def test_cls_scaled():
    # CLS 0.1 == median → 0.5, unitless handled via scale
    assert abs(score_metric("cumulative_layout_shift", 0.1) - 0.5) < 0.02
    assert score_metric("cumulative_layout_shift", 0.0) > 0.99


# ── Metric extraction ────────────────────────────────────────────────────────


NAV_FAST = {
    "startTime": 0,
    "responseStart": 120,
    "domContentLoadedEventStart": 900,
    "loadEventEnd": 1800,
    "domComplete": 1700,
}
NAV_SLOW = {
    "startTime": 0,
    "responseStart": 900,
    "domContentLoadedEventStart": 5000,
    "loadEventEnd": 9000,
}


def test_full_data_scores_high():
    section = compute_lighthouse_style_scores(
        NAV_FAST,
        cdp_metrics={"ScriptDuration": 0.05},
        paint_timing={"first_contentful_paint": 700, "largest_contentful_paint": 1400},
        resource_data={"long_tasks": [80, 90], "layout_shift": 0.02},
    )
    assert section["source"] == "computed"
    assert section["score"] >= 80, section
    m = section["metrics"]
    assert m["first_contentful_paint"]["value"] == 700
    assert m["largest_contentful_paint"]["value"] == 1400
    assert m["total_blocking_time"]["value"] == 70  # (80-50)+(90-50)
    assert m["cumulative_layout_shift"]["value"] == 0.02


def test_slow_site_scores_low():
    section = compute_lighthouse_style_scores(NAV_SLOW, cdp_metrics={}, paint_timing={}, resource_data={})
    assert section["score"] < 50


def test_missing_metrics_excluded_not_zero():
    # CLS unknown → excluded from weighted mean (not counted as 0)
    with_cls = compute_lighthouse_style_scores(
        NAV_FAST, {}, {"first_contentful_paint": 700, "largest_contentful_paint": 1400},
        {"long_tasks": [80]},
    )
    without_cls = compute_lighthouse_style_scores(
        NAV_FAST, {}, {"first_contentful_paint": 700, "largest_contentful_paint": 1400},
        {"long_tasks": [80], "layout_shift": 0.0},
    )
    assert with_cls["metrics"]["cumulative_layout_shift"]["value"] is None
    assert with_cls["metrics"]["cumulative_layout_shift"]["score"] is None
    # perfect CLS (0.0 → score 1.0) should RAISE the weighted average
    assert without_cls["score"] >= with_cls["score"]


def test_fcp_fallback_to_domcontentloaded():
    section = compute_lighthouse_style_scores(
        NAV_FAST, {}, paint_timing={}, resource_data={}
    )
    # FCP falls back to domContentLoadedEventStart - startTime
    assert section["metrics"]["first_contentful_paint"]["value"] == 900


def test_tbt_script_duration_heuristic_when_no_longtasks():
    section = compute_lighthouse_style_scores(
        NAV_FAST, cdp_metrics={"ScriptDuration": 0.2}, paint_timing={}, resource_data={}
    )
    tbt = section["metrics"]["total_blocking_time"]["value"]
    assert tbt is not None and tbt > 0


def test_empty_inputs():
    section = compute_lighthouse_style_scores({}, {}, {}, {})
    assert section["score"] == 0  # no known metrics → zero, source honest


# ── Real Lighthouse JSON normalization ──────────────────────────────────────


def test_normalize_lighthouse_json():
    fake_lh = {
        "categories": {"performance": {"score": 0.87}},
        "audits": {
            "first-contentful-paint": {"numericValue": 1200, "score": 0.9},
            "speed-index": {"numericValue": 3000, "score": 0.7},
            "largest-contentful-paint": {"numericValue": 2100, "score": 0.8},
            "total-blocking-time": {"numericValue": 250, "score": 0.85},
            "cumulative-layout-shift": {"numericValue": 0.05, "score": 0.95},
        },
    }
    section = _normalize_lighthouse_json(fake_lh)
    assert section["score"] == 87
    assert section["source"] == "lighthouse"
    assert section["metrics"]["first_contentful_paint"]["value"] == 1200
    assert section["metrics"]["cumulative_layout_shift"]["grade"] == "good"


def test_merge_real_wins_computed_fills():
    computed = compute_lighthouse_style_scores(
        NAV_FAST, {}, {"first_contentful_paint": 700, "largest_contentful_paint": 1400},
        {"long_tasks": [80], "layout_shift": 0.02},
    )
    real = {
        "score": 92, "source": "lighthouse", "grade": "good",
        "metrics": {"first_contentful_paint": {"value": 800, "score": 0.9, "grade": "good"}},
    }
    merged = merge_lighthouse_sections(computed, real)
    assert merged["score"] == 92
    assert merged["source"] == "lighthouse"
    # real FCP wins
    assert merged["metrics"]["first_contentful_paint"]["value"] == 800
    # computed metrics fill the gaps
    assert merged["metrics"]["largest_contentful_paint"]["value"] == 1400
    assert merged["computed_score"] == computed["score"]


def test_merge_with_no_real():
    computed = {"score": 71}
    assert merge_lighthouse_sections(computed, None) == computed
    assert merge_lighthouse_sections(None, {"score": 90}) == {"score": 90}


# ── Report rendering ────────────────────────────────────────────────────────


def test_report_card_html(monkeypatch):
    from src.analysis.report_generator import _render_lighthouse_summary

    class Ep:
        performance_data = {
            "chromium": {"lighthouse": {"score": 74, "grade": "needs-improvement",
                         "metrics": {"largest_contentful_paint": {"value": 2800, "grade": "poor"}}}}
        }

    class Reg:
        def get_all(self):
            return [Ep()]

    class Job:
        config = {}

    html = _render_lighthouse_summary(Reg(), Job())
    assert "Performance (Lighthouse-style)" in html
    assert ">74<" in html
    assert "All pages (avg)" in html
    assert "Worst page" in html
    assert "LIGHTHOUSE_ENABLED" in html  # guidance note for full audits


def test_report_card_seed_cli(monkeypatch):
    from src.analysis.report_generator import _render_lighthouse_summary

    class Job:
        config = {"lighthouse_seed": {"score": 95, "source": "lighthouse", "metrics": {}}}

    class Reg:
        def get_all(self):
            return []

    html = _render_lighthouse_summary(Reg(), Job())
    assert "Seed page" in html
    assert "source: Lighthouse CLI" in html


def test_report_card_empty():
    from src.analysis.report_generator import _render_lighthouse_summary

    class Reg:
        def get_all(self):
            return []

    class Job:
        config = {}

    assert _render_lighthouse_summary(Reg(), Job()) == ""
