"""Product-agnostic foreground modelling and canonical mask tooling.

This module contains no product knowledge: no classes, no colors, no part
names. Everything is learned from the user's own captures during onboarding.

Concepts:
- BackgroundModel: median + per-pixel std of empty-scene frames.
- foreground_mask: pixels that deviate from the background beyond k*std.
- proposal_from_frames: union of foreground masks across good-unit frames
  (falls back to a border-color background estimate when no empty scene is
  available).
- mask_metrics / background_false_activation: setup-time quality gates.
- presence_regions: sub-regions that are foreground in nearly all good frames;
  used for component-agnostic "missing part" checks at runtime.

All masks are computed at the model input size (see service.inference.INPUT_SIZE).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np

from service.inference import INPUT_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resize(frame: np.ndarray) -> np.ndarray:
    return cv2.resize(frame, INPUT_SIZE, interpolation=cv2.INTER_AREA)


def _to_float(frame: np.ndarray) -> np.ndarray:
    return _resize(frame).astype(np.float32)


def cleanup_mask(mask: np.ndarray, min_area_fraction: float = 0.005,
                 close_radius: int = 3) -> np.ndarray:
    """Morphological cleanup: close gaps, drop specks, fill holes."""
    binary = (mask > 0).astype(np.uint8)
    if close_radius > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (close_radius * 2 + 1, close_radius * 2 + 1)
        )
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    # Fill holes: flood-fill from the border on the inverse image. Pad by one
    # pixel first so background regions that touch different borders stay
    # connected to the seed. Without the padding a foreground ring near the
    # frame edge partitions the background and the trapped half is filled,
    # which can blow a 5% mask up to the whole frame.
    height, width = binary.shape
    inverse = (1 - binary).astype(np.uint8)
    padded = cv2.copyMakeBorder(inverse, 1, 1, 1, 1,
                                cv2.BORDER_CONSTANT, value=1)
    flood = padded.copy()
    flood_mask = np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8)
    cv2.floodFill(flood, flood_mask, (0, 0), 0)
    holes = flood[1:-1, 1:-1].astype(bool)
    binary = (binary.astype(bool) | holes).astype(np.uint8)

    # Keep components above the minimum area.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return np.zeros_like(binary)
    min_area = min_area_fraction * height * width
    kept = np.zeros_like(binary)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= min_area:
            kept[labels == label] = 1
    return kept.astype(bool)


# ---------------------------------------------------------------------------
# Background model
# ---------------------------------------------------------------------------

@dataclass
class BackgroundModel:
    median: np.ndarray   # HxWx3 float32
    std: np.ndarray      # HxWx3 float32

    @classmethod
    def build(cls, frames: Sequence[np.ndarray]) -> "BackgroundModel":
        if len(frames) < 3:
            raise ValueError("Need at least 3 background frames")
        stack = np.stack([_to_float(frame) for frame in frames])
        median = np.median(stack, axis=0)
        std = np.clip(stack.std(axis=0), 1.0, None)
        return cls(median=median, std=std)

    def foreground_mask(self, frame: np.ndarray, k: float = 3.0,
                        min_area_fraction: float = 0.005) -> np.ndarray:
        diff = np.abs(_to_float(frame) - self.median).max(axis=2)
        threshold = k * self.std.max(axis=2)
        raw = diff > threshold
        return cleanup_mask(raw, min_area_fraction=min_area_fraction)

    def deviation(self, frame: np.ndarray) -> np.ndarray:
        """Per-pixel deviation in units of background std (for diagnostics)."""
        diff = np.abs(_to_float(frame) - self.median).max(axis=2)
        return diff / self.std.max(axis=2)

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            str(path), median=self.median.astype(np.float32),
            std=self.std.astype(np.float32),
        )

    @classmethod
    def load(cls, path: str | Path) -> "BackgroundModel":
        data = np.load(str(path))
        return cls(median=data["median"], std=data["std"])


# ---------------------------------------------------------------------------
# Mask proposals
# ---------------------------------------------------------------------------

def border_background_color(frame: np.ndarray, border_fraction: float = 0.04
                            ) -> np.ndarray:
    """Median border color: a robust plain-background estimate."""
    image = _resize(frame)
    height, width = image.shape[:2]
    border = max(1, int(round(min(height, width) * border_fraction)))
    strips = np.concatenate([
        image[:border].reshape(-1, 3),
        image[-border:].reshape(-1, 3),
        image[:, :border].reshape(-1, 3),
        image[:, -border:].reshape(-1, 3),
    ])
    return np.median(strips, axis=0)


def foreground_from_border(frame: np.ndarray, color_tolerance: float = 35.0,
                           min_area_fraction: float = 0.005) -> np.ndarray:
    """Fallback foreground: pixels far from the border color."""
    image = _to_float(frame)
    background = border_background_color(frame)
    diff = np.abs(image - background).max(axis=2)
    return cleanup_mask(diff > color_tolerance,
                        min_area_fraction=min_area_fraction)


def proposal_from_frames(good_frames: Sequence[np.ndarray],
                         background: BackgroundModel | None = None,
                         k: float = 3.0,
                         border_tolerance: float = 35.0,
                         coverage_fraction: float = 0.5,
                         min_area_fraction: float = 0.005) -> np.ndarray:
    """Union of foreground masks across good frames.

    With a background model, deviations from the empty scene are used. Without
    one, the border-color fallback is used (plain backdrops only).
    """
    if not good_frames:
        raise ValueError("Need at least one good frame")
    masks = []
    for frame in good_frames:
        if background is not None:
            masks.append(background.foreground_mask(frame, k=k))
        else:
            masks.append(foreground_from_border(frame, border_tolerance))
    coverage = np.mean(np.stack(masks), axis=0)
    threshold = max(coverage_fraction, 1e-3)
    return cleanup_mask(coverage >= threshold,
                        min_area_fraction=min_area_fraction)


# ---------------------------------------------------------------------------
# Metrics and presence regions
# ---------------------------------------------------------------------------

def mask_metrics(proposed: np.ndarray, approved: np.ndarray) -> dict:
    """IoU and Dice between two boolean masks."""
    a = proposed.astype(bool)
    b = approved.astype(bool)
    intersection = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    iou = float(intersection / union) if union else 1.0
    dice = float(2 * intersection / (a.sum() + b.sum())) if (a.sum() + b.sum()) else 1.0
    return {"iou": iou, "dice": dice}


def background_false_activation(frame: np.ndarray, approved_mask: np.ndarray,
                                background: BackgroundModel, k: float = 3.0
                                ) -> float:
    """Fraction of detected foreground that lies outside the approved mask."""
    detected = background.foreground_mask(frame, k=k)
    detected_pixels = detected.sum()
    if detected_pixels == 0:
        return 0.0
    outside = np.logical_and(detected, np.logical_not(approved_mask)).sum()
    return float(outside / detected_pixels)


def presence_regions(good_masks: Sequence[np.ndarray],
                     min_fraction: float = 0.95,
                     min_area_fraction: float = 0.01) -> np.ndarray:
    """Sub-regions that are foreground in >= min_fraction of good frames.

    These are regions the product reliably occupies. A sudden drop in their
    foreground coverage at runtime is a component-agnostic "missing part"
    signal (no colors, no part names).
    """
    if not good_masks:
        raise ValueError("Need at least one good mask")
    coverage = np.mean(np.stack([m.astype(bool) for m in good_masks]), axis=0)
    return cleanup_mask(coverage >= min_fraction,
                        min_area_fraction=min_area_fraction)


def _drop_border_components(mask: np.ndarray,
                            margin_fraction: float = 0.02) -> np.ndarray:
    """Remove components that touch the frame border.

    Background clutter (table edges, people) enters from the frame edges; a
    presence region pinned to the border is not trustworthy product area.
    """
    if not mask.any():
        return mask
    height, width = mask.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), 8)
    margin_x = max(1, int(round(width * margin_fraction)))
    margin_y = max(1, int(round(height * margin_fraction)))
    kept = np.zeros_like(mask, dtype=bool)
    for label in range(1, count):
        x = stats[label, cv2.CC_STAT_LEFT]
        y = stats[label, cv2.CC_STAT_TOP]
        w = stats[label, cv2.CC_STAT_WIDTH]
        h = stats[label, cv2.CC_STAT_HEIGHT]
        if (x < margin_x or y < margin_y
                or x + w > width - margin_x or y + h > height - margin_y):
            continue
        kept |= labels == label
    return kept


def propose_presence_regions(fg_masks: Sequence[np.ndarray],
                             approved_mask: np.ndarray | None = None,
                             min_fraction: float = 0.95,
                             min_area_fraction: float = 0.01,
                             fallback_min_fractions: Sequence[float] = (
                                 0.90, 0.85, 0.80),
                             border_margin_fraction: float = 0.02,
                             ) -> tuple[np.ndarray, float | None]:
    """Presence regions with background-clutter guards.

    Tries ``min_fraction`` first, then each fallback, and returns the first
    candidate that still has regions after:
    - intersecting with the approved canonical mask (only scored product
      area can be "expected"), and
    - dropping components that touch the frame border.

    Returns ``(regions, used_min_fraction)``; ``used_min_fraction`` is None
    when nothing is stable at any attempted fraction. The caller surfaces
    that as an onboarding warning (the missing-part check is disabled).
    """
    if not fg_masks:
        return np.zeros((1, 1), dtype=bool), None
    empty = np.zeros(fg_masks[0].shape, dtype=bool)
    for fraction in (min_fraction, *fallback_min_fractions):
        regions = presence_regions(fg_masks, min_fraction=fraction,
                                   min_area_fraction=min_area_fraction)
        if approved_mask is not None:
            regions = np.logical_and(regions, approved_mask.astype(bool))
        regions = _drop_border_components(regions, border_margin_fraction)
        if regions.any():
            return regions, float(fraction)
    return empty, None


def proposal_quality_warnings(fg_masks: Sequence[np.ndarray],
                              proposal: np.ndarray | None,
                              regions: np.ndarray | None,
                              used_min_fraction: float | None) -> list[str]:
    """Plain-language onboarding warnings for the profile wizard.

    These do not block profile creation; they tell the operator why the
    component-level checks will be weak (varied framing, inconsistent
    background, invisible product).
    """
    warnings: list[str] = []
    if proposal is not None:
        mask_area = float(proposal.mean())
        if mask_area < 0.05:
            warnings.append(
                f"canonical mask covers only {mask_area:.0%} of the frame - "
                "the product is not framed consistently"
            )
    if regions is None or not regions.any():
        warnings.append(
            "no stable product regions found - the missing-part check will "
            "be disabled for this profile"
        )
    elif used_min_fraction is not None and used_min_fraction < 0.95:
        warnings.append(
            f"product regions are only stable in {used_min_fraction:.0%} of "
            "the good images"
        )
    unreliable = [i + 1 for i, m in enumerate(fg_masks)
                  if m.mean() < 0.01 or m.mean() > 0.75]
    if unreliable:
        shown = ", ".join(str(i) for i in unreliable[:5])
        warnings.append(
            f"background detection is unreliable for good image(s) {shown} - "
            "use a fixed fixture or capture background frames"
        )
    return warnings


def region_coverage(mask: np.ndarray, region: np.ndarray) -> float:
    """Fraction of a presence region covered by the current foreground mask."""
    region_pixels = region.sum()
    if region_pixels == 0:
        return 1.0
    return float(np.logical_and(mask.astype(bool), region).sum() / region_pixels)


def missing_region_fraction(mask: np.ndarray, regions: np.ndarray,
                            coverage_floor: float) -> float:
    """Fraction of presence-region pixels missing at runtime."""
    if regions.sum() == 0:
        return 0.0
    covered = region_coverage(mask, regions)
    return max(0.0, coverage_floor - covered) / coverage_floor
