#!/usr/bin/env python3
"""Benchmark PatchCore backbones across MVTec AD categories.

Trains one model per (category, backbone) on the good-only training split and
evaluates on the held-out test split. This is the generalization benchmark:
the model never sees the product category it is scored on beyond the good
images, exactly like onboarding a new customer product.

Usage:
    python3 tools/benchmark_generalization.py \
        --categories bottle transistor screw wood \
        --backbones wide_resnet50 dinov2_vits14 \
        --device mps

Results are printed as a table and written to JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.inference import PatchCoreModel  # noqa: E402

MVTEC_ROOT = ROOT / "data" / "mvtec_hf"


# ---------------------------------------------------------------------------
# Metrics (no sklearn dependency)
# ---------------------------------------------------------------------------

def _rank_auroc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Rank-based AUROC with correct tie handling."""
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=bool)
    n_pos = int(labels.sum())
    n_neg = int(len(labels) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")

    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    ranks = np.empty(len(scores), dtype=np.float64)
    i = 0
    while i < len(sorted_scores):
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        ranks[order[i:j + 1]] = average_rank
        i = j + 1

    rank_sum_pos = ranks[labels].sum()
    return float((rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def _fpr_at_tpr(scores: np.ndarray, labels: np.ndarray, tpr: float = 0.95) -> float:
    """False-positive rate when recall is at least ``tpr``."""
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=bool)
    pos = scores[labels]
    neg = scores[~labels]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    threshold = np.quantile(pos, 1.0 - tpr)
    return float((neg >= threshold).mean())


# ---------------------------------------------------------------------------
# Dataset layout
# ---------------------------------------------------------------------------

def category_splits(category: str) -> dict:
    base = MVTEC_ROOT / category
    train = sorted(str(p) for p in (base / "train" / "good").glob("*.png"))
    test_good = sorted(str(p) for p in (base / "test" / "good").glob("*.png"))
    defects = {}
    for defect_dir in sorted((base / "test").iterdir()):
        if defect_dir.is_dir() and defect_dir.name != "good":
            defects[defect_dir.name] = sorted(
                str(p) for p in defect_dir.glob("*.png")
            )
    return {"train": train, "test_good": test_good, "defects": defects}


def ground_truth_mask(category: str, image_path: str) -> np.ndarray | None:
    """Mask for a defective test image, or None for good images."""
    base = MVTEC_ROOT / category
    if Path(image_path).parent.name == "good":
        return None
    stem = Path(image_path).stem
    candidates = [
        base / "ground_truth" / Path(image_path).parent.name / f"{stem}_mask.png",
        base / "ground_truth" / Path(image_path).parent.name / f"{Path(image_path).name}",
    ]
    for candidate in candidates:
        if candidate.exists():
            return cv2.imread(str(candidate), cv2.IMREAD_GRAYSCALE)
    return None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(category: str, backbone: str, args) -> dict:
    splits = category_splits(category)
    train = splits["train"]
    if args.max_train:
        train = train[: args.max_train]

    model = PatchCoreModel(
        backbone=backbone,
        backbone_kwargs=args.backbone_kwargs.get(backbone, {}),
    )
    model.to(args.device)

    t0 = time.time()
    stats = model.fit(train)
    fit_seconds = time.time() - t0

    # Image-level scoring
    image_scores: list[float] = []
    image_labels: list[bool] = []
    pixel_scores: list[np.ndarray] = []
    pixel_labels: list[np.ndarray] = []
    inference_ms: list[float] = []

    def score(path: str, is_bad: bool) -> None:
        image = cv2.imread(path)
        t = time.time()
        result = model.predict(image)
        inference_ms.append((time.time() - t) * 1000.0)
        image_scores.append(result["raw_score"])
        image_labels.append(is_bad)

        # Pixel metric at a fixed working resolution keeps memory bounded.
        anomaly = cv2.resize(result["anomaly_map"], (256, 256))
        mask = ground_truth_mask(category, path)
        if mask is None:
            gt = np.zeros((256, 256), dtype=np.uint8)
        else:
            gt = (cv2.resize(mask, (256, 256)) > 127).astype(np.uint8)
        pixel_scores.append(anomaly.astype(np.float32).ravel())
        pixel_labels.append(gt.ravel())

    for path in splits["test_good"]:
        score(path, False)
    defect_counts = {}
    for defect, paths in splits["defects"].items():
        defect_counts[defect] = len(paths)
        for path in paths:
            score(path, True)

    image_auroc = _rank_auroc(np.array(image_scores), np.array(image_labels))
    pixel_auroc = _rank_auroc(
        np.concatenate(pixel_scores), np.concatenate(pixel_labels).astype(bool)
    )
    fpr95 = _fpr_at_tpr(np.array(image_scores), np.array(image_labels))

    return {
        "category": category,
        "backbone": backbone,
        "image_auroc": round(float(image_auroc), 4),
        "pixel_auroc": round(float(pixel_auroc), 4),
        "fpr_at_95_tpr": round(float(fpr95), 4),
        "n_train": len(train),
        "n_test": len(image_scores),
        "defect_counts": defect_counts,
        "fit_seconds": round(fit_seconds, 1),
        "inference_ms_mean": round(float(np.mean(inference_ms)), 1),
        "memory_bank_size": stats["memory_bank_size"],
        "feature_map_size": stats["feature_map_size"],
        "ref_score": round(float(stats["ref_score"]), 4),
    }


def print_table(results: list[dict]) -> None:
    header = (
        f"{'category':<12} {'backbone':<16} {'imgAUROC':>9} "
        f"{'pxAUROC':>9} {'FPR@95':>7} {'fit s':>6} {'ms/img':>7} {'bank':>6}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['category']:<12} {r['backbone']:<16} "
            f"{r['image_auroc']:>9.4f} {r['pixel_auroc']:>9.4f} "
            f"{r['fpr_at_95_tpr']:>7.3f} {r['fit_seconds']:>6.1f} "
            f"{r['inference_ms_mean']:>7.1f} {r['memory_bank_size']:>6}"
        )
    print()
    backbones = sorted({r["backbone"] for r in results})
    for backbone in backbones:
        rows = [r for r in results if r["backbone"] == backbone]
        mean_img = float(np.mean([r["image_auroc"] for r in rows]))
        mean_px = float(np.mean([r["pixel_auroc"] for r in rows]))
        print(
            f"MEAN {backbone:<16} imgAUROC={mean_img:.4f} pxAUROC={mean_px:.4f} "
            f"over {len(rows)} categories"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--categories", nargs="+", required=True)
    parser.add_argument(
        "--backbones", nargs="+", default=["wide_resnet50", "dinov2_vits14"],
    )
    parser.add_argument("--device", default="cpu",
                        help="cpu, mps or cuda")
    parser.add_argument("--max-train", type=int, default=0,
                        help="cap training images per category (0 = all)")
    parser.add_argument("--dinov2-input-size", type=int, default=448)
    parser.add_argument("--dinov2-layers", type=int, nargs="+", default=[-1],
                        help="transformer block indices, e.g. -1 or 8 11")
    parser.add_argument("--output", default="",
                        help="JSON output path (default: data/benchmarks/...)")
    args = parser.parse_args()
    args.backbone_kwargs = {
        "dinov2_vits14": {
            "input_size": args.dinov2_input_size,
            "layers": tuple(args.dinov2_layers),
        },
        "dinov2_vitb14": {
            "input_size": args.dinov2_input_size,
            "layers": tuple(args.dinov2_layers),
        },
    }
    return args


def main() -> None:
    args = parse_args()
    results = []
    for category in args.categories:
        for backbone in args.backbones:
            print(f"=== {category} / {backbone} ===", flush=True)
            try:
                result = evaluate(category, backbone, args)
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED: {type(exc).__name__}: {exc}")
                result = {"category": category, "backbone": backbone,
                          "error": f"{type(exc).__name__}: {exc}"}
            results.append(result)
            if "image_auroc" in result:
                print(
                    f"  imgAUROC={result['image_auroc']} "
                    f"pxAUROC={result['pixel_auroc']} "
                    f"FPR@95={result['fpr_at_95_tpr']} "
                    f"fit={result['fit_seconds']}s",
                    flush=True,
                )

    print()
    print_table([r for r in results if "image_auroc" in r])

    output = Path(args.output) if args.output else (
        ROOT / "data" / "benchmarks"
        / f"generalization_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "args": {
            "categories": args.categories,
            "backbones": args.backbones,
            "max_train": args.max_train,
            "dinov2_input_size": args.dinov2_input_size,
            "dinov2_layers": args.dinov2_layers,
            "device": args.device,
        },
        "results": results,
    }, indent=2))
    print(f"\nwrote {output}")


if __name__ == "__main__":
    main()
