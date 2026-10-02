#!/usr/bin/env python3
"""POC stability sweep: run the profile pipeline many times under controlled
perturbations and record every score/verdict.

Modes
-----
identical : repeat the exact pipeline (determinism check)
order     : shuffle the good-image order per run. The greedy coreset starts
            from the first point, so ordering changes the memory bank.
jitter    : randomize the proposal / region parameters (tests how much the
            hand-tuned knobs matter), order held fixed
device    : one fit, evaluated on CPU and on the auto device (MPS/CUDA/CPU);
            isolates device numerics from training variability

Configs
-------
harness : parameters used by tools/validate_poc8.py
          (regions 0.8 / 0.005, coverage_floor 0.6)
app     : parameters used by the desktop Profiles wizard
          (propose_mask_job + build_profile_job: regions 0.95 / 0.01,
          coverage_floor 0.9)

Output: results/poc_stability/raw.json  (plot with poc_stability_plot.py)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

from desktop.model_store import ProfileArtifacts, run_inspection  # noqa: E402
from service.backbones import resolve_default_engine  # noqa: E402
from service.foreground import (  # noqa: E402
    foreground_from_border,
    presence_regions,
    proposal_from_frames,
    propose_presence_regions,
    region_coverage,
)
from service.inference import PatchCoreModel, auto_device  # noqa: E402

POC = ROOT / "data" / "poc"
DEFAULT_OUT = ROOT / "results" / "poc_stability" / "raw.json"

THRESHOLD = 0.50
DELTA = 0.05

BASE_CONFIGS = {
    # Historical relaxed parameters (pre-fix validate_poc8 --tuned)
    "harness": {
        "coverage_fraction": 0.5,
        "border_tolerance": 35.0,
        "regions_min_fraction": 0.8,
        "regions_min_area": 0.005,
        "coverage_floor": 0.6,
        "adaptive_regions": False,
    },
    # Mirrors desktop/ui/profiles.py (propose_mask_job + build_profile_job)
    "app": {
        "coverage_fraction": 0.5,
        "border_tolerance": 35.0,
        "regions_min_fraction": 0.95,
        "regions_min_area": 0.01,
        "coverage_floor": None,   # derived from the used fill fraction
        "adaptive_regions": True,
    },
}


# ---------------------------------------------------------------------------
# Feature cache
# ---------------------------------------------------------------------------

def patch_feature_cache(model: PatchCoreModel) -> None:
    """Memoise patch features per image (keyed by content hash + device).

    Without this, every sweep run re-runs the backbone over the same images.
    Missing images are still extracted in one batch, so the numbers match the
    uncached pipeline.
    """
    real_extract = model.extract_features
    cache: dict[tuple[str, str], torch.Tensor] = {}

    def cached(images: torch.Tensor) -> torch.Tensor:
        device = str(images.device)
        keys = []
        for i in range(images.shape[0]):
            arr = images[i:i + 1].detach().cpu().numpy()
            digest = hashlib.md5(arr.tobytes()).hexdigest()
            keys.append((device, digest))
        missing = [i for i, key in enumerate(keys) if key not in cache]
        if missing:
            feats = real_extract(images[missing])
            for j, i in enumerate(missing):
                cache[keys[i]] = feats[j:j + 1].detach()
        return torch.cat([cache[key] for key in keys], dim=0)

    model.extract_features = cached  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# One pipeline run
# ---------------------------------------------------------------------------

def build_masks(good_frames, params):
    proposal = proposal_from_frames(
        good_frames, background=None,
        border_tolerance=params["border_tolerance"],
        coverage_fraction=params["coverage_fraction"],
    )
    fg_masks = [foreground_from_border(f, params["border_tolerance"])
                for f in good_frames]
    if params.get("adaptive_regions", False):
        regions, used = propose_presence_regions(
            fg_masks,
            approved_mask=proposal,
            min_fraction=params["regions_min_fraction"],
            min_area_fraction=params["regions_min_area"],
        )
    else:
        regions = presence_regions(
            fg_masks,
            min_fraction=params["regions_min_fraction"],
            min_area_fraction=params["regions_min_area"],
        )
        used = params["regions_min_fraction"]
    coverage_floor = params["coverage_floor"]
    if coverage_floor is None:
        coverage_floor = (0.9 if used is None
                          else max(0.5, round(used - 0.2, 2)))
    return proposal, regions, coverage_floor


def fit_run(model, good_paths, good_frames, params, art_dir):
    """Build profile artifacts and fit the model (on CPU, like the app)."""
    proposal, regions, coverage_floor = build_masks(good_frames, params)
    model.to("cpu")
    stats = model.fit([str(p) for p in good_paths], score_mask=proposal)

    mask_path = art_dir / "canonical_mask.png"
    regions_path = art_dir / "presence_regions.png"
    cv2.imwrite(str(mask_path), proposal.astype(np.uint8) * 255)
    cv2.imwrite(str(regions_path), regions.astype(np.uint8) * 255)
    artifacts = ProfileArtifacts({
        "id": "sweep",
        "canonical_mask_path": str(mask_path),
        "presence_regions_path": str(regions_path),
        "background_model_path": None,
        "coverage_floor": coverage_floor,
        "threshold": THRESHOLD,
        "delta": DELTA,
    })
    return stats, artifacts, regions, proposal


def evaluate(model, artifacts, regions, entries, images, eval_device):
    """Run every image through the full inspection pipeline on one device."""
    model.to(eval_device)
    results = {}
    for entry in entries:
        image = images[entry["id"]]
        result = run_inspection(image, model, {}, artifacts)
        foreground = foreground_from_border(image)
        coverage = (region_coverage(foreground, regions)
                    if regions.sum() > 0 else 1.0)
        results[entry["id"]] = {
            "score": round(float(result["score"]), 6),
            "raw": round(float(result["raw_score"]), 6),
            "verdict": result["verdict"],
            "missing_fraction": round(float(result["missing_fraction"]), 6),
            "region_coverage": round(float(coverage), 6),
            "no_product": bool(result["no_product"]),
        }
    return results


# ---------------------------------------------------------------------------
# Sweep driver
# ---------------------------------------------------------------------------

def build_schedule(modes, configs, runs, rng, n_good):
    schedule = []
    for mode in modes:
        n = 10 if mode == "device" else runs
        for config_name in configs:
            base = BASE_CONFIGS[config_name]
            for i in range(n):
                params = dict(base)
                order = list(range(n_good))
                if mode == "order":
                    rng.shuffle(order)
                elif mode == "jitter":
                    params.update(
                        coverage_fraction=rng.uniform(0.35, 0.65),
                        border_tolerance=rng.uniform(25.0, 45.0),
                        regions_min_fraction=rng.uniform(0.70, 0.95),
                        regions_min_area=rng.uniform(0.003, 0.02),
                        coverage_floor=rng.uniform(0.5, 0.9),
                    )
                schedule.append((mode, config_name, i, order, params))
    return schedule


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--modes", default="identical,order,jitter,device")
    parser.add_argument("--configs", default="harness,app")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    for mode in modes:
        if mode not in ("identical", "order", "jitter", "device"):
            raise SystemExit(f"unknown mode: {mode}")
    for config_name in configs:
        if config_name not in BASE_CONFIGS:
            raise SystemExit(f"unknown config: {config_name}")

    entries = json.loads((POC / "manifest.json").read_text())["images"]
    good_entries = [e for e in entries if e["condition"] == "good"]
    good_paths = [POC / e["file"] for e in good_entries]
    good_frames = [cv2.imread(str(p)) for p in good_paths]
    images = {e["id"]: cv2.imread(str(POC / e["file"])) for e in entries}

    backbone, kwargs = resolve_default_engine(len(good_paths))
    model = PatchCoreModel(backbone=backbone, backbone_kwargs=kwargs)
    patch_feature_cache(model)
    primary_device = auto_device()

    rng = random.Random(args.seed)
    schedule = build_schedule(modes, configs, args.runs, rng, len(good_paths))
    print(f"engine {backbone} | eval {primary_device} | "
          f"{len(schedule)} runs | modes {modes} | configs {configs}")

    records = []
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="visionqc_sweep_") as tmp:
        art_dir = Path(tmp)
        for index, (mode, config_name, run_id, order, params) in enumerate(schedule):
            paths = [good_paths[j] for j in order]
            frames = [good_frames[j] for j in order]
            run_started = time.time()
            stats, artifacts, regions, proposal = fit_run(
                model, paths, frames, params, art_dir)
            record = {
                "mode": mode,
                "config": config_name,
                "run": run_id,
                "order": order,
                "ref_score": round(float(stats["ref_score"]), 6),
                "bank": int(stats["memory_bank_size"]),
                "calibration": stats["calibration"],
                "mask_area": round(float(proposal.mean()), 6),
                "regions_area": round(float(regions.mean()), 6),
                "params": {k: (round(v, 4) if isinstance(v, float) else v)
                           for k, v in params.items()},
                "images": evaluate(model, artifacts, regions, entries, images,
                                   primary_device),
                "ms": int((time.time() - run_started) * 1000),
            }
            if mode == "device":
                record["cpu_images"] = evaluate(
                    model, artifacts, regions, entries, images, "cpu")
            records.append(record)
            if (index + 1) % 25 == 0 or index == len(schedule) - 1:
                elapsed = time.time() - started
                eta = elapsed / (index + 1) * (len(schedule) - index - 1)
                print(f"  {index + 1}/{len(schedule)} runs | "
                      f"{elapsed:.0f}s elapsed | eta {eta:.0f}s", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "meta": {
            "dataset": "data/poc",
            "engine": backbone,
            "primary_device": primary_device,
            "seed": args.seed,
            "threshold": THRESHOLD,
            "delta": DELTA,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "runs": records,
    }))
    print(f"wrote {out} ({len(records)} runs, "
          f"{out.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
