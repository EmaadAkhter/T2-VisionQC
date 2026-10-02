"""Onboard a different object type with zero code changes.

Builds a product profile from an MVTec category's good images (border-color
fallback mask, no background frames), then evaluates the profile-driven
pipeline on its official test set. Prints masked vs unmasked AUROC to show
what restricting scoring to the learned region changes.

Usage:
    python3 tools/validate_profile_generic.py metal_nut
    python3 tools/validate_profile_generic.py hazelnut
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP_DATA = tempfile.mkdtemp(prefix="visionqc_generic_")
os.environ["VISIONQC_DATA_DIR"] = TMP_DATA

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from db import database as db  # noqa: E402
from desktop.model_store import ProfileArtifacts, run_inspection  # noqa: E402
from service.foreground import (  # noqa: E402
    foreground_from_border,
    presence_regions,
    proposal_from_frames,
)
from service.inference import PatchCoreModel  # noqa: E402
from tests.test_mvtec import category_paths, image_auroc  # noqa: E402

MVTEC_ROOT = ROOT / "data" / "mvtec_hf"


def main() -> int:
    category = sys.argv[1] if len(sys.argv) > 1 else "metal_nut"
    train_paths, test_items = category_paths(str(MVTEC_ROOT), category)
    train_paths = train_paths[:30]
    if len(train_paths) < 5:
        print(f"Not enough training images for {category}")
        return 1

    db.init_db()
    good_frames = [cv2.imread(p) for p in train_paths]

    proposal = proposal_from_frames(good_frames, background=None)
    fg_masks = [foreground_from_border(f) for f in good_frames]
    regions = presence_regions(fg_masks, min_fraction=0.8,
                               min_area_fraction=0.005)

    print(f"=== {category}: profile proposal (no code changes) ===")
    print(f"train images: {len(train_paths)} | mask area: {proposal.mean():.1%}"
          f" | presence regions: {regions.mean():.2%}")

    model = PatchCoreModel()
    stats = model.fit(train_paths, score_mask=proposal)
    model_path = Path(TMP_DATA) / "models" / f"{stats['model_version']}.pt"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(model_path))
    db.save_model_metadata(
        version=stats["model_version"], product_id="default",
        created_at=model.created_at, n_images=stats["n_training_images"],
        backbone="WideResNet-50", coreset_ratio=model.coreset_ratio,
        ref_score=stats["ref_score"],
        baseline_brightness=model.training_stats.get("mean_brightness", 0),
        baseline_blur=model.training_stats.get("median_blur", 0),
        parent_version=None, model_path=str(model_path),
    )
    db.update_settings("default", "active_model_version",
                       stats["model_version"])

    art_dir = Path(TMP_DATA) / "profiles" / category
    art_dir.mkdir(parents=True, exist_ok=True)
    mask_path = art_dir / "canonical_mask.png"
    regions_path = art_dir / "presence_regions.png"
    cv2.imwrite(str(mask_path), proposal.astype(np.uint8) * 255)
    cv2.imwrite(str(regions_path), regions.astype(np.uint8) * 255)
    db.save_profile({
        "id": category, "product_id": "default", "name": category,
        "canonical_mask_path": str(mask_path),
        "presence_regions_path": str(regions_path),
        "coverage_floor": 0.6, "threshold": 0.50, "delta": 0.05,
        "model_version": stats["model_version"], "ref_score": stats["ref_score"],
        "status": "active", "metrics": {"mask_area_fraction": float(proposal.mean())},
    })
    db.activate_profile(category)

    artifacts = ProfileArtifacts(db.get_profile(category))

    print(f"model {stats['model_version']} | bank {stats['memory_bank_size']} "
          f"| ref {stats['ref_score']:.2f}")

    masked_good, masked_bad = [], []
    unmasked_good, unmasked_bad = [], []
    verdicts = {"PASS": 0, "REVIEW": 0, "FAIL": 0}
    for item in test_items:
        image = cv2.imread(item["path"])
        if image is None:
            continue
        masked = model.predict(image, score_mask=artifacts.mask)
        unmasked = model.predict(image)
        (masked_bad if item["defective"] else masked_good).append(
            masked["normalized_score"]
        )
        (unmasked_bad if item["defective"] else unmasked_good).append(
            unmasked["normalized_score"]
        )
        result = run_inspection(image, model, db.get_settings(), artifacts)
        verdicts[result["verdict"]] += 1

    auroc_masked = image_auroc(masked_good, masked_bad)
    auroc_unmasked = image_auroc(unmasked_good, unmasked_bad)
    good_scores = np.asarray(masked_good)
    bad_scores = np.asarray(masked_bad)
    print(f"\ntest units: {len(test_items)} "
          f"({len(masked_bad)} defective, {len(masked_good)} good)")
    print(f"AUROC masked   (profile region): {auroc_masked:.3f}")
    print(f"AUROC unmasked (full frame):     {auroc_unmasked:.3f}")
    print(f"good scores: p50={np.percentile(good_scores, 50):.2f} "
          f"p90={np.percentile(good_scores, 90):.2f} "
          f"max={good_scores.max():.2f}")
    print(f"bad  scores: p10={np.percentile(bad_scores, 10):.2f} "
          f"p50={np.percentile(bad_scores, 50):.2f}")
    print(f"verdicts: {verdicts}")
    print("Note: 30 training images with a border-fallback mask; this is a "
          "zero-code-change onboarding smoke test, not a tuned benchmark.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
