"""Validate the generalized profile pipeline on the eight POC images.

Builds a profile from the five good bottle photos (no background frames; the
border-color fallback proposes the mask), then runs all eight images through
the profile-driven pipeline. Reports verdicts, missing-region fractions and
honest counts. No product-specific logic is used.

By default the run mirrors the desktop wizard exactly: clutter-guarded
presence regions (inside the approved mask, never pinned to the frame border,
with a fill-fraction fallback) and the derived coverage floor. Pass --tuned to
reproduce the historical relaxed parameters (regions at 0.8 fill, floor 0.6,
no clutter guards) that the earlier 3/3 number used.

Usage:
    python3 tools/validate_poc8.py            # app-faithful
    python3 tools/validate_poc8.py --tuned    # historical relaxed params
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP_DATA = tempfile.mkdtemp(prefix="visionqc_poc8_")
os.environ["VISIONQC_DATA_DIR"] = TMP_DATA

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from db import database as db  # noqa: E402
from desktop.model_store import ModelStore, run_inspection  # noqa: E402
from service.foreground import (  # noqa: E402
    background_false_activation,
    foreground_from_border,
    mask_metrics,
    presence_regions,
    proposal_from_frames,
    proposal_quality_warnings,
    propose_presence_regions,
)
from service.backbones import engine_display_name, resolve_default_engine  # noqa: E402
from service.inference import PatchCoreModel  # noqa: E402

POC = ROOT / "data" / "poc"
MANIFEST = json.loads((POC / "manifest.json").read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tuned", action="store_true",
        help="historical relaxed region params (0.8 fill, floor 0.6, no "
             "clutter guards)",
    )
    args = parser.parse_args()

    db.init_db()
    entries = MANIFEST["images"]
    good = [POC / e["file"] for e in entries if e["condition"] == "good"]
    defective = [POC / e["file"] for e in entries if e["condition"] != "good"]
    good_frames = [cv2.imread(str(p)) for p in good]

    # 1. Proposal from good frames only (border fallback: no empty scene).
    proposal = proposal_from_frames(good_frames, background=None)
    fg_masks = [foreground_from_border(f) for f in good_frames]

    if args.tuned:
        regions = presence_regions(fg_masks, min_fraction=0.8,
                                   min_area_fraction=0.005)
        used_fraction = 0.8
        coverage_floor = 0.6
    else:
        regions, used_fraction = propose_presence_regions(
            fg_masks, approved_mask=proposal)
        coverage_floor = (0.9 if used_fraction is None
                          else max(0.5, round(used_fraction - 0.2, 2)))
    warnings = proposal_quality_warnings(fg_masks, proposal, regions,
                                         used_fraction)

    mode = "tuned relaxed params" if args.tuned else "app-faithful"
    print(f"=== Profile proposal [{mode}] ===")
    print(f"good frames: {len(good)} | mask area: {proposal.mean():.1%} | "
          f"presence regions: {regions.mean():.2%} | "
          f"fill: {used_fraction if used_fraction is not None else '-'} | "
          f"floor: {coverage_floor}")
    for warning in warnings:
        print(f"  warning: {warning}")

    # 2. Train with masked reference scoring (current default engine).
    backbone, engine_kwargs = resolve_default_engine(len(good))
    model = PatchCoreModel(backbone=backbone, backbone_kwargs=engine_kwargs)
    stats = model.fit([str(p) for p in good], score_mask=proposal)
    model_path = Path(TMP_DATA) / "models" / f"{stats['model_version']}.pt"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(model_path))
    db.save_model_metadata(
        version=stats["model_version"],
        product_id="default",
        created_at=model.created_at,
        n_images=stats["n_training_images"],
        backbone=engine_display_name(model.backbone.config()["name"]),
        coreset_ratio=model.coreset_ratio,
        ref_score=stats["ref_score"],
        baseline_brightness=model.training_stats.get("mean_brightness", 0),
        baseline_blur=model.training_stats.get("median_blur", 0),
        parent_version=None,
        model_path=str(model_path),
    )
    print(f"model {stats['model_version']} | bank {stats['memory_bank_size']} "
          f"| ref {stats['ref_score']:.2f} | holdout {stats['n_holdout']}")

    # 3. Persist artifacts + profile in the temp data dir.
    art_dir = Path(TMP_DATA) / "profiles" / "poc8"
    art_dir.mkdir(parents=True, exist_ok=True)
    mask_path = art_dir / "canonical_mask.png"
    regions_path = art_dir / "presence_regions.png"
    sample_path = art_dir / "sample.png"
    cv2.imwrite(str(mask_path), proposal.astype(np.uint8) * 255)
    cv2.imwrite(str(regions_path), regions.astype(np.uint8) * 255)
    cv2.imwrite(str(sample_path), good_frames[0])

    profile_id = db.save_profile({
        "id": "poc8",
        "product_id": "default",
        "name": "POC bottle",
        "canonical_mask_path": str(mask_path),
        "presence_regions_path": str(regions_path),
        "coverage_floor": coverage_floor,
        "threshold": 0.50,
        "delta": 0.05,
        "model_version": stats["model_version"],
        "ref_score": stats["ref_score"],
        "status": "active",
        "metrics": {"mask_area_fraction": float(proposal.mean())},
    })
    db.activate_profile(profile_id)
    db.update_settings("default", "active_model_version", stats["model_version"])

    # 4. Run every image through the profile-driven pipeline.
    store = ModelStore()
    artifacts = store.artifacts()
    settings = db.get_settings()

    print("\n=== Per-image results (profile-driven) ===")
    print(f"{'image':<10} {'expected':<26} {'verdict':<10} {'score':>6} "
          f"{'missing':>8} {'no_product':>10}")
    false_fails = 0
    caught = 0
    for entry in entries:
        path = POC / entry["file"]
        image = cv2.imread(str(path))
        result = run_inspection(image, store.get(), settings, artifacts)
        expected = entry["condition"]
        is_defect = expected != "good"
        print(f"{entry['id']:<10} {expected:<26} {result['verdict']:<10} "
              f"{result['score']:>6.2f} {result['missing_fraction']:>7.1%} "
              f"{str(result['no_product']):>10}")
        if is_defect and result["verdict"] in ("REVIEW", "FAIL"):
            caught += 1
        if not is_defect and result["verdict"] == "FAIL":
            false_fails += 1

    print("\n=== Counts ===")
    print(f"defects flagged (REVIEW/FAIL): {caught}/{len(defective)}")
    print(f"good units failed: {false_fails}/{len(good)}")
    print("NOTE: five good images is far below a real onboarding set; these "
          "are smoke counts, not accuracy claims.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
