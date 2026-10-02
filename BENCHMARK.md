# VisionQC — MVTec AD Benchmark Results

Benchmark of the VisionQC anomaly detector against the MVTec AD dataset
(15 categories, official test splits). MVTec AD is the standard industrial
anomaly-detection benchmark: every category ships defect-free training images
and a test set of good + defective units with pixel-level defect masks.

**Dataset source:** `Voxel51/mvtec-ad` mirror on HuggingFace (see
`tests/fetch_mvtec_hf.py`). MVTec AD is **free for non-commercial use only**
(CC BY-NC-SA 4.0); this project is a hackathon prototype.

---

## Configuration

| Setting | Value |
| --- | --- |
| Backbone | WideResNet-50 (ImageNet pretrained), frozen |
| Features | layer2 (512ch) + layer3 (1024ch) upsampled to layer2 resolution, 3x3 avg-pool aggregation |
| Input size | 320 x 320 |
| Patch grid | 40 x 40 = 1600 patches/image |
| Memory bank | greedy coreset, ≤60,000 random candidates → up to 2,000 points |
| Reference score | max patch distance over 20% held-out good images |
| Normalisation | `s = clip(0.5 * raw / ref, 0, 1)` |
| Threshold / band | T = 0.46, delta = 0.05 (tuned, see below) |
| Training images | full official train split per category (209–320 images) |
| Device | CPU (Apple Silicon, PyTorch 2.x) |

T was tuned on the MVTec test set per the PRD's rule ("defaults are confirmed
or adjusted on the test set and then fixed"). Because that is mildly
optimistic, a leave-one-category-out (LOCO) cross-validation is also reported:
the threshold is chosen on 14 categories and evaluated on the 15th.

## Headline results (T = 0.46, delta = 0.05)

| Metric | Result | PRD target |
| --- | --- | --- |
| Mean image-level AUROC | **0.976** | ≥ 0.95 |
| Defect recall (FAIL or REVIEW), tuned T | **90.5%** (1139/1258) | ≥ 90% |
| Defect recall, LOCO cross-validated | **89.4%** (1125/1258) | ≥ 90% |
| False-reject rate (good units FAIL), tuned T | **0.9%** (4/467) | < 10% |
| False-reject rate, LOCO | **1.1%** (5/467) | < 10% |
| Pixel-level AUROC (mean over defective units) | 0.91–0.997 per category | — |
| Heatmap localisation (top 5% region overlaps mask) | 97–100% per category | ≥ 80% |
| Inference latency (320px, CPU) | median 100 ms, p95 ≤ 195 ms | median < 500 ms |
| Training time per category (full split) | 50–66 s | < 2 min for 30 images |

### Per-category results

| Category | AUROC | Recall @T=0.46 | False rejects | Pixel AUROC | Localisation | Latency p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bottle | 1.000 | 63/63 (100%) | 0/20 | 0.990 | 63/63 (100%) | 106 ms |
| cable | 0.977 | 89/92 (97%) | 0/58 | 0.972 | 92/92 (100%) | 195 ms |
| capsule | 0.993 | 78/109 (72%) | 0/23 | 0.990 | 109/109 (100%) | 104 ms |
| carpet | 0.965 | 87/89 (98%) | 0/28 | 0.986 | 88/89 (99%) | 104 ms |
| grid | 0.979 | 55/57 (96%) | 0/21 | 0.988 | 57/57 (100%) | 104 ms |
| hazelnut | 1.000 | 70/70 (100%) | 0/40 | 0.988 | 70/70 (100%) | 103 ms |
| leather | 1.000 | 92/92 (100%) | 0/32 | 0.997 | 92/92 (100%) | 104 ms |
| metal_nut | 1.000 | 93/93 (100%) | 0/22 | 0.980 | 93/93 (100%) | 105 ms |
| pill | 0.971 | 132/141 (94%) | 0/26 | 0.979 | 141/141 (100%) | 106 ms |
| screw | 0.924 | 54/119 (45%) | 0/41 | 0.983 | 119/119 (100%) | 106 ms |
| tile | 0.998 | 84/84 (100%) | 0/33 | 0.976 | 84/84 (100%) | 103 ms |
| toothbrush | 0.883 | 30/30 (100%) | 3/12 | 0.985 | 30/30 (100%) | 105 ms |
| transistor | 0.979 | 38/40 (95%) | 0/60 | 0.911 | 40/40 (100%) | 105 ms |
| wood | 0.993 | 60/60 (100%) | 1/19 | 0.964 | 60/60 (100%) | 105 ms |
| zipper | 0.971 | 114/119 (96%) | 0/32 | 0.990 | 119/119 (100%) | 109 ms |
| **Overall** | **0.976** | **1139/1258 (90.5%)** | **4/467 (0.9%)** | — | — | — |

### Threshold behaviour (all categories pooled)

| T | Recall (FAIL+REVIEW) | False rejects (FAIL) |
| ---: | ---: | ---: |
| 0.40 | 98.6% | 4.9% |
| 0.45 | 92.4% | 1.1% |
| **0.46** | **90.5%** | **0.9%** |
| 0.50 | 79.3% | 0.6% |
| 0.55 | 65.3% | 0.6% |
| 0.60 (original PRD default) | 48.6% | 0.4% |
| 0.70 | 25.3% | 0.2% |

## Honest caveats

1. **Screw is the weak category.** AUROC 0.924 but recall only 45% at the
   chosen threshold: tiny thread/head defects overlap the normal score
   distribution. All other categories are ≥ 72% recall, most ≥ 95%.
2. **T was tuned on the test set** (as the PRD's H12 process prescribes).
   The LOCO column is the more honest generalisation estimate: 89.4% recall,
   1.1% false rejects.
3. **Full train splits were used** (209–320 images), more than the product's
   20–30 image onboarding. The product flow was separately validated: a model
   trained on **25 bottle images** caught **12/12 defects with 0/18 false
   rejects** on held-out units.
4. **Latency** was measured on this development machine (Apple Silicon CPU).
   The PRD target (median < 500 ms) has margin, but a slower venue laptop
   should re-run `tests/test_mvtec.py`.

## Reproducing

```bash
# 1. Fetch the dataset (bottle, cable, capsule, ... or --all)
python3 tests/fetch_mvtec_hf.py --all

# 2. Run the benchmark
python3 tests/test_mvtec.py --all --root data/mvtec_hf \
    --json-out /tmp/bench.json --scores-out /tmp/scores.json

# 3. Threshold analysis + LOCO validation
python3 tests/analyze_scores.py /tmp/scores.json
```

Raw run outputs: `/tmp/bench_v2.json` (summary) and `/tmp/scores_v2.json`
(per-image scores).
