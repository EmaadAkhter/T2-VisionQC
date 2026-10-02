"""Seed VisionQC with a trained model and demo inspections from MVTec AD.

Mimics the product flow: trains on 25 good bottle images (product spec says
20-30), inspects 30 held-out test units (18 good, 12 defective), logs them,
and copies a few images into the demo gallery.

Usage:
    python3 tools/seed_demo.py
"""

import os
import sys
import time
import shutil
from pathlib import Path
from datetime import datetime

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from service.inference import (  # noqa: E402
    PatchCoreModel,
    compute_verdict,
    compute_certainty,
    generate_explanation,
    compute_setup_status,
    create_overlay,
)
from db import database as db  # noqa: E402

MVTEC = ROOT / "data" / "mvtec_hf" / "bottle"
MODELS = ROOT / "data" / "models"
IMAGES = ROOT / "data" / "images"
DEMO = ROOT / "data" / "demo"


def main():
    db.init_db()
    for d in (MODELS, IMAGES, DEMO):
        d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Train on 25 good images
    # ------------------------------------------------------------------
    train = sorted((MVTEC / "train" / "good").glob("*.png"))[:25]
    print(f"Training on {len(train)} good images (product flow: 20-30)...")
    model = PatchCoreModel()
    t0 = time.time()
    stats = model.fit([str(p) for p in train])
    print(f"  done in {time.time() - t0:.1f}s | bank={stats['memory_bank_size']} "
          f"| ref={stats['ref_score']:.3f} | holdout={stats['n_holdout']}")

    model_path = MODELS / f"{model.model_version}.pt"
    model.save(str(model_path))

    db.save_model_metadata(
        version=model.model_version,
        product_id="default",
        created_at=model.created_at,
        n_images=model.n_training_images,
        backbone="WideResNet-50",
        coreset_ratio=model.coreset_ratio,
        ref_score=model.ref_score,
        baseline_brightness=model.training_stats.get("mean_brightness", 0),
        baseline_blur=model.training_stats.get("median_blur", 0),
        parent_version=None,
        model_path=str(model_path),
    )
    db.update_settings("default", "active_model_version", model.model_version)

    settings = db.get_settings()
    T = settings["threshold"]
    D = settings["delta"]
    print(f"Active model: {model.model_version} | T={T} | delta={D}")

    # ------------------------------------------------------------------
    # 2. Inspect 18 good + 12 defective test images
    # ------------------------------------------------------------------
    good = sorted((MVTEC / "test" / "good").glob("*.png"))[:18]
    defects = []
    for dt in ["broken_large", "broken_small", "contamination"]:
        defects.extend(sorted((MVTEC / "test" / dt).glob("*.png"))[:4])
    items = [(p, False) for p in good] + [(p, True) for p in defects]

    counts = {"PASS": 0, "REVIEW": 0, "FAIL": 0}
    caught = false_rejects = 0

    for path, is_defect in items:
        image = cv2.imread(str(path))
        pred = model.predict(image)
        score = pred["normalized_score"]
        verdict = compute_verdict(score, T, D)
        setup, _ = compute_setup_status(image, model.training_stats)
        expl = generate_explanation(pred["anomaly_map"], T)
        certainty, _ = compute_certainty(score, verdict, T, D, setup,
                                         expl["area_pct"])
        overlay = create_overlay(image, pred["anomaly_map"], T)

        uid = db.generate_inspection_uid()
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        img_path = IMAGES / f"{uid}_original.png"
        ov_path = IMAGES / f"{uid}_overlay.png"
        cv2.imwrite(str(img_path), image)
        cv2.imwrite(str(ov_path), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))

        row_id = db.log_inspection(
            uid=uid, timestamp=ts, product_id="default",
            model_version=model.model_version,
            raw_score=pred["raw_score"], score=score,
            threshold=T, delta=D, verdict=verdict, certainty=certainty,
            explanation=expl["explanation"], region_label=expl["region_label"],
            area_pct=expl["area_pct"], setup_status=setup,
            image_path=str(img_path), overlay_path=str(ov_path),
            latency_ms=0, demo=0,
        )
        # FR-11.4: final disposition for PASS/FAIL equals the system verdict;
        # REVIEW stays PENDING until an operator resolves it.
        if verdict in ("PASS", "FAIL"):
            db.update_disposition(row_id, verdict)

        counts[verdict] += 1
        if is_defect and verdict in ("FAIL", "REVIEW"):
            caught += 1
        if not is_defect and verdict == "FAIL":
            false_rejects += 1

    print(f"Verdicts: {counts}")
    print(f"Defects caught (FAIL+REVIEW): {caught}/{len(defects)} | "
          f"False rejects: {false_rejects}/{len(good)}")

    # ------------------------------------------------------------------
    # 3. Demo gallery
    # ------------------------------------------------------------------
    gallery = {
        "good_01.png": sorted((MVTEC / "test" / "good").glob("*.png"))[0],
        "good_02.png": sorted((MVTEC / "test" / "good").glob("*.png"))[1],
        "defect_broken_large.png":
            sorted((MVTEC / "test" / "broken_large").glob("*.png"))[0],
        "defect_broken_small.png":
            sorted((MVTEC / "test" / "broken_small").glob("*.png"))[0],
        "defect_contamination.png":
            sorted((MVTEC / "test" / "contamination").glob("*.png"))[0],
    }
    for name, src in gallery.items():
        shutil.copyfile(src, DEMO / name)
    print(f"Demo gallery: {len(gallery)} images -> {DEMO}")

    # Dashboard stats for today
    stats_today = db.get_dashboard_stats()
    print(f"Dashboard today: {stats_today}")


if __name__ == "__main__":
    main()
