"""Baseline comparison engine — pixel diff with ignore regions and verdicts.

Production-grade visual regression on top of Pillow (already a dependency).

Design:
  - Pure pixel comparison (no numpy) for portability and zero new deps.
  - Ignore regions are resolved to rectangles *before* the pixel pass so
    dynamic content (ads, timestamps, carousels, cookies banners) is masked
    out and never counts toward mismatch.
  - Selector-based ignore regions are resolved by the caller (which has DOM
    access via Playwright) into rectangles; this engine also accepts them and
    simply skips any it cannot resolve (never raises).
  - Verdicts: ``new_baseline`` (no baseline), ``match`` (<= threshold),
    ``change`` (> threshold but under regression gate semantics are caller
    policy), ``regression`` (> project threshold), ``error``.

The pixel tolerance mirrors the existing visual_regression.py semantics so
results are consistent across the codebase.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageDraw

logger = logging.getLogger(__name__)

PIXEL_TOLERANCE = 10  # per-channel delta below which a pixel is "unchanged"


@dataclass
class Rect:
    x: int
    y: int
    width: int
    height: int

    def clamp_to(self, w: int, h: int) -> Rect:
        x0 = max(0, min(self.x, w))
        y0 = max(0, min(self.y, h))
        x1 = max(x0, min(self.x + self.width, w))
        y1 = max(y0, min(self.y + self.height, h))
        return Rect(x0, y0, x1 - x0, y1 - y0)


@dataclass
class DiffResult:
    """Outcome of comparing a current image against a baseline."""

    url: str
    verdict: str = "new_baseline"  # new_baseline | match | change | regression | error
    mismatch_percentage: float = 0.0
    changed_pixels: int = 0
    total_pixels: int = 0
    width: int = 0
    height: int = 0
    threshold: float = 0.1
    ignore_regions_applied: list[Rect] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "verdict": self.verdict,
            "mismatch_percentage": round(self.mismatch_percentage, 6),
            "changed_pixels": self.changed_pixels,
            "total_pixels": self.total_pixels,
            "width": self.width,
            "height": self.height,
            "threshold": self.threshold,
            "ignore_regions_applied": [
                {"x": r.x, "y": r.y, "width": r.width, "height": r.height}
                for r in self.ignore_regions_applied
            ],
            "error": self.error,
        }


def resolve_ignore_regions(
    regions: list[dict],
    *,
    image_width: int,
    image_height: int,
) -> list[Rect]:
    """Convert ignore-region dicts into clamped Rect objects.

    Accepts rectangle regions ``{x,y,width,height}``. Selector-only regions
    (no x/y/width/height) are skipped — the caller is responsible for
    resolving them to rectangles via DOM/Playwright before this call.
    """
    rects: list[Rect] = []
    for region in regions or []:
        if not isinstance(region, dict):
            continue
        try:
            x = int(region.get("x", 0))
            y = int(region.get("y", 0))
            w = int(region.get("width", 0))
            h = int(region.get("height", 0))
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        rects.append(Rect(x, y, w, h).clamp_to(image_width, image_height))
    return rects


def _build_ignore_mask(
    width: int,
    height: int,
    rects: list[Rect],
) -> Image.Image:
    """Return a 1-bit 'L' mask where 0 = ignored, 255 = compared."""
    mask = Image.new("L", (width, height), 255)
    if not rects:
        return mask
    draw = ImageDraw.Draw(mask)
    for r in rects:
        draw.rectangle([r.x, r.y, r.x + r.width, r.y + r.height], fill=0)
    return mask


def _pixel_changed(px: tuple[int, int, int]) -> bool:
    r, g, b = px
    return r > PIXEL_TOLERANCE or g > PIXEL_TOLERANCE or b > PIXEL_TOLERANCE


def compare_images(
    baseline_bytes: bytes,
    current_bytes: bytes,
    *,
    url: str = "",
    threshold: float = 0.1,
    ignore_regions: list[dict] | None = None,
) -> DiffResult:
    """Compare current against baseline, honoring ignore regions.

    Returns a DiffResult; the ``diff_image`` (red-overlay PNG) is produced
    separately via :func:`render_diff_image` so callers can persist it.
    """
    result = DiffResult(url=url, threshold=threshold)
    try:
        baseline = Image.open(io.BytesIO(baseline_bytes)).convert("RGB")
        current = Image.open(io.BytesIO(current_bytes)).convert("RGB")
    except Exception as e:
        result.verdict = "error"
        result.error = f"image decode failed: {e}"
        return result

    # Normalize dimensions: resize current to baseline size (same as legacy).
    if baseline.size != current.size:
        current = current.resize(baseline.size, Image.Resampling.LANCZOS)

    w, h = baseline.size
    result.width = w
    result.height = h

    rects = resolve_ignore_regions(ignore_regions or [], image_width=w, image_height=h)
    result.ignore_regions_applied = rects
    mask = _build_ignore_mask(w, h, rects)

    diff = ImageChops.difference(baseline, current)

    # Count changed pixels outside ignore regions.
    changed = 0
    total = 0
    diff_data = diff.get_flattened_data()
    mask_data = mask.get_flattened_data()
    for i, px in enumerate(diff_data):
        if mask_data[i] == 0:  # ignored
            continue
        total += 1
        if _pixel_changed(px):  # type: ignore[arg-type]
            changed += 1

    result.changed_pixels = changed
    result.total_pixels = total
    result.mismatch_percentage = (changed / total * 100.0) if total > 0 else 0.0

    if result.mismatch_percentage <= threshold:
        result.verdict = "match"
    elif result.mismatch_percentage > threshold:
        # Callers distinguish "change" vs "regression" via a separate gate
        # threshold; at this layer any threshold breach is a regression signal.
        result.verdict = "regression"

    logger.info(
        "diff %s -> %s (%.4f%% over %d px, %d ignored)",
        url, result.verdict, result.mismatch_percentage, total, len(rects),
    )
    return result


def render_diff_image(
    baseline_bytes: bytes,
    current_bytes: bytes,
    ignore_regions: list[dict] | None = None,
) -> bytes:
    """Render a red-overlay diff PNG highlighting changed (non-ignored) pixels."""
    baseline = Image.open(io.BytesIO(baseline_bytes)).convert("RGB")
    current = Image.open(io.BytesIO(current_bytes)).convert("RGB")
    if baseline.size != current.size:
        current = current.resize(baseline.size, Image.Resampling.LANCZOS)
    w, h = baseline.size
    rects = resolve_ignore_regions(ignore_regions or [], image_width=w, image_height=h)
    mask = _build_ignore_mask(w, h, rects)

    diff = ImageChops.difference(baseline, current)
    overlay = current.copy().convert("RGB")
    draw = ImageDraw.Draw(overlay)
    diff_data = diff.get_flattened_data()
    mask_data = mask.get_flattened_data()
    for i, px in enumerate(diff_data):
        if mask_data[i] == 0:
            continue
        if _pixel_changed(px):  # type: ignore[arg-type]
            x = i % w
            y = i // w
            draw.point((x, y), fill=(255, 0, 0))

    buf = io.BytesIO()
    overlay.save(buf, format="PNG")
    return buf.getvalue()


__all__ = [
    "DiffResult",
    "Rect",
    "compare_images",
    "render_diff_image",
    "resolve_ignore_regions",
]
