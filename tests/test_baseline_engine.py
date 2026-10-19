"""Tests for the baseline comparison engine (src/analysis/baseline.py).

Synthetic PIL images only — no network, no Playwright, deterministic.
"""

from __future__ import annotations

import io

from PIL import Image

from src.analysis.baseline import (
    compare_images,
    render_diff_image,
    resolve_ignore_regions,
)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _solid(w: int, h: int, color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def test_identical_images_match() -> None:
    img = _solid(100, 80, (200, 200, 200))
    r = compare_images(_png(img), _png(img), threshold=0.1)
    assert r.verdict == "match"
    assert r.mismatch_percentage == 0.0


def test_fully_different_regression() -> None:
    base = _solid(100, 80, (0, 0, 0))
    cur = _solid(100, 80, (255, 255, 255))
    r = compare_images(_png(base), _png(cur), threshold=0.1)
    assert r.verdict == "regression"
    assert r.mismatch_percentage > 99.0
    assert r.changed_pixels > 0


def test_small_change_below_threshold_is_match() -> None:
    base = _solid(1000, 1000, (128, 128, 128))
    cur = base.copy()
    # change a single 1x1 pixel
    cur.putpixel((0, 0), (200, 200, 200))
    r = compare_images(_png(base), _png(cur), threshold=0.1)
    # 1 of 1,000,000 px = 0.0001% < 0.1%
    assert r.verdict == "match"


def test_ignore_region_masks_dynamic_content() -> None:
    base = _solid(100, 100, (10, 10, 10))
    cur = base.copy()
    # A dynamic banner in the top 20px changes entirely.
    for x in range(100):
        for y in range(20):
            cur.putpixel((x, y), (250, 250, 250))
    # Without ignore: 20% mismatch → regression.
    r_no_ignore = compare_images(_png(base), _png(cur), threshold=0.1)
    assert r_no_ignore.verdict == "regression"
    # With ignore region covering that banner → match.
    r_ignore = compare_images(
        _png(base), _png(cur), threshold=0.1,
        ignore_regions=[{"x": 0, "y": 0, "width": 100, "height": 20}],
    )
    assert r_ignore.verdict == "match"
    assert r_ignore.ignore_regions_applied


def test_ignore_region_selector_only_skipped() -> None:
    rects = resolve_ignore_regions(
        [{"selector": ".ad-banner"}, {"x": 5, "y": 5, "width": 10, "height": 10}],
        image_width=50, image_height=50,
    )
    assert len(rects) == 1
    assert (rects[0].x, rects[0].y, rects[0].width, rects[0].height) == (5, 5, 10, 10)


def test_region_clamped_to_bounds() -> None:
    rects = resolve_ignore_regions(
        [{"x": 40, "y": 40, "width": 100, "height": 100}],
        image_width=50, image_height=50,
    )
    assert rects[0].x == 40 and rects[0].y == 40
    assert rects[0].width == 10 and rects[0].height == 10


def test_malformed_image_returns_error() -> None:
    r = compare_images(b"not-an-image", b"also-not", threshold=0.1)
    assert r.verdict == "error"
    assert r.error


def test_render_diff_image_returns_png() -> None:
    base = _solid(50, 50, (0, 0, 0))
    cur = _solid(50, 50, (255, 255, 255))
    out = render_diff_image(_png(base), _png(cur))
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    # Decodable and same dimensions.
    img = Image.open(io.BytesIO(out))
    assert img.size == (50, 50)


def test_size_mismatch_resizes() -> None:
    base = _solid(100, 100, (50, 50, 50))
    cur = _solid(200, 200, (50, 50, 50))
    r = compare_images(_png(base), _png(cur), threshold=0.1)
    assert r.verdict == "match"
    assert r.width == 100 and r.height == 100
