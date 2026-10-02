#!/usr/bin/env python3
"""Plot and summarise the POC stability sweep (see tools/poc_stability.py).

Reads results/poc_stability/raw.json, writes:
- results/poc_stability/poc_stability_summary.png (six-panel figure)
- results/poc_stability/summary.csv (per image x config aggregates)
- a plain-text table to stdout
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW = ROOT / "results" / "poc_stability" / "raw.json"

VERDICT_COLORS = {"PASS": "#3f8f5f", "REVIEW": "#c99700", "FAIL": "#b3402f"}
COND_COLORS = {
    "good": "#9aa0a6",
    "cap_missing": "#b3402f",
    "sticker_missing": "#3b6ea5",
    "cap_and_sticker_missing": "#7a3b8f",
}


def compute_verdict(score: float, threshold: float, delta: float = 0.05) -> str:
    if score < threshold - delta:
        return "PASS"
    if score < threshold + delta:
        return "REVIEW"
    return "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="raw", default=str(DEFAULT_RAW))
    parser.add_argument("--out", dest="png", default=None)
    parser.add_argument("--csv", dest="csv_out", default=None)
    args = parser.parse_args()

    raw_path = Path(args.raw)
    stem = raw_path.stem
    out_dir = raw_path.parent
    PNG = Path(args.png) if args.png else out_dir / f"{stem}_summary.png"
    CSV = Path(args.csv_out) if args.csv_out else out_dir / f"{stem}_summary.csv"

    data = json.loads(raw_path.read_text())
    meta = data["meta"]
    runs = data["runs"]

    manifest = json.loads((ROOT / "data" / "poc" / "manifest.json").read_text())
    entries = manifest["images"]
    conditions = {e["id"]: e["condition"] for e in entries}
    image_ids = [e["id"] for e in entries]
    defective = [i for i in image_ids if conditions[i] != "good"]
    good = [i for i in image_ids if conditions[i] == "good"]
    threshold, delta = meta["threshold"], meta["delta"]

    def subset(mode, config=None):
        return [r for r in runs if r["mode"] == mode
                and (config is None or r["config"] == config)]

    fig, axes = plt.subplots(3, 2, figsize=(16, 13), constrained_layout=True)
    fig.suptitle(
        f"POC stability sweep - {len(runs)} runs | engine {meta['engine']} | "
        f"eval {meta['primary_device']} | threshold {threshold} +/- {delta}",
        fontsize=13,
    )

    # -- Panel 1: per-image score distribution (order mode, pooled configs) ----
    ax = axes[0, 0]
    order_runs = subset("order")
    scores = {i: [r["images"][i]["score"] for r in order_runs] for i in image_ids}
    positions = np.arange(len(image_ids))
    bp = ax.boxplot([scores[i] for i in image_ids], positions=positions,
                    widths=0.55, patch_artist=True, showfliers=False,
                    medianprops=dict(color="black"))
    for patch, image_id in zip(bp["boxes"], image_ids):
        patch.set_facecolor(COND_COLORS[conditions[image_id]])
        patch.set_alpha(0.55)
    for pos, image_id in zip(positions, image_ids):
        spread = np.random.default_rng(0).normal(pos, 0.05, len(scores[image_id]))
        ax.scatter(spread, scores[image_id], s=4, color="#222", alpha=0.25)
    ax.axhline(threshold, color="#555", ls="--", lw=1,
               label=f"threshold {threshold:.2f}")
    ax.axhspan(threshold - delta, threshold + delta, color="#c99700", alpha=0.10,
               label=f"review band {threshold - delta:.2f}-{threshold + delta:.2f}")
    ax.axhline(0.50, color="#999", ls=":", lw=1, label="worst-good line 0.50")
    ax.set_xticks(positions)
    ax.set_xticklabels([i.replace("image_", "") for i in image_ids])
    ax.set_ylim(0.20, 0.70)
    ax.set_title("Per-image score across order perturbations (100 runs)")
    ax.set_ylabel("calibrated score")
    ax.legend(fontsize=8, loc="upper left")

    # -- Panel 2: verdict composition per image, harness vs app ---------------
    ax = axes[0, 1]
    width = 0.38
    for k, (config_name, offset) in enumerate([("harness", -width / 2),
                                               ("app", width / 2)]):
        config_runs = subset("order", config_name)
        for pos, image_id in zip(positions, image_ids):
            counts = Counter(r["images"][image_id]["verdict"]
                             for r in config_runs)
            bottom = 0.0
            for verdict_name in ("PASS", "REVIEW", "FAIL"):
                value = counts.get(verdict_name, 0) / len(config_runs)
                if value <= 0:
                    continue
                ax.bar(pos + offset, value, width, bottom=bottom,
                       color=VERDICT_COLORS[verdict_name],
                       edgecolor="white", linewidth=0.4)
                if value > 0.12:
                    ax.text(pos + offset, bottom + value / 2,
                            verdict_name[0], ha="center", va="center",
                            fontsize=8, color="white", fontweight="bold")
                bottom += value
    ax.set_xticks(positions)
    ax.set_xticklabels([i.replace("image_", "") for i in image_ids])
    ax.set_ylim(0, 1.02)
    ax.set_title("Verdict frequency per image (left bar harness, right app)")
    ax.set_ylabel("fraction of 100 runs")
    handles = [plt.Rectangle((0, 0), 1, 1, color=VERDICT_COLORS[v])
               for v in ("PASS", "REVIEW", "FAIL")]
    ax.legend(handles, ["PASS", "REVIEW", "FAIL"], fontsize=8, loc="lower right")

    # -- Panel 3: score-only threshold sensitivity ----------------------------
    ax = axes[1, 0]
    thresholds = np.arange(0.30, 0.71, 0.01)
    caught_curve, good_pass_curve = [], []
    for t in thresholds:
        caught = []
        goods_pass = []
        for r in order_runs:
            verdicts = {i: compute_verdict(r["images"][i]["score"], t, delta)
                        for i in image_ids}
            caught.append(sum(verdicts[i] != "PASS" for i in defective))
            goods_pass.append(np.mean([verdicts[i] == "PASS" for i in good]))
        caught_curve.append(np.mean(caught))
        good_pass_curve.append(np.mean(goods_pass) * 100)
    ax.plot(thresholds, caught_curve, color="#b3402f", lw=2,
            label="defects flagged (of 3)")
    ax.set_ylim(0, 3.2)
    ax.set_ylabel("defects flagged", color="#b3402f")
    ax.set_xlabel("threshold")
    ax2 = ax.twinx()
    ax2.plot(thresholds, good_pass_curve, color="#3f8f5f", lw=2, ls="--",
             label="good units passing (%, score-only)")
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("good units passing (%)", color="#3f8f5f")
    for t, style in [(0.46, ":"), (0.50, "-"), (0.55, "--")]:
        ax.axvline(t, color="#777", ls=style, lw=1)
        ax.text(t, 3.05, f"{t:.2f}", fontsize=7, ha="center", color="#555")
    ax.set_title("Threshold sensitivity (score-only verdicts, no region check)")

    # -- Panel 4: region coverage vs floors -----------------------------------
    ax = axes[1, 1]
    harness_order = subset("order", "harness") or subset("order")
    coverage = {i: [r["images"][i]["region_coverage"] for r in harness_order]
                for i in image_ids}
    bp = ax.boxplot([coverage[i] for i in image_ids], positions=positions,
                    widths=0.55, patch_artist=True, showfliers=False,
                    medianprops=dict(color="black"))
    for patch, image_id in zip(bp["boxes"], image_ids):
        patch.set_facecolor(COND_COLORS[conditions[image_id]])
        patch.set_alpha(0.55)
    ax.axhline(0.9, color="#3b6ea5", ls="--", lw=1,
               label="app coverage floor 0.9")
    ax.axhline(0.6, color="#b3402f", ls="--", lw=1,
               label="harness coverage floor 0.6")
    for pos, image_id in zip(positions, image_ids):
        ax.text(pos, 1.02, f"{np.mean([r['regions_area'] for r in harness_order]):.1%}",
                ha="center", fontsize=6, color="#666")
    ax.set_xticks(positions)
    ax.set_xticklabels([i.replace("image_", "") for i in image_ids])
    ax.set_ylim(0, 1.1)
    ax.set_title("Presence-region coverage per image (harness regions)")
    ax.set_ylabel("region pixels covered by foreground")
    ax.legend(fontsize=7, loc="center left")
    app_order = subset("order", "app")
    harness_area = np.mean([r["regions_area"] for r in harness_order])
    app_area = np.mean([r["regions_area"] for r in app_order]) if app_order else 0.0
    empty_share = np.mean([r["regions_area"] == 0 for r in harness_order])
    ax.text(0.99, 0.02,
            f"mean regions area: harness {harness_area:.2%}, app {app_area:.2%}\n"
            f"harness runs with empty regions: {empty_share:.0%}",
            transform=ax.transAxes, fontsize=7, color="#555",
            ha="right", va="bottom")

    # -- Panel 5: defects caught per run --------------------------------------
    ax = axes[2, 0]
    for config_name, color in [("harness", "#b3402f"), ("app", "#3b6ea5")]:
        config_runs = subset("order", config_name)
        caught = [sum(r["images"][i]["verdict"] != "PASS" for i in defective)
                  for r in config_runs]
        ax.plot(range(len(caught)), caught, color=color, alpha=0.35, lw=0.8)
        ax.axhline(np.mean(caught), color=color, lw=2,
                   label=f"{config_name}: mean {np.mean(caught):.2f}/3")
    ax.set_ylim(-0.2, 3.3)
    ax.set_yticks([0, 1, 2, 3])
    ax.set_xlabel("run index (order perturbations)")
    ax.set_ylabel("defects flagged")
    ax.set_title("Defects flagged per run (order mode)")
    ax.legend(fontsize=8, loc="lower left")

    # -- Panel 6: device parity (MPS vs CPU) ----------------------------------
    ax = axes[2, 1]
    device_runs = subset("device")
    if not device_runs:
        ax.axis("off")
        ax.set_title("Device parity - no device-mode runs in this dataset")
    else:
        deltas = {i: [r["images"][i]["score"] - r["cpu_images"][i]["score"]
                      for r in device_runs] for i in image_ids}
        mismatches = sum(
            1 for r in device_runs for i in image_ids
            if r["images"][i]["verdict"] != r["cpu_images"][i]["verdict"])
        bp = ax.boxplot([deltas[i] for i in image_ids], positions=positions,
                        widths=0.55, patch_artist=True, showfliers=False,
                        medianprops=dict(color="black"))
        for patch, image_id in zip(bp["boxes"], image_ids):
            patch.set_facecolor(COND_COLORS[conditions[image_id]])
            patch.set_alpha(0.55)
        max_abs = max(abs(d) for ds in deltas.values() for d in ds)
        ax.set_xticks(positions)
        ax.set_xticklabels([i.replace("image_", "") for i in image_ids])
        ax.set_title(f"Score parity {meta['primary_device']} vs CPU "
                     f"(max |delta| {max_abs:.2e}, verdict mismatches {mismatches})")
        ax.set_ylabel("score delta (device - cpu)")
        ax.ticklabel_format(axis="y", style="sci", scilimits=(-3, 3))

    PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(PNG, dpi=150)
    print(f"wrote {PNG}")

    # -- CSV + stdout summary --------------------------------------------------
    rows = []
    print(f"\n{'image':<9} {'condition':<24} {'config':<7} {'mode':<9} "
          f"{'mean':>6} {'std':>6} {'min':>6} {'max':>6} "
          f"{'PASS%':>6} {'REV%':>6} {'FAIL%':>6} {'miss%':>6} {'regcov':>6}")
    for image_id in image_ids:
        for config_name in ("harness", "app"):
            for mode in ("identical", "order", "jitter"):
                mode_runs = subset(mode, config_name)
                if not mode_runs:
                    continue
                values = [r["images"][image_id]["score"] for r in mode_runs]
                verdicts = Counter(r["images"][image_id]["verdict"]
                                   for r in mode_runs)
                missing = np.mean([r["images"][image_id]["missing_fraction"]
                                   for r in mode_runs])
                regcov = np.mean([r["images"][image_id]["region_coverage"]
                                  for r in mode_runs])
                n = len(mode_runs)
                row = {
                    "image": image_id, "condition": conditions[image_id],
                    "config": config_name, "mode": mode, "runs": n,
                    "mean": np.mean(values), "std": np.std(values),
                    "min": np.min(values), "max": np.max(values),
                    "pass_pct": 100 * verdicts.get("PASS", 0) / n,
                    "review_pct": 100 * verdicts.get("REVIEW", 0) / n,
                    "fail_pct": 100 * verdicts.get("FAIL", 0) / n,
                    "mean_missing": missing, "mean_region_coverage": regcov,
                }
                rows.append(row)
                print(f"{image_id:<9} {conditions[image_id]:<24} "
                      f"{config_name:<7} {mode:<9} {row['mean']:>6.3f} "
                      f"{row['std']:>6.3f} {row['min']:>6.3f} {row['max']:>6.3f} "
                      f"{row['pass_pct']:>6.1f} {row['review_pct']:>6.1f} "
                      f"{row['fail_pct']:>6.1f} {missing * 100:>6.1f} "
                      f"{regcov:>6.3f}")
    with CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {CSV}")

    # Run-level summary
    print(f"\n{'mode':<9} {'config':<7} {'runs':>4} {'caught mean':>11} "
          f"{'caught range':>12} {'good failed':>11} {'good flagged':>12} "
          f"{'regions empty':>13}")
    for mode in ("identical", "order", "jitter"):
        for config_name in ("harness", "app"):
            mode_runs = subset(mode, config_name)
            if not mode_runs:
                continue
            caught = [sum(r["images"][i]["verdict"] != "PASS" for i in defective)
                      for r in mode_runs]
            failed = [sum(r["images"][i]["verdict"] == "FAIL" for i in good)
                      for r in mode_runs]
            flagged = [sum(r["images"][i]["verdict"] != "PASS" for i in good)
                       for r in mode_runs]
            empty = np.mean([r["regions_area"] == 0 for r in mode_runs])
            print(f"{mode:<9} {config_name:<7} {len(mode_runs):>4} "
                  f"{np.mean(caught):>11.2f} "
                  f"{min(caught)}-{max(caught):<10} {np.mean(failed):>11.2f} "
                  f"{np.mean(flagged):>12.2f} {empty * 100:>12.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
