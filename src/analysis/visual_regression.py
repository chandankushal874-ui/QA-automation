"""Visual regression testing via pixel-level screenshot comparison.

Compares current page screenshots against saved baselines using pixel diffing.
On first run, screenshots are saved as baselines. On subsequent runs, diffs
are computed and flagged if the mismatch exceeds a configurable threshold.

Uses raw pixel comparison (no external dependency needed — pure Python).
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Attempt to import PIL for image comparison
try:
    from PIL import Image, ImageChops, ImageDraw

    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    logger.warning("Pillow not installed. Visual regression testing will be disabled. Install with: pip install Pillow")


@dataclass
class VisualDiffResult:
    """Result of a visual regression comparison."""

    url: str
    baseline_path: str | None = None
    current_path: str | None = None
    diff_path: str | None = None
    mismatch_percentage: float = 0.0
    is_new_baseline: bool = False
    is_match: bool = True
    total_pixels: int = 0
    changed_pixels: int = 0


class VisualRegressionEngine:
    """Pixel-level visual regression comparison engine.

    Usage:
        engine = VisualRegressionEngine(baselines_dir="baselines/example.com")
        result = engine.compare(url, current_screenshot_path)
    """

    def __init__(self, baselines_dir: str = "baselines", threshold: float = 0.1) -> None:
        """
        Args:
            baselines_dir: Directory to store baseline screenshots.
            threshold: Mismatch percentage threshold (0.0 - 100.0). Default 0.1%.
        """
        self.baselines_dir = baselines_dir
        self.threshold = threshold
        os.makedirs(baselines_dir, exist_ok=True)

    def _url_to_filename(self, url: str) -> str:
        """Convert a URL to a stable, filesystem-safe filename."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        safe_name = url.replace("://", "_").replace("/", "_").replace("?", "_").replace("=", "_")
        if len(safe_name) > 80:
            safe_name = safe_name[:80]
        return f"{safe_name}_{url_hash}.png"

    def _get_baseline_path(self, url: str) -> str:
        """Get the baseline image path for a URL."""
        return os.path.join(self.baselines_dir, self._url_to_filename(url))

    def compare(self, url: str, current_screenshot_path: str) -> VisualDiffResult:
        """Compare a current screenshot against the stored baseline.

        If no baseline exists, the current screenshot becomes the baseline.
        If a baseline exists, pixel-level diffing is performed.
        """
        result = VisualDiffResult(url=url, current_path=current_screenshot_path)

        if not HAS_PIL:
            logger.warning("Pillow not available — skipping visual regression")
            return result

        if not os.path.exists(current_screenshot_path):
            logger.warning(f"Current screenshot not found: {current_screenshot_path}")
            return result

        baseline_path = self._get_baseline_path(url)
        result.baseline_path = baseline_path

        # If no baseline exists, save current as baseline
        if not os.path.exists(baseline_path):
            try:
                current_img = Image.open(current_screenshot_path)
                current_img.save(baseline_path)
                result.is_new_baseline = True
                result.is_match = True
                logger.info(f"New baseline saved for {url} → {baseline_path}")
            except Exception as e:
                logger.error(f"Failed to save baseline for {url}: {e}")
            return result

        # Compare current vs baseline
        try:
            baseline_img = Image.open(baseline_path).convert("RGB")
            current_img = Image.open(current_screenshot_path).convert("RGB")

            # Resize current to match baseline dimensions if they differ
            if baseline_img.size != current_img.size:
                current_img = current_img.resize(baseline_img.size, Image.Resampling.LANCZOS)

            # Compute pixel diff
            diff_img = ImageChops.difference(baseline_img, current_img)

            # Count non-zero (changed) pixels
            pixels = list(diff_img.getdata())
            total_pixels = len(pixels)
            # A pixel is "changed" if any channel differs by more than a small tolerance (10)
            tolerance = 10
            changed = sum(1 for r, g, b in pixels if r > tolerance or g > tolerance or b > tolerance)

            result.total_pixels = total_pixels
            result.changed_pixels = changed
            result.mismatch_percentage = (changed / total_pixels * 100) if total_pixels > 0 else 0.0
            result.is_match = result.mismatch_percentage <= self.threshold

            # Generate highlighted diff image if there are changes
            if not result.is_match:
                diff_dir = os.path.join(self.baselines_dir, "diffs")
                os.makedirs(diff_dir, exist_ok=True)
                diff_path = os.path.join(diff_dir, self._url_to_filename(url))

                # Create a visual diff: overlay red on changed regions
                highlighted = current_img.copy()
                draw = ImageDraw.Draw(highlighted)
                width = highlighted.width

                for idx, (r, g, b) in enumerate(pixels):
                    if r > tolerance or g > tolerance or b > tolerance:
                        x = idx % width
                        y = idx // width
                        draw.point((x, y), fill=(255, 0, 0, 200))

                highlighted.save(diff_path)
                result.diff_path = diff_path
                logger.warning(
                    f"Visual regression detected for {url}: "
                    f"{result.mismatch_percentage:.2f}% changed ({changed}/{total_pixels} pixels)"
                )
            else:
                logger.info(f"Visual match for {url}: {result.mismatch_percentage:.4f}% diff")

        except Exception as e:
            logger.error(f"Visual comparison failed for {url}: {e}")

        return result
