"""Lighthouse-style performance scoring from data collected in the browser.

Two layers:

1. compute_lighthouse_style_scores(nav_timing, cdp_metrics, resource_data)
   Pure function. Derives Core-Web-Vitals-shaped metrics from Navigation
   Timing + CDP + Paint Timing data already collected by the crawler and
   scores them with Google's published Lighthouse v10 weights and the
   log-normal curve used by PSI. No external tools — works for every
   crawled page in every browser.

2. run_lighthouse_audit(url) — optional REAL Lighthouse (Node CLI) for the
   seed URL only, enabled via LIGHTHOUSE_ENABLED + node in the worker image.
   Its JSON is merged over the computed scores where available.

Scoring follows Lighthouse v10 weights:
    FCP 10% · SI 10% · LCP 25% · TBT 30% · CLS 25%
Log-normal scoring curve: score = 1 - PHI((ln(v) - ln(median)) / (sqrt(2)*slope))
with metric medians/slopes from Lighthouse's constants (HTTP Archive-informed).
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# Lighthouse v10 category weights (sum = 1.0)
WEIGHTS = {
    "first_contentful_paint": 0.10,
    "speed_index": 0.10,
    "largest_contentful_paint": 0.25,
    "total_blocking_time": 0.30,
    "cumulative_layout_shift": 0.25,
}

# Log-normal curve params: (median, slope) — from Lighthouse v10 constants
_METRIC_CURVES = {
    "first_contentful_paint": (1800, 0.5),
    "speed_index": (3387, 0.5),
    "largest_contentful_paint": (2500, 0.5),
    "total_blocking_time": (300, 0.5),
    "cumulative_layout_shift": (0.1, 0.5),
}


def _log_normal_score(value_ms: float, median: float, slope: float) -> float:
    """Lighthouse log-normal utility curve → 0..1."""
    if value_ms is None or value_ms < 0:
        return 0.0
    if value_ms == 0:
        return 1.0
    z = (math.log(value_ms) - math.log(median)) / (math.sqrt(2) * slope)
    return max(0.0, min(1.0, 1.0 - 0.5 * (1 + math.erf(z / math.sqrt(2)))))


# CLS is unitless-small: scale to ms-space for the same curve
_CLS_SCALE = 1000.0


def score_metric(metric: str, value: float | None) -> float:
    """Score one metric 0..1; None/unknown → excluded later (not zero).

    CLS is unitless; both value and its median are scaled into ms-space
    (_CLS_SCALE) so the same log-normal curve applies.
    """
    if value is None:
        return 0.0
    median, slope = _METRIC_CURVES[metric]
    if metric == "cumulative_layout_shift":
        value = float(value) * _CLS_SCALE
        median = median * _CLS_SCALE
    return _log_normal_score(float(value), median, slope)


def _grade(score: float) -> str:
    """0..1 → Lighthouse grade label."""
    if score >= 0.9:
        return "good"
    if score >= 0.5:
        return "needs-improvement"
    return "poor"


def _extract_metrics(
    nav_timing: dict[str, Any] | None,
    cdp_metrics: dict[str, float] | None,
    paint_timing: dict[str, Any] | None,
    resource_data: dict[str, Any] | None,
) -> dict[str, float | None]:
    """Derive the 5 Lighthouse metrics from collected browser data.

    - FCP: Paint Timing first-contentful-paint (fallback: domContentLoaded)
    - LCP: Paint Timing largest-contentful-paint (fallback: loadEventEnd-start)
    - TBT: CDP long-task proxy — sum of long tasks beyond 50ms when
      resource_data carries `long_tasks`; else ScriptDuration heuristic
    - SI: approximated from FCP + DOM completeness (documented approximation)
    - CLS: layout-shift score when resource_data carries `layout_shift`;
      else None (honest absence — weighted out)
    """
    nav = nav_timing or {}
    cdp = cdp_metrics or {}
    paint = paint_timing or {}
    res = resource_data or {}

    start = float(nav.get("startTime", 0) or 0)
    fcp = paint.get("first_contentful_paint")
    if fcp is None:
        # domContentLoadedEventStart is a sane upper-bound proxy for FCP
        dcl = nav.get("domContentLoadedEventStart")
        fcp = float(dcl) - start if dcl else None

    lcp = paint.get("largest_contentful_paint")
    if lcp is None:
        load_end = nav.get("loadEventEnd")
        lcp = float(load_end) - start if load_end and load_end > 0 else None

    # TBT proxy
    long_tasks = res.get("long_tasks") or []
    if long_tasks:
        tbt = sum(max(0.0, float(t) - 50.0) for t in long_tasks)
    else:
        # Chromium-only ScriptDuration heuristic when long tasks unavailable
        script_ms = cdp.get("ScriptDuration", 0) or 0
        tbt = float(script_ms) * 200 if script_ms > 0 else None  # heuristic cap

    # Speed Index approximation: FCP + 60% of (FCP→load) tail
    si = None
    if fcp is not None:
        load_end = nav.get("loadEventEnd")
        tail = (float(load_end) - start - float(fcp)) if load_end and load_end > 0 else 0.0
        si = max(float(fcp), float(fcp) + 0.6 * max(0.0, tail))

    cls = res.get("layout_shift")
    cls = float(cls) if cls is not None else None

    return {
        "first_contentful_paint": fcp,
        "speed_index": si,
        "largest_contentful_paint": lcp,
        "total_blocking_time": tbt,
        "cumulative_layout_shift": cls,
    }


def compute_lighthouse_style_scores(
    nav_timing: dict[str, Any] | None,
    cdp_metrics: dict[str, float] | None = None,
    paint_timing: dict[str, Any] | None = None,
    resource_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute a Lighthouse-style performance section for one page.

    Returns {score (0-100), grade, metrics: {name: {value_ms, score}},
    source}. Unknown metrics are excluded from the weighted mean and
    reported with value=None — never guessed.
    """
    metrics_ms = _extract_metrics(nav_timing, cdp_metrics, paint_timing, resource_data)

    weighted_sum = 0.0
    weight_total = 0.0
    out_metrics: dict[str, dict[str, Any]] = {}
    for name, weight in WEIGHTS.items():
        raw = metrics_ms.get(name)
        if raw is None:
            out_metrics[name] = {"value": None, "score": None, "weight": weight}
            continue
        s = score_metric(name, raw)
        # CLS display stays unitless
        # CLS stays unitless for display (scaling happens only inside score_metric)
        display = round(float(raw), 3) if name == "cumulative_layout_shift" else round(float(raw))
        out_metrics[name] = {
            "value": display,
            "score": round(s, 4),
            "weight": weight,
            "grade": _grade(s),
        }
        weighted_sum += weight * s
        weight_total += weight

    overall = weighted_sum / weight_total if weight_total > 0 else 0.0
    return {
        "score": round(overall * 100),
        "grade": _grade(overall),
        "metrics": out_metrics,
        "source": "computed",  # vs "lighthouse" when real CLI data merged
    }


# ── Paint/shift/long-task collection (injected into the live page) ─────────

PAINT_AND_SHIFT_JS = """
() => new Promise((resolve) => {
  const out = {first_contentful_paint: null, largest_contentful_paint: null, layout_shift: null};
  try {
    const paints = performance.getEntriesByType('paint');
    const fcp = paints.find(p => p.name === 'first-contentful-paint');
    out.first_contentful_paint = fcp ? Math.round(fcp.startTime) : null;

    new PerformanceObserver((list) => {
      const entries = list.getEntries();
      if (entries.length) {
        const e = entries[entries.length - 1];
        out.largest_contentful_paint = Math.round(e.startTime);
      }
    }).observe({type: 'largest-contentful-paint', buffered: true});

    // CLS: accumulate all layout-shift entries without recentInput
    let cls = 0;
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (!e.hadRecentInput) cls += e.value;
      }
      out.layout_shift = Math.round(cls * 1000) / 1000;
    }).observe({type: 'layout-shift', buffered: true});

    // Long tasks for TBT proxy
    const longTasks = [];
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) longTasks.push(Math.round(e.duration));
    }).observe({type: 'longtask', buffered: true});
    out._long_tasks = longTasks;
  } catch (e) { /* older browsers */ }
  // give buffered observers a tick, then resolve
  setTimeout(() => { out.long_tasks = out._long_tasks || []; delete out._long_tasks; resolve(out); }, 250);
})
"""


async def collect_paint_and_shift(page: Any) -> dict[str, Any]:
    """Collect FCP/LCP/CLS/long-tasks from a live Playwright page. Non-fatal."""
    try:
        data = await page.evaluate(PAINT_AND_SHIFT_JS)
        return data or {}
    except Exception as e:
        logger.debug("paint/shift collection failed: %s", e)
        return {}


# ── Optional REAL Lighthouse (Node CLI) for the seed page ───────────────────


def lighthouse_available() -> bool:
    return shutil.which("lighthouse") is not None


def run_lighthouse_audit(
    url: str,
    *,
    chrome_flags: str | None = None,
    timeout_s: int = 120,
    categories: str = "performance",
) -> dict[str, Any] | None:
    """Run real Lighthouse CLI on one URL. Returns normalized section or None.

    Requires `lighthouse` on PATH (npm i -g lighthouse) + LIGHTHOUSE_ENABLED.
    Synchronous — call from a worker context, not the API event loop.
    """
    import os

    if os.environ.get("LIGHTHOUSE_ENABLED", "").lower() not in ("1", "true"):
        return None
    if not lighthouse_available():
        logger.info("LIGHTHOUSE_ENABLED but lighthouse CLI not found — skipping")
        return None
    try:
        out = subprocess.run(
            [
                "lighthouse", url,
                "--output", "json",
                "--quiet",
                "--chrome-flags", chrome_flags or "--headless=new --no-sandbox",
                "--only-categories", categories,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=True,
        )
        data = json.loads(out.stdout)
        return _normalize_lighthouse_json(data)
    except Exception as e:
        logger.warning("Lighthouse run failed for %s: %s", url, e)
        return None


def _normalize_lighthouse_json(data: dict[str, Any]) -> dict[str, Any]:
    """Real Lighthouse JSON → our section shape (source='lighthouse')."""
    audits = data.get("audits", {})
    by_metric = {
        "first-contentful-paint": "first_contentful_paint",
        "speed-index": "speed_index",
        "largest-contentful-paint": "largest_contentful_paint",
        "total-blocking-time": "total_blocking_time",
        "cumulative-layout-shift": "cumulative_layout_shift",
    }
    metrics: dict[str, dict[str, Any]] = {}
    for lh_key, our_key in by_metric.items():
        a = audits.get(lh_key, {})
        metrics[our_key] = {
            "value": a.get("numericValue"),
            "score": a.get("score"),
            "grade": "good" if (a.get("score") or 0) >= 0.9 else (
                "needs-improvement" if (a.get("score") or 0) >= 0.5 else "poor"
            ),
        }
    perf = data.get("categories", {}).get("performance", {})
    return {
        "score": round((perf.get("score") or 0) * 100),
        "grade": _grade(perf.get("score") or 0),
        "metrics": metrics,
        "source": "lighthouse",
        "full_report_url": None,
    }


def merge_lighthouse_sections(
    computed: dict[str, Any] | None,
    real: dict[str, Any] | None,
) -> dict[str, Any]:
    """Real Lighthouse wins when present; computed fills the rest."""
    if real is None:
        return computed or {}
    if computed is None:
        return real
    merged = dict(real)
    # keep computed metrics that real run lacks
    for name, m in computed.get("metrics", {}).items():
        if name not in merged.get("metrics", {}) or merged["metrics"][name].get("value") is None:
            merged.setdefault("metrics", {})[name] = m
    merged["computed_score"] = computed.get("score")
    return merged
