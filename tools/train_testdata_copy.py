"""Train a VisionQC profile on the bottle photos in a test data folder.

The dataset is a folder of good-unit photos (a Pepsi bottle on a wooden
table). A ``back/`` subfolder may hold empty-table shots — the model is
trained good-only, exactly like the desktop wizard; the background frames
are held out and used only to check that they score as anomalies.

The trained profile is saved to ``data/models/`` and registered in the
local database so it shows up in the desktop app's model list. Pass
``--activate`` to also make it the active profile.

Usage:
    python3 tools/train_testdata_copy.py
    python3 tools/train_testdata_copy.py --data "/path/to/folder"
    python3 tools/train_testdata_copy.py --activate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paths  # noqa: E402

# Pin torch/Hugging Face to the locally staged weights (offline, no downloads).
paths.ensure_torch_home()
paths.ensure_model_home()

from db import database as db  # noqa: E402
from service.backbones import engine_display_name, resolve_default_engine  # noqa: E402
from service.components import (  # noqa: E402
    calibrate_component_check,
    evaluate_component_check,
)
from service.inference import (  # noqa: E402
    DEFAULT_THRESHOLD,
    PatchCoreModel,
    compute_verdict,
    load_image_bgr,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_DATA = Path.home() / "Downloads" / "testdata copy"


def collect_images(folder: Path) -> list[Path]:
    """Image files directly inside ``folder`` (no recursion), sorted."""
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def evaluate(model: PatchCoreModel, paths_list: list[Path]) -> list[dict]:
    rows = []
    for path in paths_list:
        image = load_image_bgr(str(path))
        prediction = model.predict(image)
        score = prediction["normalized_score"]
        verdict = compute_verdict(score, DEFAULT_THRESHOLD)

        severity = None
        check = getattr(model, "component_check", None)
        if check:
            severity = evaluate_component_check(image, check)["severity"]
            if severity == "FAIL":
                verdict = "FAIL"
            elif severity == "REVIEW" and verdict == "PASS":
                verdict = "REVIEW"

        rows.append({
            "file": path.name,
            "score": score,
            "component": severity or "-",
            "verdict": verdict,
        })
    return rows


def print_rows(title: str, rows: list[dict]) -> None:
    print(f"\n{title}")
    for row in rows:
        print(f"  {row['score']:.3f}  {row['component']:<6}  "
              f"{row['verdict']:<6}  {row['file']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA,
                        help=f"dataset folder (default: {DEFAULT_DATA})")
    parser.add_argument("--activate", action="store_true",
                        help="set the new profile as the active model")
    args = parser.parse_args()

    data = args.data.expanduser().resolve()
    if not data.is_dir():
        parser.error(f"dataset folder not found: {data}")

    good = collect_images(data)
    background = collect_images(data / "back") if (data / "back").is_dir() else []

    print(f"Dataset:    {data}")
    print(f"Good:       {len(good)} images (training set)")
    print(f"Background: {len(background)} images (evaluation only)")

    if not good:
        parser.error(f"no images found in {data}")

    print("\nTraining PatchCore profile (good-only)...")
    model = PatchCoreModel()
    stats = model.fit([str(p) for p in good])
    print(f"  version:          {stats['model_version']}")
    print(f"  calibration:      {stats['calibration']} ({stats['n_holdout']} holdouts)")
    print(f"  ref_score:        {stats['ref_score']:.4f}")
    print(f"  memory bank:      {stats['memory_bank_size']} points")

    model_dir = ROOT / "data" / "models"
    model_path = model_dir / f"{model.model_version}.pt"
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
        parent_version=model.parent_version,
        model_path=str(model_path),
        name="testdata copy (bottle)",
    )
    print(f"\nSaved profile: {model_path}")

    if args.activate:
        db.update_settings("default", "active_model_version", model.model_version)
        print("Activated as the active model.")

    # Round-trip: reload from disk and confirm it predicts identically.
    reloaded = PatchCoreModel().load(str(model_path))
    sample = load_image_bgr(str(good[0]))
    before = model.predict(sample)["normalized_score"]
    after = reloaded.predict(sample)["normalized_score"]
    print(f"Reload check: {before:.4f} -> {after:.4f} "
          f"({'OK' if abs(before - after) < 1e-6 else 'MISMATCH'})")

    good_rows = evaluate(reloaded, good)
    back_rows = evaluate(reloaded, background)

    print_rows(f"Good images (threshold {DEFAULT_THRESHOLD}, expect PASS):",
               good_rows)
    print_rows("Background images (held out, expect REVIEW/FAIL):", back_rows)

    good_pass = sum(r["verdict"] == "PASS" for r in good_rows)
    back_flagged = sum(r["verdict"] != "PASS" for r in back_rows)
    print(f"\nSummary: {good_pass}/{len(good_rows)} good PASS, "
          f"{back_flagged}/{len(back_rows)} background flagged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
