"""Prompted SAM baseline for the translucent-bottle segmentation POC.

SAM is prompted with a coarse box around the bottle. The prompts are
image-specific POC inputs, not an automatic camera detector. This tests
whether a strong general segmentation model can follow the translucent
silhouette before committing to a dedicated trained segmenter.

Usage:
    python3 tests/segment_sam_poc.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageOps
import torch
from transformers import SamModel, SamProcessor


ROOT = Path(__file__).resolve().parents[1]
POC_DIR = ROOT / "data" / "poc"
MANIFEST = POC_DIR / "manifest.json"
OUTPUT_DIR = POC_DIR / "sam_output"
MODEL_ID = "facebook/sam-vit-base"

# Coarse prompted boxes in normalized (x0, y0, x1, y1) coordinates.
# They deliberately include a little background around the bottle. A reviewer
# should inspect/refine these before using masks as training ground truth.
BOXES = {
    "image_01": (0.32, 0.08, 0.70, 0.85),
    "image_02": (0.04, 0.41, 0.96, 0.60),
    "image_03": (0.06, 0.41, 0.94, 0.60),
    "image_04": (0.40, 0.34, 1.00, 0.68),
    "image_05": (0.28, 0.36, 0.80, 0.80),
    "image_06": (0.32, 0.08, 0.70, 0.85),
    "image_07": (0.32, 0.11, 0.69, 0.86),
    "image_08": (0.32, 0.11, 0.69, 0.86),
}

# Positive/negative prompts are intentionally explicit for this POC. The
# coordinates are normalized to each image's width/height and must be reviewed
# against the saved contact sheet before use. A positive point is inside the
# bottle; a negative point is on known background.
POINT_PROMPTS = {
    "image_01": (
        [(0.50, 0.44), (0.50, 0.66), (0.50, 0.80)],
        [(0.20, 0.45), (0.80, 0.45), (0.50, 0.04), (0.50, 0.95)],
    ),
    "image_02": (
        [(0.27, 0.51), (0.64, 0.51), (0.86, 0.52)],
        [(0.50, 0.31), (0.50, 0.70), (0.04, 0.22), (0.96, 0.22)],
    ),
    "image_03": (
        [(0.24, 0.50), (0.62, 0.51), (0.85, 0.52)],
        [(0.50, 0.30), (0.50, 0.70), (0.05, 0.22), (0.96, 0.22)],
    ),
    "image_04": (
        [(0.80, 0.62), (0.63, 0.52), (0.94, 0.70)],
        [(0.18, 0.48), (0.82, 0.20), (0.17, 0.82)],
    ),
    "image_05": (
        [(0.52, 0.54), (0.66, 0.69), (0.40, 0.43)],
        [(0.15, 0.50), (0.85, 0.23), (0.15, 0.84)],
    ),
    "image_06": (
        [(0.50, 0.44), (0.50, 0.64), (0.50, 0.15)],
        [(0.18, 0.50), (0.82, 0.50), (0.50, 0.95)],
    ),
    "image_07": (
        [(0.50, 0.20), (0.50, 0.53), (0.50, 0.78)],
        [(0.17, 0.50), (0.83, 0.50), (0.50, 0.96)],
    ),
    "image_08": (
        [(0.50, 0.40), (0.50, 0.58), (0.50, 0.76)],
        [(0.16, 0.45), (0.84, 0.45), (0.50, 0.96)],
    ),
}


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(ImageOps.exif_transpose(im).convert("RGB"))


def make_overlay(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    image = Image.fromarray(rgb).convert("RGBA")
    tint = Image.new("RGBA", image.size, (0, 255, 50, 100))
    alpha = Image.fromarray((mask.astype(np.uint8) * 100), mode="L")
    image.alpha_composite(Image.composite(tint, Image.new("RGBA", image.size), alpha))
    output = np.asarray(image.convert("RGB")).copy()
    edges = cv2.Canny(mask.astype(np.uint8) * 255, 50, 150) > 0
    output[edges] = (255, 0, 0)
    return output


def keep_components_touched_by_positive_points(
    mask: np.ndarray,
) -> tuple[np.ndarray, int, float]:
    """Keep the largest connected instance and report removed speckle area.

    The POC contains one bottle per frame, so disconnected tiny SAM fragments
    are treated as background. If a future frame can contain multiple products,
    this single-instance cleanup must be replaced with per-instance tracking.
    """
    binary = mask.astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return binary, 0, 1.0

    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_label = 1 + int(np.argmax(areas))
    largest_area = int(stats[largest_label, cv2.CC_STAT_AREA])
    total_area = int(binary.sum())
    kept = (labels == largest_label).astype(np.uint8)
    retained_fraction = largest_area / max(total_area, 1)
    return kept, count - 1, retained_fraction


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"Loading {MODEL_ID} on {device}; first run downloads model weights.")
    processor = SamProcessor.from_pretrained(MODEL_ID)
    model = SamModel.from_pretrained(MODEL_ID).eval().to(device)

    report = {
        "dataset": manifest["dataset"],
        "segmenter": MODEL_ID,
        "device": str(device),
        "prompt_mode": "manual coarse bounding box per POC image",
        "quality_score_note": (
            "The SAM predicted-IoU value is the model's quality estimate, not "
            "measured IoU against a human-annotated ground-truth mask."
        ),
        "images": [],
        "caveat": (
            "These masks are prompted candidates. SAM does not itself make the "
            "live camera workflow automatic; an automatic box/detector and "
            "reviewed training masks are still required."
        ),
    }

    for entry in manifest["images"]:
        image = load_rgb(POC_DIR / entry["file"])
        height, width = image.shape[:2]
        nx0, ny0, nx1, ny1 = BOXES[entry["id"]]
        box = [
            int(nx0 * width), int(ny0 * height),
            int(nx1 * width), int(ny1 * height),
        ]
        positive, negative = POINT_PROMPTS[entry["id"]]
        points = [
            [int(x * width), int(y * height)] for x, y in positive + negative
        ]
        point_labels = [1] * len(positive) + [0] * len(negative)
        inputs = processor(
            images=Image.fromarray(image),
            input_boxes=[[box]],
            input_points=[points],
            input_labels=[point_labels],
            return_tensors="pt",
        )
        inputs = {
            key: (
                value if key in {"original_sizes", "reshaped_input_sizes"}
                else value.to(device=device, dtype=(
                    torch.float32 if value.dtype == torch.float64 else value.dtype
                ))
            )
            for key, value in inputs.items()
        }

        start = time.perf_counter()
        with torch.inference_mode():
            outputs = model(**inputs)
        elapsed_ms = (time.perf_counter() - start) * 1000

        masks = processor.image_processor.post_process_masks(
            outputs.pred_masks.detach().cpu(),
            inputs["original_sizes"],
            inputs["reshaped_input_sizes"],
            mask_threshold=0.5,
            binarize=True,
        )[0]
        scores = outputs.iou_scores[0, 0].detach().cpu().numpy()
        best = int(np.argmax(scores))
        mask = masks[0, best].numpy()
        if mask.shape != (height, width):
            mask = np.asarray(Image.fromarray(mask.astype(np.float32)).resize(
                (width, height), Image.Resampling.BILINEAR
            ))
        raw_binary = mask.astype(bool)
        # In the fixed-camera POC, the configured search ROI is a hard safety
        # boundary: SAM may select background outside its box on translucent
        # objects, so those pixels are never admitted into the product mask.
        x0, y0, x1, y1 = box
        roi = np.zeros((height, width), dtype=bool)
        roi[max(0, y0):min(height, y1), max(0, x0):min(width, x1)] = True
        roi_binary = raw_binary & roi
        binary, component_count, retained_fraction = keep_components_touched_by_positive_points(roi_binary)

        mask_path = OUTPUT_DIR / f"{entry['id']}_mask.png"
        overlay_path = OUTPUT_DIR / f"{entry['id']}_overlay.png"
        cutout_path = OUTPUT_DIR / f"{entry['id']}_cutout.png"
        Image.fromarray(binary.astype(np.uint8) * 255).save(mask_path)
        Image.fromarray(make_overlay(image, binary)).save(overlay_path)
        cutout = np.dstack([image, binary.astype(np.uint8) * 255])
        Image.fromarray(cutout, mode="RGBA").save(cutout_path)

        record = {
            "id": entry["id"],
            "file": entry["file"],
            "expected_condition": entry["condition"],
            "expected_cap_present": entry["cap_present"],
            "expected_sticker_present": entry["sticker_present"],
            "prompt_box_px": box,
            "mask_area_fraction": float(binary.mean()),
            "raw_mask_area_fraction": float(raw_binary.mean()),
            "roi_clipped_mask_area_fraction": float(roi_binary.mean()),
            "connected_components_before_filter": component_count,
            "largest_component_fraction_of_mask": retained_fraction,
            "sam_predicted_quality_estimate": float(scores[best]),
            "needs_human_review": bool(
                scores[best] < 0.80 or binary.mean() < 0.02 or binary.mean() > 0.35
            ),
            "inference_ms": round(elapsed_ms, 1),
            "mask_path": str(mask_path.relative_to(ROOT)),
            "overlay_path": str(overlay_path.relative_to(ROOT)),
            "cutout_path": str(cutout_path.relative_to(ROOT)),
        }
        report["images"].append(record)
        print(
            f"{entry['id']} [{entry['condition']}]: "
            f"box={box}, mask={binary.mean():.1%}, "
            f"SAM-IoU={scores[best]:.3f}, {elapsed_ms:.0f} ms"
        )

    report_path = OUTPUT_DIR / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nReview prompted masks in {OUTPUT_DIR}")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
