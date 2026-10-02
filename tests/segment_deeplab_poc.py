"""Test a pretrained semantic "bottle" segmenter on the eight POC images.

This is the closest off-the-shelf semantic-segmentation baseline in
torchvision: DeepLabV3-MobileNet trained on COCO/VOC labels. Results are
candidate masks, not ground-truth accuracy; review them before use.

Usage:
    python3 tests/segment_deeplab_poc.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageOps
from torchvision.models.segmentation import (
    DeepLabV3_MobileNet_V3_Large_Weights,
    deeplabv3_mobilenet_v3_large,
)


ROOT = Path(__file__).resolve().parents[1]
POC_DIR = ROOT / "data" / "poc"
MANIFEST = POC_DIR / "manifest.json"
OUTPUT_DIR = POC_DIR / "deeplab_output"
ROI_BOXES = {
    "image_01": (0.32, 0.08, 0.70, 0.85),
    "image_02": (0.04, 0.41, 0.96, 0.60),
    "image_03": (0.06, 0.41, 0.94, 0.60),
    "image_04": (0.40, 0.34, 1.00, 0.68),
    "image_05": (0.28, 0.36, 0.80, 0.80),
    "image_06": (0.32, 0.08, 0.70, 0.85),
    "image_07": (0.32, 0.11, 0.69, 0.86),
    "image_08": (0.32, 0.11, 0.69, 0.86),
}


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(ImageOps.exif_transpose(im).convert("RGB"))


def overlay(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    result = rgb.copy()
    result[mask] = (
        0.55 * result[mask].astype(np.float32)
        + 0.45 * np.array([0, 255, 50], dtype=np.float32)
    ).clip(0, 255).astype(np.uint8)
    edges = cv2.Canny(mask.astype(np.uint8) * 255, 50, 150) > 0
    result[edges] = (255, 0, 0)
    return result


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    weights = DeepLabV3_MobileNet_V3_Large_Weights.COCO_WITH_VOC_LABELS_V1
    categories = weights.meta["categories"]
    bottle_id = categories.index("bottle")
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

    print(f"Loading pretrained DeepLabV3-MobileNet on {device}.")
    model = deeplabv3_mobilenet_v3_large(weights=weights).eval().to(device)
    preprocess = weights.transforms()

    def predict_bottle_probability(rgb: np.ndarray) -> tuple[np.ndarray, float]:
        tensor = preprocess(Image.fromarray(rgb)).to(device)
        start = time.perf_counter()
        with torch.inference_mode():
            logits = model(tensor.unsqueeze(0))["out"][0]
            probabilities = logits.softmax(dim=0)[bottle_id].detach().cpu().numpy()
        elapsed_ms = (time.perf_counter() - start) * 1000
        height, width = rgb.shape[:2]
        if probabilities.shape != (height, width):
            probabilities = cv2.resize(probabilities, (width, height),
                                       interpolation=cv2.INTER_LINEAR)
        return probabilities, elapsed_ms

    report = {
        "dataset": manifest["dataset"],
        "segmenter": "torchvision DeepLabV3-MobileNet, COCO/VOC bottle class",
        "device": str(device),
        "bottle_class_index": bottle_id,
        "candidate_thresholds": [0.25, 0.40, 0.55],
        "modes": ["full_frame", "manual_camera_roi"],
        "images": [],
        "caveat": (
            "Pretrained generic bottle segmentation is not trained for this "
            "translucent packaged product. Probability scores are not mask IoU."
        ),
    }

    for entry in manifest["images"]:
        rgb = load_rgb(POC_DIR / entry["file"])
        height, width = rgb.shape[:2]
        full_probability, full_ms = predict_bottle_probability(rgb)

        rx0, ry0, rx1, ry1 = ROI_BOXES[entry["id"]]
        x0, y0 = int(rx0 * width), int(ry0 * height)
        x1, y1 = int(rx1 * width), int(ry1 * height)
        crop = rgb[y0:y1, x0:x1]
        crop_probability, crop_ms = predict_bottle_probability(crop)
        roi_probability = np.zeros((height, width), dtype=np.float32)
        roi_probability[y0:y1, x0:x1] = crop_probability

        modes = {}
        for mode, probabilities, elapsed_ms in [
            ("full_frame", full_probability, full_ms),
            ("manual_camera_roi", roi_probability, crop_ms),
        ]:
            probability_path = OUTPUT_DIR / f"{entry['id']}_{mode}_probability.png"
            Image.fromarray((probabilities * 255).clip(0, 255).astype(np.uint8)).save(
                probability_path
            )
            threshold_records = []
            for threshold in report["candidate_thresholds"]:
                mask = probabilities >= threshold
                mask_path = OUTPUT_DIR / f"{entry['id']}_{mode}_mask_{threshold:.2f}.png"
                overlay_path = OUTPUT_DIR / f"{entry['id']}_{mode}_overlay_{threshold:.2f}.png"
                Image.fromarray(mask.astype(np.uint8) * 255).save(mask_path)
                Image.fromarray(overlay(rgb, mask)).save(overlay_path)
                threshold_records.append({
                    "threshold": threshold,
                    "mask_area_fraction": float(mask.mean()),
                    "mask_path": str(mask_path.relative_to(ROOT)),
                    "overlay_path": str(overlay_path.relative_to(ROOT)),
                })
            modes[mode] = {
                "probability_mean": float(probabilities.mean()),
                "probability_max": float(probabilities.max()),
                "inference_ms": round(elapsed_ms, 1),
                "probability_map": str(probability_path.relative_to(ROOT)),
                "thresholds": threshold_records,
            }

        record = {
            "id": entry["id"],
            "file": entry["file"],
            "expected_condition": entry["condition"],
            "expected_cap_present": entry["cap_present"],
            "expected_sticker_present": entry["sticker_present"],
            "manual_roi_px": [x0, y0, x1, y1],
            "modes": modes,
        }
        report["images"].append(record)
        full_areas = ", ".join(
            f"{x['mask_area_fraction']:.1%}"
            for x in modes["full_frame"]["thresholds"]
        )
        roi_areas = ", ".join(
            f"{x['mask_area_fraction']:.1%}"
            for x in modes["manual_camera_roi"]["thresholds"]
        )
        print(
            f"{entry['id']} [{entry['condition']}]: "
            f"full max={modes['full_frame']['probability_max']:.3f}, "
            f"full mask @.25/.40/.55={full_areas}; "
            f"ROI mask={roi_areas}"
        )

    report_path = OUTPUT_DIR / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nReview masks in {OUTPUT_DIR}")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
