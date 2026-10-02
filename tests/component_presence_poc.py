"""Heuristic POC: cap/sticker presence after prompted SAM segmentation.

This is a diagnostic on the eight supplied images, not a production classifier.
It projects the masked bottle onto its principal axis so the bottle can be
sideways/diagonal, then measures blue pixels near either end (cap cue) and
colored pixels in the middle (sticker cue).

Usage:
    python3 tests/component_presence_poc.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
POC_DIR = ROOT / "data" / "poc"
OUT_DIR = POC_DIR / "component_output"


def principal_projection(mask: np.ndarray):
    ys, xs = np.where(mask > 0)
    points = np.column_stack([xs, ys]).astype(np.float32)
    if len(points) < 20:
        raise ValueError("Segmentation mask is empty or too small")
    center = points.mean(axis=0)
    _, vectors = np.linalg.eigh(np.cov(points.T))
    axis = vectors[:, -1]
    projections = (points - center) @ axis
    low, high = float(projections.min()), float(projections.max())
    normalized = (projections - low) / max(high - low, 1e-6)
    return xs, ys, normalized


def score_components(image_path: Path, mask_path: Path) -> dict:
    with Image.open(image_path) as im:
        rgb = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
    mask = np.asarray(Image.open(mask_path).convert("L")) > 0
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)

    xs, ys, along = principal_projection(mask)
    h = hsv[ys, xs, 0].astype(np.int16)
    s = hsv[ys, xs, 1].astype(np.int16)
    v = hsv[ys, xs, 2].astype(np.int16)

    # OpenCV hue is 0..179. Blue cap and red/blue sticker are separate cues.
    blue = (h >= 92) & (h <= 135) & (s >= 75) & (v >= 35)
    red = ((h <= 12) | (h >= 168)) & (s >= 75) & (v >= 35)
    endpoint = (along <= 0.13) | (along >= 0.87)
    center_band = (along >= 0.18) & (along <= 0.82)

    endpoint_n = max(int(endpoint.sum()), 1)
    center_n = max(int(center_band.sum()), 1)
    cap_score = float((blue & endpoint).sum() / endpoint_n)
    sticker_score = float(((blue | red) & center_band).sum() / center_n)

    return {
        "cap_endpoint_blue_fraction": cap_score,
        "sticker_center_color_fraction": sticker_score,
        "foreground_area_fraction": float(mask.mean()),
    }


def main():
    manifest = json.loads((POC_DIR / "manifest.json").read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in manifest["images"]:
        scores = score_components(
            POC_DIR / entry["file"],
            POC_DIR / "sam_output" / f"{entry['id']}_mask.png",
        )
        row = {
            "id": entry["id"],
            "expected_condition": entry["condition"],
            "expected_cap_present": entry["cap_present"],
            "expected_sticker_present": entry["sticker_present"],
            **scores,
        }
        rows.append(row)
        print(
            f"{entry['id']} {entry['condition']:<28} "
            f"cap-end-blue={scores['cap_endpoint_blue_fraction']:.3f} "
            f"sticker-center-color={scores['sticker_center_color_fraction']:.3f}"
        )

    report = {
        "note": (
            "Exploratory color cues only; thresholds are not tuned or validated. "
            "The 8-frame set is too small for a production performance claim."
        ),
        "rows": rows,
    }
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2))
    with (OUT_DIR / "scores.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {OUT_DIR / 'report.json'} and {OUT_DIR / 'scores.csv'}")


if __name__ == "__main__":
    main()
