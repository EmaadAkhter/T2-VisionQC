# VisionQC

Offline visual inspection assistant for small manufacturers. Learns "normal"
from 20-30 good photos using unsupervised anomaly detection (PatchCore) — no
defect dataset needed.

## Features

- **Normal-only training**: Upload 20-30 good unit photos, get a working model
  in seconds
- **Instant inspection**: Capture a unit and get a PASS / REVIEW / FAIL verdict
  in ~100 ms on CPU
- **Explainable results**: Plain-language explanations with defect location,
  area and intensity, plus a heatmap overlay
- **Decision certainty**: High / Medium / Low certainty rating (a heuristic,
  not a probability)
- **Threshold tuning**: Adjustable threshold with an impact preview over recent
  inspections before you apply it
- **Manual review workflow**: Borderline units go to REVIEW; operator records
  Accept/Reject; system verdict is never overwritten
- **Audit trail**: Every inspection logged with score, threshold, verdict,
  explanation, setup status and images
- **Dashboard**: Today's rejection rate, pending reviews, filterable history,
  CSV export
- **Fully offline**: No internet required after setup

## Benchmark: MVTec AD

VisionQC was benchmarked against the standard MVTec AD industrial
anomaly-detection dataset (15 categories, official test splits):

| Metric | Result | PRD target |
| --- | --- | --- |
| Mean image-level AUROC | **0.976** | ≥ 0.95 |
| Defect recall (FAIL or REVIEW) | **90.5%** (1139/1258) | ≥ 90% |
| Recall, leave-one-category-out | 89.4% (1125/1258) | ≥ 90% |
| False rejects (good units FAIL) | **0.9%** (4/467) | < 10% |
| Heatmap localisation | 97-100% per category | ≥ 80% |
| Inference latency (CPU) | median 100 ms | < 500 ms |

Full per-category tables, methodology and caveats: **[BENCHMARK.md](BENCHMARK.md)**

Product-flow check: a model trained on **25 good bottle images** caught
**12/12 defects** with **0/18 false rejects** on held-out units.

> **Dataset licence:** MVTec AD is free for **non-commercial use only**
> (CC BY-NC-SA 4.0). It is used here for a hackathon prototype. The dataset is
> fetched by `tests/fetch_mvtec_hf.py` from the `Voxel51/mvtec-ad` HuggingFace
> mirror and is not redistributed in this repository.

## Real-world bottle segmentation POC

Eight labelled WhatsApp images are preserved in `data/poc/raw/`. Initial
tests compare COCO Mask R-CNN, IS-Net, DeepLabV3-MobileNet and prompted SAM on
the translucent bottle. The generic COCO/IS-Net models mostly select the
colourful label/cap instead of the faint bottle silhouette; DeepLab misses
some diagonal views. Prompted SAM returns candidate masks, but needs a manual
ROI and still leaks into the pale background in some views. **No segmentation
model is yet approved for live inference.** The SAM score is its own quality
estimate, not measured mask IoU.

See [`data/poc/SEGMENTATION_POC.md`](data/poc/SEGMENTATION_POC.md) and review
`data/poc/segmentation_review.jpg`. The current POC scripts are:

```bash
python3 tests/segment_poc.py --device cpu       # COCO Mask R-CNN baseline
python3 tests/segment_rembg_poc.py              # optional IS-Net baseline
python3 tests/segment_deeplab_poc.py            # semantic bottle-class baseline
python3 tests/segment_sam_poc.py                # prompted SAM mask drafts
python3 tests/component_presence_poc.py         # exploratory cap/sticker color cues
```

Optional dependencies for these experiments are listed in
`requirements-segmentation-poc.txt`. The first run may download model weights.

## Quick Start

### 1. Install dependencies

```bash
python3.10 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run the application

```bash
streamlit run app/main.py
```

### 3. Train a model

1. Go to **Train** in the sidebar
2. Upload 20-30 photos of good units (JPG or PNG, at least 640x480)
3. Click **Train Model** (a 25-image model builds in ~10 s on a laptop CPU)

### 4. Inspect units

1. Go to **Inspect** in the sidebar
2. Use your webcam or upload an image
3. Click **Inspect**
4. View the verdict, score, heatmap, explanation and certainty

## Demo

To populate the app with a trained model and demo data from MVTec AD:

```bash
python3 tests/fetch_mvtec_hf.py bottle      # fetch one category (~1 min)
python3 tools/seed_demo.py                  # train on 25 images + log 30 inspections
streamlit run app/main.py
```

The Demo page shows a one-click gallery (2 good, 3 defective units) with a
permanent "Demo mode" banner; gallery inspections are flagged and excluded
from the real dashboard.

Screenshots from the running app (in `data/screenshots/`):

| Screenshot | Shows |
| --- | --- |
| `06_dashboard_desktop.png` | Dashboard: totals, rejection rate, recent inspections |
| `07_inspection_detail.png` | Expanded inspection with review Accept/Reject |
| `13_heatmap.png` | Full result: FAIL verdict, heatmap on the defect, setup OK |
| `14_settings.png` | Threshold + impact preview, model versions, settings history |

## Project Structure

```
VisionQC/
├── app/
│   └── main.py               # Streamlit UI (Inspect, Train, Dashboard, Settings, Demo)
├── service/
│   └── inference.py          # PatchCore model + verdict/certainty/explanation/setup checks
├── db/
│   └── database.py           # SQLite layer (inspections, models, settings)
├── tests/
│   ├── test_core.py          # 27 unit/integration tests
│   ├── test_mvtec.py         # MVTec AD benchmark
│   ├── analyze_scores.py     # Threshold analysis + LOCO validation
│   ├── fetch_mvtec_hf.py     # Dataset fetcher
│   └── segment_*_poc.py      # Real-world foreground-segmentation experiments
├── tools/
│   └── seed_demo.py          # Train + seed demo inspections
├── data/                     # Runtime data (models, images, DB, screenshots)
├── BENCHMARK.md              # Full MVTec AD results
├── data/poc/                  # Eight real images, masks and POC reports
├── requirements.txt
└── README.md
```

## Testing

```bash
pytest tests/test_core.py -v          # unit + integration tests
python3 tests/test_mvtec.py --all --root data/mvtec_hf   # benchmark
```

## Configuration

Defaults (changeable in the UI):

| Setting | Default | Description |
|---------|---------|-------------|
| Threshold | 0.46 | Decision boundary, tuned on MVTec AD (see BENCHMARK.md) |
| Review band | ±0.05 | Range around threshold that yields REVIEW |
| Min training images | 5 | Minimum for a model; 20-30 recommended |
| Input size | 320x320 | Internal model input resolution |

## How It Works

1. **Training**: PatchCore extracts patch features (WideResNet-50, layers 2+3)
   from good images and builds a coreset "memory bank" of normal patterns.
2. **Reference**: The worst held-out good image sets the normalisation scale,
   so the worst good unit scores ~0.50 by construction.
3. **Inference**: New images are compared patch-by-patch to the memory bank;
   the image score is the maximum patch distance, normalised to 0-1.
4. **Verdict**: PASS / REVIEW / FAIL from the threshold and review band.
5. **Explanation**: The anomaly map yields location (3x3 grid), area %, and
   intensity — never a defect type (it learns normal only, by design).

## Limitations

- Position, scale and lighting sensitive: use a fixed camera or phone stand
  and a guide frame; the setup check warns when conditions drift.
- Cannot name defect types (no defect examples are used, by design).
- Screw-type micro-defects are the known weak spot of this configuration
  (see BENCHMARK.md caveats).
- Not integrated with PLCs, reject arms or ERP systems (roadmap only).

## License

MIT for the application code. MVTec AD dataset: non-commercial use only.
