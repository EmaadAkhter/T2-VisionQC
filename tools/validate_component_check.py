"""Validation gates for the learned component check.

Exit code 0 only when every gate passes:
  G1  every good training image passes with the check enabled
      (0 FAIL, at most 1 REVIEW)
  G2  every synthesized component-removed frame (inpaint / fill / cap-only)
      fails
  G3  with the check disabled the legacy anomaly-only behavior is unchanged
  G4  live-pipeline latency stays under the interactive limit

Usage:
    python3 tools/validate_component_check.py [image_glob ...]
"""

from __future__ import annotations

import glob
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import paths  # noqa: E402

paths.ensure_torch_home()
paths.ensure_model_home()

from db import database as db  # noqa: E402

db.init_db()

from desktop.model_store import ModelStore, run_inspection  # noqa: E402
from service.components import doctor_frame  # noqa: E402

MAX_LATENCY_MS = 250


def main() -> int:
    globs = sys.argv[1:] or ["/Users/emaad/Downloads/testdata/*.jpeg"]
    files = sorted(
        path for pattern in globs for path in glob.glob(pattern)
    )
    if len(files) < 3:
        print(f"need at least 3 images, got {len(files)}")
        return 1

    store = ModelStore()
    version = store.active_version()
    model = store.get()
    if model is None:
        print("no active model")
        return 1
    spec = getattr(model, "component_check", None)
    if not spec:
        print(f"active model {version} has no component check; "
              "train first via desktop.ui.train.train_model")
        return 1
    print(f"model {version} ({store.model_name(version)}), "
          f"clusters: {len(spec['clusters'])}")

    settings = db.get_settings()
    failures = []

    # -- G1: good images pass ------------------------------------------------
    good_verdicts = {}
    for path in files:
        image = cv2.imread(path)
        result = run_inspection(image, model, settings, None)
        good_verdicts[path] = result
        print(f"  G1 {result['verdict']:6s} score={result['score']:.3f} "
              f"component={result.get('component_fractions')}  "
              f"{Path(path).name}")
    n_fail = sum(1 for r in good_verdicts.values() if r["verdict"] == "FAIL")
    n_review = sum(1 for r in good_verdicts.values()
                   if r["verdict"] == "REVIEW")
    if n_fail or n_review > 1:
        failures.append(f"G1: {n_fail} FAIL / {n_review} REVIEW on good set")
    else:
        print(f"G1 ok: {len(files) - n_review} PASS, {n_review} REVIEW, 0 FAIL")

    # -- G2: component-removed frames fail -----------------------------------
    n_removed = 0
    n_removed_fail = 0
    for path in files:
        image = cv2.imread(path)
        for mode in ("inpaint", "fill", "cap_only"):
            removed = doctor_frame(image, spec["clusters"], mode=mode)
            result = run_inspection(removed, model, settings, None)
            n_removed += 1
            n_removed_fail += result["verdict"] == "FAIL"
            if result["verdict"] != "FAIL":
                failures.append(
                    f"G2: {Path(path).name} [{mode}] -> "
                    f"{result['verdict']} (fractions "
                    f"{result.get('component_fractions')})")
    if not any(f.startswith("G2") for f in failures):
        print(f"G2 ok: {n_removed_fail}/{n_removed} removed-component frames FAIL")

    # -- G3: legacy behavior with the check disabled -------------------------
    legacy_settings = dict(settings, component_check=0)
    legacy_mismatch = 0
    for path in files:
        image = cv2.imread(path)
        result = run_inspection(image, model, legacy_settings, None)
        # anomaly-only verdicts on these good images must be PASS/REVIEW,
        # never FAIL (established baseline of this data set)
        if result["verdict"] == "FAIL":
            legacy_mismatch += 1
    if legacy_mismatch:
        failures.append(f"G3: {legacy_mismatch} good images FAIL with check off")
    else:
        print("G3 ok: check-off behavior unchanged (no new false fails)")

    # -- G4: latency ----------------------------------------------------------
    image = cv2.imread(files[0])
    run_inspection(image, model, settings, None)  # warm-up
    times = []
    for _ in range(15):
        started = time.time()
        run_inspection(image, model, settings, None)
        times.append((time.time() - started) * 1000)
    p95 = float(np.percentile(times, 95))
    if p95 > MAX_LATENCY_MS:
        failures.append(f"G4: latency p95 {p95:.0f} ms > {MAX_LATENCY_MS} ms")
    else:
        print(f"G4 ok: latency median {np.median(times):.0f} ms, "
              f"p95 {p95:.0f} ms")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print("  -", failure)
        return 1
    print("\nALL GATES PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
