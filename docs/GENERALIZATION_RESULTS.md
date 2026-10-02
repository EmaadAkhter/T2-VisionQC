# Generalized Pipeline — Results

The generalization workstream makes VisionQC onboard **any product on any
camera without code changes**: the operator captures background frames
(optional) and good units, approves one mask, and the stored **product
profile** drives fully automatic runtime inspection.

Nothing product-specific exists in code — no classes, no colors, no part
names, no fixed boxes.

## How a profile is built

1. Optional: 5–10 empty-scene frames → background model (median + per-pixel σ).
2. 20–30 good-unit frames.
3. Automatic mask proposal:
   - with background frames: pixels deviating from the empty scene beyond k·σ;
   - without: border-color fallback (plain backdrops).
4. **Operator approves the mask once** (brush add/remove, undo, reset).
5. Profile stores: canonical mask, background model, presence regions
   (sub-regions the product reliably occupies), model version, threshold.
6. Runtime is fully automatic: alignment/health from an adaptive foreground
   estimate, but **scoring happens inside the canonical region** — so a
   missing part cannot crop itself out of the check.

Component-agnostic missing-part detection: presence regions learned from good
frames, kept only inside the approved mask and never pinned to the frame
border; a coverage drop below the profile's coverage floor flags the unit
(no color rules, no part names). When no region is stable across the good
frames the check is disabled and the wizard says so.

## Zero-code-change onboarding: two unrelated objects

Method: 30 good images from the official MVTec train split, border-fallback
mask (no empty-scene frames), profile threshold 0.55 (these runs predate the
0.50 default; the worst held-out good image sits at 0.50 by construction of
the normalisation), full official test split. These are smoke results for the
*onboarding flow*, not a tuned benchmark.

### metal_nut — a metal part (nothing like a bottle)

| Metric | Value |
| --- | --- |
| Training images | 30 |
| Proposed mask area | 57.4% of frame |
| Presence regions | 37.5% of frame |
| Test units | 115 (93 defective, 22 good) |
| **AUROC (profile region)** | **0.999** |
| AUROC (full frame) | 0.999 |
| Good units | **22/22 PASS**, 0 failed |
| Defects caught (REVIEW+FAIL) | **93/93 (100%)** |

### hazelnut — a textured natural object

| Metric | Value |
| --- | --- |
| Training images | 30 |
| Proposed mask area | 32.3% of frame |
| Presence regions | 28.5% of frame |
| Test units | 110 (70 defective, 40 good) |
| **AUROC (profile region)** | **1.000** |
| AUROC (full frame) | 1.000 |
| Good units | **38/40 PASS**, 2 REVIEW, **0 failed** |
| Defects caught (REVIEW+FAIL) | **70/70 (100%)** |

Score separation (hazelnut): good p50 0.46 / p90 0.49 / max 0.52 vs defects
p10 0.58 / p50 0.63. The profile default threshold is now 0.50 (band
0.45–0.55): small onboarding sets need the margin below the worst-good line.

## The eight bottle images (honest smoke result)

Five good photos, three labelled missing-part photos, **no background frames**,
transparent product on a pale backdrop. Built with the border fallback.

| Image | Expected | Verdict | Score | Missing-region signal |
| --- | --- | --- | --- | --- |
| 1–5 | good | PASS (5/5) | 0.24–0.28 | 0% |
| 6 | cap missing | PASS | 0.39 | 0% (no region exists) |
| 7 | sticker missing | **FAIL** | 0.62 | 0% |
| 8 | cap + sticker missing | **REVIEW** | 0.49 | 0% (no region exists) |

**Counts: 2/3 defects flagged, 0/5 false fails.**

What this means honestly:
- The **sticker-missing unit is caught by the calibrated score** (0.62 → FAIL)
  and the **both-missing unit sits at the review-band edge** (0.49 → REVIEW).
  Whether it reviews or passes is decided by a ~0.01 margin.
- The **missing cap alone is not detectable on this set**: the cap sits in a
  different place in every photo, so it falls outside the canonical mask
  (2.5% of the frame — label area only) and no stable presence region can
  form. With no background frames and a transparent body, no foreground
  signal exists for it either. The wizard now warns about exactly this
  ("no stable product regions found", "mask covers only 3% of the frame").
- An earlier revision reported 3/3 here. That number was an artifact of two
  things, both now fixed:
  1. a flood-fill bug in `cleanup_mask` that turned image 1's 6% foreground
     into 100% (a background corner blob then became a "presence region" and
     the missing cap "failed" on it), and
  2. region parameters relaxed only in the test harness, not in the app.
  With the fixes, `tools/validate_poc8.py --tuned` also reports 2/3.
- This is a **data/capture limitation**, not a code defect: the transparent
  bottle needs empty-scene background frames, a fixed fixture, and 20–30
  onboarding images for the component check to work as designed.

## Reproduction

```bash
python3 tools/validate_profile_generic.py metal_nut
python3 tools/validate_profile_generic.py hazelnut
python3 tools/validate_poc8.py              # app-faithful (2/3 on this set)
python3 tools/validate_poc8.py --tuned      # historical relaxed params
python3 -m pytest tests/test_foreground.py -q      # 20 unit tests
```

## Next steps for production-grade generalization

1. Require (or strongly recommend) background frames during onboarding — the
   median+σ model is dramatically better than the border fallback on
   low-contrast and transparent products.
2. Capture 30+ good units per camera view, all with the intended fixture.
3. Tune the coverage floor per profile with a few held-out good units.
4. Optional: fine-tune a compact foreground segmenter from approved masks
   (the wizard already saves them) to replace the background-difference
   heuristic where lighting is unstable.
