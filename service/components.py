"""Learned component-presence checks.

Purpose-built answer to a real PatchCore blind spot: on transparent or
see-through products, a *missing component* (e.g. a peeled-off label) exposes
the background through the part, and background patches are — by definition —
"normal" for the memory bank. The anomaly score stays low and the unit passes.

Fix (still no product-specific code): during training we learn the color
signature of the product's distinctive component clusters from the very same
good photos the operator already supplied, and calibrate per-cluster minimum
fractions. At runtime a frame whose expected-component fraction drops below
the calibrated limit fails the inspection.

Design gates (all enforced by calibrate_component_check):
- Learned from the user's own good images; no product names, no hardcoded
  colors or part names.
- Self-disabling: if good frames and synthesized component-removed frames
  cannot be separated with a solid margin, the cluster (or the whole check)
  returns ``None`` / is dropped and the check stays off (e.g. bare metal
  parts with no colored component).
- Saturation-weighted hue histograms keep dull background colors (wood,
  marble veining) from polluting a cluster's hue window.
- Every decision is logged in the returned spec for auditability.

All functions operate at :data:`service.inference.INPUT_SIZE`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import cv2
import numpy as np

from service.inference import INPUT_SIZE

# HSV floors: pixels at or below these count as "colorless" (white/gray
# walls, transparent plastic). OpenCV hue range is 0..179.
NEUTRAL_S_MAX = 60
VALUE_FLOOR = 40
HUE_WINDOW = 14        # member collection window around a seed (of 180)

# Cluster saturation/value floors are derived from pooled member percentiles,
# but never dropped below these "clearly colored" limits.
S_FLOOR = 85
V_FLOOR = 35

# A seed must own at least this share of all clearly-colored pixels.
CLUSTER_MIN_SHARE = 0.05
MAX_CLUSTERS = 3

# Per-cluster acceptance: the 5th percentile of good frames must beat the
# worst component-removed frame by this ratio, and be at least this large.
MIN_RATIO = 3.0
MIN_ABS_FRACTION = 0.002
REVIEW_BAND = 0.60     # REVIEW when fraction < REVIEW_BAND * min_fraction


# ---------------------------------------------------------------------------
# Pixel helpers
# ---------------------------------------------------------------------------

def to_input_size(frame_bgr: np.ndarray) -> np.ndarray:
    """Resize to INPUT_SIZE (the scale every fraction is calibrated on)."""
    if frame_bgr.shape[:2] == INPUT_SIZE:
        return frame_bgr
    return cv2.resize(frame_bgr, INPUT_SIZE, interpolation=cv2.INTER_AREA)


def hsv_of(frame_bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(to_input_size(frame_bgr), cv2.COLOR_BGR2HSV)


def hue_distance(h_a: float, h_b: float) -> float:
    diff = abs(h_a - h_b) % 180.0
    return min(diff, 180.0 - diff)


# ---------------------------------------------------------------------------
# Runtime scoring
# ---------------------------------------------------------------------------

def component_mask(hsv: np.ndarray, clusters: Sequence[Dict[str, Any]]
                   ) -> np.ndarray:
    """Boolean mask of pixels matching any learned cluster."""
    hue = hsv[..., 0].astype(np.int16)
    sat = hsv[..., 1].astype(np.int16)
    val = hsv[..., 2].astype(np.int16)
    hit = np.zeros(hue.shape, dtype=bool)
    for cluster in clusters:
        diff = np.abs(hue - int(cluster["h"])).astype(np.int16)
        close = np.minimum(diff, 180 - diff) <= cluster["tol"]
        hit |= close & (sat >= cluster["s_min"]) & (val >= cluster["v_min"])
    return hit


def evaluate_component_check(frame_bgr: np.ndarray,
                             spec: Dict[str, Any]) -> Dict[str, Any]:
    """Runtime check: per-cluster fractions + worst severity.

    Returns ``{"severity": "FAIL" | "REVIEW" | None, "fractions": [...],
    "frailest": index or None}`` where ``frailest`` is the cluster closest
    to its limit (for diagnostics).
    """
    hsv = hsv_of(frame_bgr)
    fractions = []
    severity = None
    frailest = None
    worst_margin = 1e9
    for index, cluster in enumerate(spec["clusters"]):
        fraction = float(component_mask(hsv, [cluster]).mean())
        fractions.append(fraction)
        limit = cluster["min_fraction"]
        margin = (fraction - limit) / limit if limit > 0 else 1.0
        if margin < worst_margin:
            worst_margin = margin
            frailest = index
        if fraction < cluster["review_fraction"]:
            severity = "FAIL"
        elif fraction < limit and severity is None:
            severity = "REVIEW"
    return {"severity": severity, "fractions": fractions,
            "frailest": frailest}


def component_fraction(frame_bgr: np.ndarray, spec: Dict[str, Any]) -> float:
    """Fraction of frame pixels inside any learned cluster (union)."""
    hsv = hsv_of(frame_bgr)
    return float(component_mask(hsv, spec["clusters"]).mean())


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def _weighted_hue_histogram(hsvs: Sequence[np.ndarray]
                            ) -> tuple[np.ndarray, float]:
    """Pooled hue histogram of clearly-colored pixels, saturation-weighted.

    Weighting by saturation keeps strongly colored pixels (a red logo) from
    being outvoted by weakly colored background pixels (wood, veining) that
    share the hue window.
    """
    histogram = np.zeros(180, np.float64)
    weight_total = 0.0
    for hsv in hsvs:
        hue = hsv[..., 0].astype(np.int16)
        sat = hsv[..., 1].astype(np.float64)
        val = hsv[..., 2].astype(np.int16)
        selected = (sat > NEUTRAL_S_MAX) & (val > VALUE_FLOOR)
        weights = (sat - NEUTRAL_S_MAX)[selected]
        histogram += np.bincount(hue[selected], weights=weights, minlength=180)
        weight_total += float(weights.sum())
    return histogram, weight_total


def _seed_hues(histogram: np.ndarray, total: float) -> List[int]:
    """Greedy peaks of the pooled hue histogram, at most MAX_CLUSTERS.

    Smoothing only locates peaks; acceptance is measured as the raw histogram
    mass within ±HUE_WINDOW of the peak, so cluster share is true mass.
    """
    kernel = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
    kernel /= kernel.sum()
    padded = np.concatenate([histogram[-2:], histogram, histogram[:2]])
    smooth = np.convolve(padded, kernel, mode="valid")

    seeds: List[int] = []
    work = smooth.copy()
    while len(seeds) < MAX_CLUSTERS:
        peak = int(work.argmax())
        span = [(peak + offset) % 180
                for offset in range(-HUE_WINDOW, HUE_WINDOW + 1)]
        mass = float(histogram[span].sum())
        if mass < CLUSTER_MIN_SHARE * total:
            break
        seeds.append(peak)
        work[span] = 0.0
    return seeds


def _circular_mean_deg(hues: np.ndarray,
                       weights: np.ndarray | None = None) -> float:
    if weights is None:
        weights = np.ones_like(hues, dtype=np.float64)
    radians = np.deg2rad(hues.astype(np.float64) * 2.0)
    sin_mean = float(np.average(np.sin(radians), weights=weights))
    cos_mean = float(np.average(np.cos(radians), weights=weights))
    angle = np.arctan2(sin_mean, cos_mean)
    return (np.rad2deg(angle) / 2.0) % 180.0


def _circular_std_deg(hues: np.ndarray) -> float:
    radians = np.deg2rad(hues.astype(np.float64) * 2.0)
    r = float(np.hypot(np.mean(np.cos(radians)), np.mean(np.sin(radians))))
    r = min(max(r, 1e-6), 1.0)
    return float(np.sqrt(-2.0 * np.log(r)) / 2.0)


def _build_clusters(hsvs: Sequence[np.ndarray],
                    seeds: Sequence[int]) -> List[Dict[str, Any]]:
    """Build cluster specs from pooled member pixels across all frames.

    The seed bin itself is the cluster center: weighted means can drift a
    few degrees toward background hues, and at this scale a 3-degree drift
    is the difference between clean separation and wood leaking in.
    """
    clusters: List[Dict[str, Any]] = []
    frame_area = float(hsvs[0].shape[0] * hsvs[0].shape[1])
    for seed in seeds:
        window = 8
        member_hues, member_sats, member_vals = [], [], []
        for hsv in hsvs:
            frame_hue = hsv[..., 0].astype(np.int16)
            sat = hsv[..., 1]
            val = hsv[..., 2]
            near = np.array([hue_distance(h, seed) <= window
                             for h in range(180)])
            member = near[frame_hue] & (sat > NEUTRAL_S_MAX) & (
                val > VALUE_FLOOR)
            member_hues.append(frame_hue[member].astype(np.float64))
            member_sats.append(sat[member].astype(np.float64))
            member_vals.append(val[member].astype(np.float64))
        hues = np.concatenate(member_hues)
        sats = np.concatenate(member_sats)
        vals = np.concatenate(member_vals)
        weight = hues.size / frame_area
        if weight < 0.01:
            continue
        clusters.append({
            "h": float(seed),
            "tol": window,
            # Retained percentiles feed the (s_min, v_min) variant search in
            # calibration; the floors here are just the initial guess.
            "s_min": float(max(S_FLOOR, np.percentile(sats, 25) - 25)),
            "v_min": float(max(V_FLOOR, np.percentile(vals, 10) - 30)),
            "s_p25": float(np.percentile(sats, 25)),
            "s_p50": float(np.percentile(sats, 50)),
            "v_p10": float(np.percentile(vals, 10)),
            "v_p25": float(np.percentile(vals, 25)),
            "v_p50": float(np.percentile(vals, 50)),
        })
    return _drop_overlapping(clusters)


def _drop_overlapping(clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove clusters whose centers sit inside an earlier cluster's window."""
    kept: List[Dict[str, Any]] = []
    for cluster in clusters:
        if all(hue_distance(cluster["h"], kept_one["h"])
               > max(cluster["tol"], kept_one["tol"])
               for kept_one in kept):
            kept.append(cluster)
    return kept


def _cluster_variants(cluster: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Candidate (s_min, v_min) derivations for one hue window.

    A tighter floor hides background look-alikes; a looser floor keeps
    shaded component pixels counted. The best variant is chosen by measured
    separation, not by assumption.
    """
    variants = []
    for s_min in (
        max(S_FLOOR, cluster["s_p25"] - 25),
        max(S_FLOOR, cluster["s_p50"] - 40),
    ):
        for v_min in (
            max(V_FLOOR, cluster["v_p10"] - 30),
            max(V_FLOOR, cluster["v_p25"] - 25),
            max(V_FLOOR, cluster["v_p50"] - 40),
        ):
            variants.append({
                "h": cluster["h"], "tol": cluster["tol"],
                "s_min": float(s_min), "v_min": float(v_min),
            })
    unique: List[Dict[str, Any]] = []
    for variant in variants:
        if not any(v["s_min"] == variant["s_min"]
                   and v["v_min"] == variant["v_min"] for v in unique):
            unique.append(variant)
    return unique


def doctor_frame(frame_bgr: np.ndarray, clusters: Sequence[Dict[str, Any]],
                 mode: str) -> np.ndarray:
    """Synthesize a component-removed frame for validation.

    mode="fill": paint the component area with the median border color
    (simulates seeing the plain background through the part).
    mode="inpaint": inpaint the area from its surroundings (smoother).
    mode="cap_only": keep only the top-most component blob (e.g. a cap) and
    remove everything else — the "label gone, cap present" case.
    """
    hsv = hsv_of(frame_bgr)
    union_mask = component_mask(hsv, clusters)
    if mode == "cap_only":
        # Keep the top-most component blob (e.g. the cap) and remove the
        # rest of the component: the "label gone, cap present" case. Only
        # trust the blob if it is cap-sized; on upside-down frames the
        # top-most blob is the label itself, and then everything is removed.
        count, labels, _stats, centroids = cv2.connectedComponentsWithStats(
            union_mask.astype(np.uint8), 8)
        if count > 1:
            top_label, top_y = 0, float("inf")
            for label in range(1, count):
                if _stats[label, cv2.CC_STAT_AREA] < 30:
                    continue
                if centroids[label][1] < top_y:
                    top_y, top_label = centroids[label][1], label
            if top_label and _stats[top_label, cv2.CC_STAT_AREA] <= 0.01 * union_mask.size:
                union_mask = union_mask & ~(labels == top_label)
    mask = (union_mask * 255).astype(np.uint8)
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                      (9, 9)))
    sized = to_input_size(frame_bgr)
    if mask.sum() == 0:
        return sized.copy()
    if mode == "fill":
        height, width = sized.shape[:2]
        border = max(1, round(min(height, width) * 0.04))
        strips = np.concatenate([
            sized[:border].reshape(-1, 3),
            sized[-border:].reshape(-1, 3),
            sized[:, :border].reshape(-1, 3),
            sized[:, -border:].reshape(-1, 3),
        ])
        out = sized.copy()
        out[mask > 0] = np.median(strips, axis=0)
        return out
    return cv2.inpaint(sized, mask, 5, cv2.INPAINT_TELEA)


def calibrate_component_check(image_paths: Sequence[str]
                              ) -> Dict[str, Any] | None:
    """Learn + calibrate a component check from good images only.

    Returns a spec dict to embed in the model checkpoint, or None when the
    product shows no component the check could verify reliably.
    """
    frames = []
    for path in image_paths:
        frame = cv2.imread(str(path))
        if frame is not None:
            frames.append(frame)
    if len(frames) < 3:
        return None

    hsvs = [hsv_of(frame) for frame in frames]
    histogram, total = _weighted_hue_histogram(hsvs)
    seeds = _seed_hues(histogram, total)
    clusters = _build_clusters(hsvs, seeds)
    if not clusters:
        return None

    # Adversarial validation per cluster: remove ONLY this cluster's pixels
    # (inpaint = smooth removal, fill = flat background) and require every
    # doctored frame to sit far below the good frames for that cluster.
    # The (s_min, v_min) floors are chosen by a small deterministic variant
    # search: the derivation that separates best wins.
    accepted: List[Dict[str, Any]] = []
    for cluster in clusters:
        best = None
        for variant in _cluster_variants(cluster):
            good = [float(component_mask(hsv, [variant]).mean())
                    for hsv in hsvs]
            removed = []
            for hsv, frame in zip(hsvs, frames):
                for mode in ("inpaint", "fill"):
                    missing = doctor_frame(frame, [variant], mode=mode)
                    removed.append(float(component_mask(hsv_of(missing),
                                                        [variant]).mean()))
            good_p05 = float(np.percentile(good, 5))
            missing_max = max(removed)
            if good_p05 < MIN_ABS_FRACTION:
                continue
            if missing_max > 0 and good_p05 < MIN_RATIO * missing_max:
                continue
            margin = good_p05 - missing_max
            if best is None or margin > best[0]:
                best = (margin, variant, good_p05, missing_max)
        if best is None:
            continue  # this cluster cannot gate reliably; drop it
        _, variant, good_p05, missing_max = best
        min_fraction = missing_max + 0.5 * (good_p05 - missing_max)
        review_fraction = missing_max + 0.25 * (good_p05 - missing_max)
        accepted.append({
            **{k: variant[k] for k in ("h", "tol", "s_min", "v_min")},
            "min_fraction": round(float(min_fraction), 4),
            "review_fraction": round(float(review_fraction), 4),
            "good_p05": round(good_p05, 4),
            "missing_max": round(float(missing_max), 4),
        })

    if not accepted:
        return None
    return {
        "version": 1,
        "clusters": accepted,
        "review_band": REVIEW_BAND,
    }
