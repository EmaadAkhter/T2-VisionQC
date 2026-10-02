"""Benchmark VisionQC against MVTec AD.

Trains on each category's good training images, evaluates on the official
test set, and reports:
  - image-level AUROC
  - defect recall at the default threshold (FAIL or REVIEW = caught), as counts
  - false-reject rate on held-out good units (FAIL only), as counts
  - heatmap localisation: pixel AUROC on defective images
  - inference latency (median / p95)

Usage:
    python3 tests/benchmark_mvtec.py bottle cable capsule
    python3 tests/benchmark_mvtec.py --all
"""

import os
import sys
import json
import time
import argparse
import numpy as np
import cv2
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from service.inference import (
    PatchCoreModel,
    compute_verdict,
    DEFAULT_THRESHOLD,
    DEFAULT_DELTA,
)

MVTEC_ROOT = os.path.join(os.path.dirname(__file__), "..", "data", "mvtec",
                          "mvtec_anomaly_detection")
ALL_CATEGORIES = [
    "bottle", "cable", "capsule", "carpet", "grid", "hazelnut", "leather",
    "metal_nut", "pill", "screw", "tile", "toothbrush", "transistor",
    "wood", "zipper",
]


def category_paths(root: str, category: str):
    """Return (train_paths, test_items) for one MVTec category.

    test_items: list of dicts {path, defective, defect_type, mask_path}
    """
    cat_dir = os.path.join(root, category)
    train_dir = os.path.join(cat_dir, "train", "good")
    test_dir = os.path.join(cat_dir, "test")
    gt_dir = os.path.join(cat_dir, "ground_truth")

    train_paths = sorted(
        str(p) for p in Path(train_dir).glob("*.png")
    )
    test_items = []
    for defect_type in sorted(os.listdir(test_dir)):
        ddir = os.path.join(test_dir, defect_type)
        if not os.path.isdir(ddir):
            continue
        for p in sorted(Path(ddir).glob("*.png")):
            mask_path = None
            if defect_type != "good":
                candidate = os.path.join(gt_dir, defect_type, f"{p.stem}_mask.png")
                if os.path.exists(candidate):
                    mask_path = candidate
            test_items.append({
                "path": str(p),
                "defective": defect_type != "good",
                "defect_type": defect_type,
                "mask_path": mask_path,
            })
    return train_paths, test_items


def image_auroc(good_scores, bad_scores):
    """Rank-based AUROC."""
    good = np.asarray(good_scores)
    bad = np.asarray(bad_scores)
    if len(good) == 0 or len(bad) == 0:
        return None
    all_scores = np.concatenate([good, bad])
    labels = np.concatenate([np.zeros(len(good)), np.ones(len(bad))])
    order = np.argsort(all_scores, kind="mergesort")
    ranks = np.empty(len(all_scores), dtype=float)
    ranks[order] = np.arange(1, len(all_scores) + 1)
    n_good, n_bad = len(good), len(bad)
    auroc = (ranks[labels == 1].sum() - n_bad * (n_bad + 1) / 2) / (n_good * n_bad)
    return float(auroc)


def pixel_auroc(anomaly_map, mask):
    """Pixel-level AUROC of the anomaly map against a binary mask."""
    h, w = mask.shape
    resized = cv2.resize(anomaly_map, (w, h), interpolation=cv2.INTER_LINEAR)
    scores = resized.flatten()
    labels = (mask.flatten() > 0).astype(np.int32)
    if labels.min() == labels.max():
        return None
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    n_pos = int(labels.sum())
    n_neg = len(labels) - n_pos
    auroc = (ranks[labels == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    return float(auroc)


def localisation_hit(anomaly_map, mask, top_frac=0.05):
    """True if the top-scoring region of the map overlaps the defect mask."""
    h, w = mask.shape
    resized = cv2.resize(anomaly_map, (w, h), interpolation=cv2.INTER_LINEAR)
    thresh = np.quantile(resized, 1 - top_frac)
    hot = resized >= thresh
    return bool((hot & (mask > 0)).sum() > 0)


def run_category(root: str, category: str, threshold: float, delta: float,
                 verbose: bool = True):
    """Train and evaluate one category. Returns a result dict."""
    train_paths, test_items = category_paths(root, category)
    if len(train_paths) < 5:
        raise RuntimeError(f"{category}: found {len(train_paths)} train images")
    if not test_items:
        raise RuntimeError(f"{category}: no test images found")

    if verbose:
        print(f"\n{'=' * 64}")
        print(f"Category: {category}")
        print(f"  Train (good): {len(train_paths)}   Test: {len(test_items)} "
              f"({sum(1 for t in test_items if t['defective'])} defective, "
              f"{sum(1 for t in test_items if not t['defective'])} good)")

    model = PatchCoreModel(coreset_ratio=0.1)
    t0 = time.time()
    stats = model.fit(train_paths)
    train_time = time.time() - t0
    if verbose:
        print(f"  Trained in {train_time:.1f}s | bank={stats['memory_bank_size']} "
              f"| ref={stats['ref_score']:.4f} | map={stats['feature_map_size']}")

    good_scores, bad_scores = [], []
    verdicts = defaultdict(int)
    pixel_aurocs = []
    hits = 0
    n_masks = 0
    latencies = []
    per_image = []

    for item in test_items:
        img = cv2.imread(item["path"])
        if img is None:
            continue

        t1 = time.time()
        pred = model.predict(img)
        latencies.append((time.time() - t1) * 1000)

        score = pred["normalized_score"]
        verdict = compute_verdict(score, threshold, delta)
        verdicts[verdict] += 1
        per_image.append({
            "path": item["path"],
            "defect_type": item["defect_type"],
            "defective": item["defective"],
            "score": score,
            "raw_score": pred["raw_score"],
        })

        if item["defective"]:
            bad_scores.append(score)
            if item["mask_path"]:
                mask = cv2.imread(item["mask_path"], cv2.IMREAD_GRAYSCALE)
                if mask is not None:
                    n_masks += 1
                    pa = pixel_auroc(pred["anomaly_map"], mask)
                    if pa is not None:
                        pixel_aurocs.append(pa)
                    if localisation_hit(pred["anomaly_map"], mask):
                        hits += 1
        else:
            good_scores.append(score)

    n_good = len(good_scores)
    n_bad = len(bad_scores)

    caught = verdicts["REVIEW"] + verdicts["FAIL"]  # defective verdicts only
    # Recompute precisely: classify by score
    bad_pass = sum(1 for s in bad_scores if compute_verdict(s, threshold, delta) == "PASS")
    bad_review = sum(1 for s in bad_scores if compute_verdict(s, threshold, delta) == "REVIEW")
    bad_fail = sum(1 for s in bad_scores if compute_verdict(s, threshold, delta) == "FAIL")
    good_pass = sum(1 for s in good_scores if compute_verdict(s, threshold, delta) == "PASS")
    good_review = sum(1 for s in good_scores if compute_verdict(s, threshold, delta) == "REVIEW")
    good_fail = sum(1 for s in good_scores if compute_verdict(s, threshold, delta) == "FAIL")

    caught = bad_review + bad_fail
    recall = caught / n_bad if n_bad else 0.0
    false_reject_rate = good_fail / n_good if n_good else 0.0
    auroc = image_auroc(good_scores, bad_scores)

    lat = np.asarray(latencies)
    result = {
        "category": category,
        "train_images": len(train_paths),
        "train_time_s": round(train_time, 1),
        "memory_bank": stats["memory_bank_size"],
        "ref_score": stats["ref_score"],
        "n_good": n_good,
        "n_defective": n_bad,
        "good_pass": good_pass,
        "good_review": good_review,
        "good_fail": good_fail,
        "bad_pass": bad_pass,
        "bad_review": bad_review,
        "bad_fail": bad_fail,
        "caught": caught,
        "recall": recall,
        "false_rejects": good_fail,
        "false_reject_rate": false_reject_rate,
        "auroc": auroc,
        "pixel_auroc_mean": float(np.mean(pixel_aurocs)) if pixel_aurocs else None,
        "localisation_hits": hits,
        "localisation_total": n_masks,
        "latency_median_ms": float(np.median(lat)),
        "latency_p95_ms": float(np.percentile(lat, 95)),
        "good_score_mean": float(np.mean(good_scores)) if good_scores else None,
        "bad_score_mean": float(np.mean(bad_scores)) if bad_scores else None,
        "per_image": per_image,
    }

    if verbose:
        print(f"  Scores: good mean={result['good_score_mean']:.3f} "
              f"bad mean={result['bad_score_mean']:.3f}")
        print(f"  Verdicts good: PASS={good_pass} REVIEW={good_review} FAIL={good_fail}")
        print(f"  Verdicts bad:  PASS={bad_pass} REVIEW={bad_review} FAIL={bad_fail}")
        print(f"  AUROC: {auroc:.3f}" if auroc else "  AUROC: n/a")
        print(f"  Recall (FAIL+REVIEW): {caught}/{n_bad} = {recall:.0%}")
        print(f"  False rejects: {good_fail}/{n_good} = {false_reject_rate:.0%}")
        if pixel_aurocs:
            print(f"  Pixel AUROC (mean): {np.mean(pixel_aurocs):.3f}")
        if n_masks:
            print(f"  Localisation: {hits}/{n_masks} = {hits / n_masks:.0%}")
        print(f"  Latency: median={result['latency_median_ms']:.0f}ms "
              f"p95={result['latency_p95_ms']:.0f}ms")

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("categories", nargs="*", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--root", default=MVTEC_ROOT)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--delta", type=float, default=DEFAULT_DELTA)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--scores-out", default=None)
    args = parser.parse_args()

    categories = ALL_CATEGORIES if args.all else (args.categories or ["bottle"])
    categories = [c for c in categories if os.path.isdir(os.path.join(args.root, c))]

    if not categories:
        print(f"No categories found under {args.root}")
        print("Download and extract MVTec AD first.")
        sys.exit(1)

    print("VisionQC — MVTec AD benchmark")
    print(f"Root: {os.path.abspath(args.root)}")
    print(f"Threshold T={args.threshold}, delta={args.delta}")
    print(f"Categories: {', '.join(categories)}")

    results = []
    for cat in categories:
        try:
            results.append(run_category(args.root, cat, args.threshold, args.delta))
        except Exception as e:
            print(f"\nERROR on {cat}: {e}")

    # Summary table
    print(f"\n{'=' * 78}")
    print("SUMMARY")
    print(f"{'=' * 78}")
    header = (f"{'Category':<12} {'AUROC':>6} {'Recall':>12} {'FalseRej':>10} "
              f"{'PixelAUROC':>10} {'Local':>8} {'ms(p95)':>8}")
    print(header)
    print("-" * len(header))
    for r in results:
        auroc = f"{r['auroc']:.3f}" if r["auroc"] is not None else "n/a"
        pix = f"{r['pixel_auroc_mean']:.3f}" if r["pixel_auroc_mean"] is not None else "n/a"
        loc = (f"{r['localisation_hits']}/{r['localisation_total']}"
               if r["localisation_total"] else "n/a")
        print(f"{r['category']:<12} {auroc:>6} "
              f"{r['caught']:>3}/{r['n_defective']:<3} ({r['recall']:>3.0%}) "
              f"{r['false_rejects']:>3}/{r['n_good']:<3} ({r['false_reject_rate']:>3.0%}) "
              f"{pix:>10} {loc:>8} {r['latency_p95_ms']:>8.0f}")

    if results:
        mean_auroc = np.mean([r["auroc"] for r in results if r["auroc"] is not None])
        total_caught = sum(r["caught"] for r in results)
        total_bad = sum(r["n_defective"] for r in results)
        total_fr = sum(r["false_rejects"] for r in results)
        total_good = sum(r["n_good"] for r in results)
        print("-" * len(header))
        print(f"{'OVERALL':<12} {mean_auroc:>6.3f} "
              f"{total_caught:>3}/{total_bad:<3} ({total_caught / total_bad:>3.0%}) "
              f"{total_fr:>3}/{total_good:<3} ({total_fr / total_good:>3.0%})")

    if args.json_out:
        slim = [{k: v for k, v in r.items() if k != "per_image"} for r in results]
        with open(args.json_out, "w") as f:
            json.dump(slim, f, indent=2)
        print(f"\nResults written to {args.json_out}")

    if args.scores_out:
        with open(args.scores_out, "w") as f:
            json.dump({r["category"]: r["per_image"] for r in results}, f)
        print(f"Per-image scores written to {args.scores_out}")

    return results


if __name__ == "__main__":
    main()
