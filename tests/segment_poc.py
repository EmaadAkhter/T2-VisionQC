"""Run an offline instance-segmentation baseline on the eight real images.

The pretrained COCO Mask R-CNN is only a candidate foreground segmenter. It
does not detect missing caps or labels; those are separate inspection checks.
This script saves transparent-mask overlays and a JSON report so masks can be
reviewed before integrating the segmenter into the product.

Usage:
    python3 tests/segment_poc.py
    python3 tests/segment_poc.py --confidence 0.15
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageOps
from torchvision.models.detection import (
    MaskRCNN_ResNet50_FPN_Weights,
    maskrcnn_resnet50_fpn,
)


ROOT = Path(__file__).resolve().parents[1]
POC_DIR = ROOT / "data" / "poc"
MANIFEST = POC_DIR / "manifest.json"
OUTPUT_DIR = POC_DIR / "segmenter_output"


def load_rgb(path: Path) -> np.ndarray:
    """Load an image with EXIF orientation applied, return RGB uint8."""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        return np.asarray(image)


def make_overlay(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Blend a green mask and contour over the RGB source image."""
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


def load_detector(device: torch.device):
    weights = MaskRCNN_ResNet50_FPN_Weights.DEFAULT
    model = maskrcnn_resnet50_fpn(weights=weights)
    model.eval().to(device)
    category_id = weights.meta["categories"].index("bottle")
    return model, weights.transforms(), category_id


def segment_one(model, preprocess, category_id: int, device: torch.device,
                rgb: np.ndarray, confidence: float) -> dict:
    """Return the highest-confidence bottle instance mask, if any."""
    tensor = preprocess(Image.fromarray(rgb)).to(device)
    with torch.inference_mode():
        result = model([tensor])[0]

    candidates = []
    for idx, (label, score) in enumerate(zip(result["labels"], result["scores"])):
        score_value = float(score.detach().cpu())
        if int(label.detach().cpu()) != category_id or score_value < confidence:
            continue
        mask = result["masks"][idx, 0].detach().cpu().numpy()
        binary = (mask >= 0.5).astype(np.uint8)
        area = int(binary.sum())
        if area:
            candidates.append((score_value, area, binary))

    if not candidates:
        return {"found": False, "confidence": None, "area_px": 0, "mask": None}

    score_value, area, binary = max(candidates, key=lambda item: (item[0], item[1]))
    return {"found": True, "confidence": score_value, "area_px": area, "mask": binary}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confidence", type=float, default=0.10)
    parser.add_argument("--device", choices=("auto", "cpu", "mps"), default="auto")
    args = parser.parse_args()

    if args.device == "mps" and torch.backends.mps.is_available():
        device = torch.device("mps")
    elif args.device == "auto" and torch.backends.mps.is_available():
        device = torch.device("mps")
    else:
        device = torch.device("cpu")

    manifest = json.loads(MANIFEST.read_text())
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading COCO Mask R-CNN bottle segmenter on {device}.")
    print("The first run downloads official torchvision weights if not cached.")
    model, preprocess, category_id = load_detector(device)

    report = {
        "dataset": manifest["dataset"],
        "segmenter": "torchvision Mask R-CNN ResNet-50 FPN, COCO bottle class",
        "device": str(device),
        "confidence_threshold": args.confidence,
        "warning": (
            "This is an off-the-shelf baseline, not a fine-tuned production model. "
            "Bottle-mask quality must be reviewed, especially because the bottle is translucent."
        ),
        "images": [],
    }

    for entry in manifest["images"]:
        image_path = POC_DIR / entry["file"]
        rgb = load_rgb(image_path)
        result = segment_one(model, preprocess, category_id, device, rgb,
                             args.confidence)

        record = {
            "id": entry["id"],
            "file": entry["file"],
            "expected_condition": entry["condition"],
            "expected_cap_present": entry["cap_present"],
            "expected_sticker_present": entry["sticker_present"],
            "width": int(rgb.shape[1]),
            "height": int(rgb.shape[0]),
            "bottle_found": result["found"],
            "bottle_confidence": result["confidence"],
            "mask_area_px": result["area_px"],
            "mask_area_fraction": (
                result["area_px"] / float(rgb.shape[0] * rgb.shape[1])
            ),
        }

        if result["found"]:
            mask_path = OUTPUT_DIR / f"{entry['id']}_mask.png"
            overlay_path = OUTPUT_DIR / f"{entry['id']}_overlay.png"
            crop_path = OUTPUT_DIR / f"{entry['id']}_masked_crop.png"
            cv2.imwrite(str(mask_path), result["mask"] * 255)
            cv2.imwrite(str(overlay_path), cv2.cvtColor(
                make_overlay(rgb, result["mask"]), cv2.COLOR_RGB2BGR
            ))
            masked = rgb.copy()
            masked[result["mask"] == 0] = 0
            cv2.imwrite(str(crop_path), cv2.cvtColor(masked, cv2.COLOR_RGB2BGR))
            record.update({
                "mask_path": str(mask_path.relative_to(ROOT)),
                "overlay_path": str(overlay_path.relative_to(ROOT)),
                "masked_crop_path": str(crop_path.relative_to(ROOT)),
            })

        report["images"].append(record)
        status = (
            f"bottle score={result['confidence']:.3f}, "
            f"mask={record['mask_area_fraction']:.1%}"
            if result["found"] else "NO BOTTLE MASK"
        )
        print(f"{entry['id']} [{entry['condition']}]: {status}")

    report_path = OUTPUT_DIR / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nReview overlays in {OUTPUT_DIR}")
    print(f"Report: {report_path}")
    print("Do not interpret missing/partial masks as product defects; review the overlays.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
