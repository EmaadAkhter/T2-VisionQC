"""Try a general foreground-segmentation model on VisionQC's real POC images.

Unlike the COCO bottle detector, this model predicts a salient foreground
mask and is not expected to recognize the bottle class. It is a candidate to
compare, not a production acceptance claim. The first run downloads the local
IS-Net weights through rembg; inference itself is offline after caching.

Usage:
    python3 tests/segment_rembg_poc.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from rembg import new_session, remove


ROOT = Path(__file__).resolve().parents[1]
POC_DIR = ROOT / "data" / "poc"
MANIFEST = POC_DIR / "manifest.json"
OUTPUT_DIR = POC_DIR / "rembg_output"
MODEL_NAME = "isnet-general-use"


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(ImageOps.exif_transpose(im).convert("RGB"))


def largest_component(mask: np.ndarray) -> np.ndarray:
    """Return the largest non-background connected component."""
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return binary
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (labels == idx).astype(np.uint8)


def make_overlay(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    output = rgb.copy()
    tint = np.zeros_like(output)
    tint[..., 1] = 255
    active = mask > 0
    output[active] = (
        0.55 * output[active].astype(np.float32)
        + 0.45 * tint[active].astype(np.float32)
    ).clip(0, 255).astype(np.uint8)
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(output, contours, -1, (255, 30, 30), 2)
    return output


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Loading {MODEL_NAME} foreground segmenter.")
    print("The first run fetches weights; later runs use the local cache.")
    session = new_session(MODEL_NAME)

    report = {
        "dataset": manifest["dataset"],
        "segmenter": MODEL_NAME,
        "images": [],
        "caveat": (
            "Saliency foreground segmentation is not semantic product understanding. "
            "The translucent bottle may blend into the pale background; inspect every overlay."
        ),
    }

    for entry in manifest["images"]:
        path = POC_DIR / entry["file"]
        rgb = load_rgb(path)
        start = time.perf_counter()
        rgba = remove(Image.fromarray(rgb), session=session, alpha_matting=False)
        elapsed_ms = (time.perf_counter() - start) * 1000
        rgba = np.asarray(rgba.convert("RGBA"))
        alpha = rgba[..., 3]

        raw_mask = (alpha >= 128).astype(np.uint8)
        clean_mask = largest_component(raw_mask)
        raw_fraction = float(raw_mask.mean())
        clean_fraction = float(clean_mask.mean())

        raw_path = OUTPUT_DIR / f"{entry['id']}_alpha.png"
        mask_path = OUTPUT_DIR / f"{entry['id']}_mask.png"
        overlay_path = OUTPUT_DIR / f"{entry['id']}_overlay.png"
        cutout_path = OUTPUT_DIR / f"{entry['id']}_cutout.png"

        Image.fromarray(alpha).save(raw_path)
        Image.fromarray(clean_mask * 255).save(mask_path)
        Image.fromarray(make_overlay(rgb, clean_mask)).save(overlay_path)
        cutout = np.dstack([rgb, clean_mask * 255])
        Image.fromarray(cutout, mode="RGBA").save(cutout_path)

        record = {
            "id": entry["id"],
            "file": entry["file"],
            "expected_condition": entry["condition"],
            "expected_cap_present": entry["cap_present"],
            "expected_sticker_present": entry["sticker_present"],
            "width": int(rgb.shape[1]),
            "height": int(rgb.shape[0]),
            "inference_ms": round(elapsed_ms, 1),
            "raw_alpha_foreground_fraction": round(raw_fraction, 4),
            "largest_component_fraction": round(clean_fraction, 4),
            "alpha_path": str(raw_path.relative_to(ROOT)),
            "mask_path": str(mask_path.relative_to(ROOT)),
            "overlay_path": str(overlay_path.relative_to(ROOT)),
            "cutout_path": str(cutout_path.relative_to(ROOT)),
        }
        report["images"].append(record)
        print(
            f"{entry['id']} [{entry['condition']}]: "
            f"foreground={raw_fraction:.1%}, largest={clean_fraction:.1%}, "
            f"{elapsed_ms:.0f} ms"
        )

    report_path = OUTPUT_DIR / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nReview overlays in {OUTPUT_DIR}")
    print(f"Report: {report_path}")
    print("Masks are candidates only; do not train or classify from them without review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
