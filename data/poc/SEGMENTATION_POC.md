# VisionQC Real-World Segmentation POC

## Dataset and labels

Eight source JPEGs were copied unchanged to `data/poc/raw/` and mapped by
WhatsApp timestamp/suffix order in `manifest.json`:

| Image | Expected condition |
|---|---|
| 1–5 | Good, varied orientation |
| 6 | Cap missing |
| 7 | Sticker missing |
| 8 | Cap and sticker missing |

The filename-to-image mapping should be visually confirmed using
`contact_sheet.jpg` before using these labels for training. The source images
have a pale background and the bottle body is translucent/low contrast.

## Candidate models tested

All inference was local. We tested general pretrained models; none was fine-
tuned for this bottle. Mask area and the model's own confidence are not ground-
truth segmentation metrics; no human pixel masks exist yet.

| Candidate | Observed result | POC decision |
|---|---|---|
| COCO Mask R-CNN, bottle class | Returned no bottle mask on 2/8 images; several other masks mostly covered colorful label/cap patches rather than the translucent body. | Reject as the foreground segmenter. Keep the run only as a negative baseline. |
| IS-Net (`rembg`, `isnet-general-use`) | Produced a foreground mask on all 8 but tended to select the high-salience label/cap and omit much of the translucent bottle body. | Reject as a complete bottle mask. It may still help find color components. |
| COCO/VOC DeepLabV3-MobileNet, bottle class | Good bottle-like regions on some vertical/side views, but no bottle-class region on diagonal Images 4 and 5. Full-frame inference was ~60–70 ms after warm-up on this Mac/MPS; latency does not compensate for missed views. | Not acceptable across the supplied orientations. ROI-cropped results are also saved for diagnosis. |
| SAM ViT-B with per-image box/point prompts and ROI clipping | Produced a candidate mask for all 8. Some views follow the silhouette; horizontal/low-contrast views still include background. The prompts are manually specified per image, so this is not automatic camera detection. About 2.7–4.9 s/frame on this Mac/MPS. | Useful to draft labels offline; too slow and not yet accurate enough for live inference. Do not treat SAM's predicted-IoU estimate as measured IoU. |

## Main finding

The generic models do **not** reliably infer the full transparent bottle boundary
from these images alone. The bottle transmits the background, and the visible
silhouette has weak contrast. A segmentation model can suppress pixels outside
the silhouette, but it cannot make the background visible through clear plastic
disappear. For this product, success will need a combination of:

1. a repeatable per-camera search ROI and trigger;
2. controlled light/backdrop that makes the transparent outline visible;
3. reviewed bottle/component masks and a small model trained for this product;
4. a fallback to REVIEW/No valid capture when the mask is uncertain.

Cap/sticker absence is a separate inspection task. The proposed POC pipeline is:

```text
camera frame → fixed camera ROI → bottle mask → orientation normalization
             → cap-present check + sticker-present check
             → masked anomaly check (later) → verdict/evidence
```

## Reproduction

```bash
# COCO instance-segmentation baseline
python3 tests/segment_poc.py --device cpu

# IS-Net salient foreground baseline (optional dependency)
python3 tests/segment_rembg_poc.py

# Semantic bottle-class baseline
python3 tests/segment_deeplab_poc.py

# Prompted SAM label-drafting baseline
python3 tests/segment_sam_poc.py

# Color-component diagnostic only; not a production classifier
python3 tests/component_presence_poc.py
```

Outputs are under `data/poc/{segmenter_output,rembg_output,deeplab_output,
sam_output,component_output}/`. Keep originals in `data/poc/raw/` unchanged.

## Next POC gate

1. Human-review/annotate the bottle silhouette, cap and sticker on all 8 images.
2. Confirm the filename-to-label mapping against the contact sheet.
3. Capture more examples across permitted rotations and backgrounds; these eight
   are only a smoke set, not a generalization test.
4. Train or fine-tune a compact local segmenter on reviewed masks; use a held-out
   pose/background set.
5. Integrate only if the held-out median mask IoU is ≥0.90, every tested pose is
   ≥0.80, background false activation outside the mask is ≤5%, and the local
   segmenter meets the agreed stream rate. These are proposed gates, not results
   achieved by the pretrained baselines above.
