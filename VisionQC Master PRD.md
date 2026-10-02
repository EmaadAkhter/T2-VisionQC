# VisionQC - Master Product Requirements Document

**Team Nexify | TechForge Hackathon 2026 | Version 2.0 (consolidated) | Oct 2, 2026**

This document replaces the earlier PRD, Feature Scope, MoSCoW Analysis, Summary and Code Template as the single source of truth. Where those documents disagreed, Section 1 records the decision taken.

---

## 1. Decision Log (conflicts resolved, ideas cut)

### 1.1 Conflicts resolved

| # | Conflict in earlier docs | Decision |
| --- | --- | --- |
| D1 | PRD v1 had binary PASS/FAIL plus a review flag; Feature Scope had a three-way PASS / REVIEW / FAIL | **Three-way verdict.** Review is a band around the threshold (Section 7.2). |
| D2 | Trust score was "Could-have" in MoSCoW and "Should-have" in Summary | **P1.** Renamed **Decision Certainty** (High / Medium / Low). It is a heuristic, not a probability, so no percentages are shown. |
| D3 | What-If and calibration were Should in one doc and Could in another | **P1**, built after the P0 set and after explanation, certainty and review. |
| D4 | "Confidence score" required by the problem statement was never defined | Satisfied by **anomaly score (0-1) plus Decision Certainty**. Both are shown. |
| D5 | Effort totals differed (20h / 32h / 45h) and the Summary's priority table omitted M4 and M5 | Single timeline in Section 14, tied to the requirement IDs below. |
| D6 | "Rejection rate" undefined once REVIEW exists | Rate = units with final disposition FAIL / all inspected today. Pending reviews are a separate tile (FR-08). |

### 1.2 Ideas cut or rewritten (the earlier docs were wrong or unwise here)

| Idea | Verdict | Reason |
| --- | --- | --- |
| **SHAP values** on the anomaly map (MoSCoW wow #1) | **Cut** | PatchCore already yields a pixel-level map. SHAP adds cost and no information. |
| **Defect classification CNN** (MoSCoW S6) | **Cut** | Needs labelled defects, which is exactly what this product exists to avoid. Contradicts the pitch. |
| **"Likely scratch, stain or missing print"** in explanations | **Rewritten** | An anomaly detector cannot know the defect type. Claiming it is a fabricated output a judge can break with one question. Explanations state only measurable facts: location, area %, intensity, brighter/darker than reference. |
| **Trust via "entropy of the anomaly map"** and a "94% confidence" display | **Cut** | Entropy of a heatmap is not calibrated confidence, and 94% is false precision. Replaced by margin-based certainty (Section 7.3). |
| **Calibration as "adjust score normalisation"** | **Rewritten** | Rescaling scores does not fix a lighting or position shift. Calibration now adds new good captures to the memory bank (Section 7.5). |
| **Learning-progress curve, QR sharing, email alerts, dark mode** | **Cut** | Low value for the demo, nonzero time cost. |
| **Rupee loss figures in the pitch** (e.g. "50 lakhs lost", "10 lakh systems") | **Removed** | Unsourced. A judge may ask where they come from. Use only figures you can cite. |
| **"Top 3% / Gold" outcome predictions** | **Removed** | Not a product requirement and not knowable. |
| **Handheld phone as the primary capture mode** | **Rewritten** | PatchCore is sensitive to position, scale and lighting. The reliable setup is a fixed camera, or a phone on a stand, with a guide frame. Handheld works only as a degraded mode (FR-13 warns about it). |
| **"Live" via `st.camera_input`** | **Clarified** | It is capture-then-score, not a video stream. "Live" in this PRD means a result within 1 second of tapping capture. True streaming is P2 (FR-24). |

### 1.3 Ideas kept and strengthened

Explainable verdict, three-way decision, certainty meter, threshold simulator, setup check, evidence log, defect gallery for the demo. These are the genuine differentiators and are specified in full below.

---

## 2. Executive Summary

VisionQC is an offline visual-inspection assistant for small manufacturers. A supervisor uploads **20-30 photos of good units** of one product. VisionQC learns "normal" using unsupervised anomaly detection (PatchCore), so no defect dataset is needed. An operator then scans new units with a webcam or phone. Each unit gets an anomaly score (0-1), a defect heatmap, a **PASS / REVIEW / FAIL** verdict, a plain-language reason, and a decision-certainty rating. The supervisor tunes strictness with a threshold slider that previews its impact, every inspection is logged for audit, and a dashboard shows today's rejection rate. Everything runs locally on a laptop with no GPU and no internet.

---

## 3. Problem, Users, Goals

### 3.1 Problem

Small manufacturers ship defective units or reject good ones because inspection relies on tired human eyes. Machine-vision integrators are expensive and slow to set up. Supervised deep learning needs hundreds of labelled defect images that small plants never collect. Manual inspection is inconsistent across shifts and leaves no audit trail.

### 3.2 Personas

**Ramesh Patil, Factory Supervisor (primary).** Runs a 12-person line, inspects by eye, comfortable with a smartphone, no ML knowledge. Wants fewer escaped defects, today's reject count, and independence from outside experts.

**Anita Sharma, Quality Manager (secondary).** Owns quality metrics and customer complaints. Wants trustworthy rejection data, trends, and decisions she can defend with evidence.

**Operator (implied).** Scans units all day. Needs one-tap inspection and unambiguous results.

### 3.3 User stories

1. As a supervisor, I teach the system with about 25 good photos and need no defect examples.
2. As an operator, I scan a unit and instantly see the verdict, where the problem is, and what to do next.
3. As a supervisor, I tune strictness and see the effect on past inspections before I apply it.
4. As a supervisor, I am warned when lighting or position is off, and I can add fresh good captures in seconds.
5. As a quality manager, every inspection is logged with score, threshold, verdict, reason and image.
6. As a quality manager, I see today's rejection rate and its trend.
7. As an operator, borderline units are sent to manual review instead of being forced into pass or fail.

### 3.4 Goals and KPIs

| KPI | Target | Stretch |
| --- | --- | --- |
| Inference time per unit (CPU laptop, capture to result) | median \< 500 ms, p95 \< 800 ms | median \< 300 ms |
| Image-level AUROC on demo product test set | >= 0.95 | >= 0.98 |
| Defect recall at default threshold (FAIL or REVIEW counts as caught) | >= 90% | >= 95% |
| False reject rate on held-out good units (FAIL only) | \< 10% | \< 5% |
| Training images required | 20-30 | 20 |
| Model build time | \< 2 min | \< 60 s |
| Calibration time | \< 15 s | \< 10 s |
| New user to first inspection, unaided | \< 10 min | \< 5 min |

Report accuracy as counts, not just percentages (for example "14 of 15 defects caught, 1 of 12 good units flagged"). With a test set this small, a quoted "92%" is statistically meaningless and a sharp judge will say so. Note also that a 10% false-reject rate would be unacceptable on a real line; the target is a hackathon bar, and the roadmap should say so.

### 3.5 Non-goals (this release)

Defect-type classification, multi-angle or 3D inspection, PLC or reject-arm integration, ERP/MES integration, cloud sync, multi-factory dashboards, role-based login, root-cause analytics, continuous retraining. These appear only as roadmap (Section 16) and must never be presented as implemented.

---

## 4. Product Flow

1. Supervisor creates or selects the inspection profile (one product in the hackathon build).
2. Uploads 20-30 good-unit photos. VisionQC builds the model and shows the setup baseline.
3. Operator opens Inspect, sees the setup status, and captures a unit.
4. VisionQC returns verdict, score, heatmap, reason, certainty and suggested action.
5. For REVIEW, the operator records Accept or Reject.
6. The inspection is logged. The dashboard updates.
7. Supervisor adjusts the threshold using the impact preview, or recalibrates when conditions change.

---

## 5. Final Scope (MoSCoW)

| ID | Feature | Priority |
| --- | --- | --- |
| FR-01 | Product onboarding and normal-only training | P0 |
| FR-02 | Camera inspection (webcam, phone, upload fallback) | P0 |
| FR-03 | Anomaly score (0-1) | P0 |
| FR-04 | Defect heatmap | P0 |
| FR-05 | Three-way verdict | P0 |
| FR-06 | Tunable threshold | P0 |
| FR-07 | Inspection logging | P0 |
| FR-08 | Today's rejection dashboard | P0 |
| FR-09 | Plain-language explanation and defect-region details | P1 |
| FR-10 | Decision certainty | P1 |
| FR-11 | Manual-review workflow | P1 |
| FR-12 | Threshold impact preview | P1 |
| FR-13 | Setup quality check | P1 |
| FR-14 | Calibration (add good captures) | P1 |
| FR-15 | Mobile-friendly UI | P1 |
| FR-16 | Reference comparison panel | P1 |
| FR-17 | Trend dashboard | P1 |
| FR-18 | CSV export | P1 |
| FR-19 | Demo mode and defect gallery | P2 (treat as required for the demo) |
| FR-20 | Batch upload | P2 |
| FR-21 | Multi-product profiles | P2 |
| FR-22 | Audio alert | P2 |
| FR-23 | Defect severity levels | P2 |
| FR-24 | Continuous live mode (streamlit-webrtc) | P2 |

**Build order (hard rule):** FR-01 to FR-08, then FR-05 review zone with FR-11, then FR-09, FR-10, FR-12, FR-13, FR-14, FR-15, then FR-16 to FR-19, then the rest. Scope freezes at hour 24 for P0 and hour 44 for everything.

---

## 6. Functional Requirements and Acceptance Criteria

Format: each criterion is independently testable. "Demo product" means the one product chosen in Section 15.

### P0

#### FR-01 Product onboarding and normal-only training

Supervisor uploads 20-30 good photos; the system builds a model of normal.

- AC-01.1 Accepts JPG and PNG, 1-30 files per batch, each at least 640x480 px. Files below that size or in other formats are rejected with a per-file message and the rest still load.
- AC-01.2 Fewer than 20 valid images shows a warning ("Accuracy drops below 20 photos") and still allows training. More than 30 is accepted, with a note that returns diminish.
- AC-01.3 A thumbnail grid of accepted images is shown before training, with a remove button per image.
- AC-01.4 Training runs with a progress indicator and completes in under 2 minutes for 30 images on a CPU laptop.
- AC-01.5 Training uses only the uploaded images. No defect image or label is requested anywhere in the flow.
- AC-01.6 Training holds out at least 20% of images (minimum 5) to compute the reference score used for normalisation (Section 7.1), then builds the final memory bank on all images.
- AC-01.7 On completion the model, reference score, training image statistics (mean brightness, blur, mean image) and a model version ID are saved to disk. After an app restart the last model loads without retraining, in under 5 seconds.
- AC-01.8 Retraining creates a new model version. The old version is kept and selectable until the supervisor deletes it. Logged inspections keep the version they were scored with.
- AC-01.9 Status chip on the Inspect screen shows "Trained on N images" and the training date.
- AC-01.10 If an uploaded set contains an obvious outlier (reference score check: any single image scores above 2x the median held-out score), the UI lists it as "possibly not a good unit" and lets the supervisor remove it.

#### FR-02 Camera inspection

Operator scans a new unit via laptop webcam or phone camera.

- AC-02.1 Inspect screen offers a camera capture control and an image-upload fallback. Both feed the same pipeline.
- AC-02.2 On a phone, the rear camera is selected by default where the browser allows it.
- AC-02.3 From tapping capture to a rendered result is under 1 second at p50 on the target laptop, with the model already loaded. The model is loaded once per session and cached.
- AC-02.4 While scoring, a visible "Analysing" state appears and the capture button is disabled to prevent double submission.
- AC-02.5 If no model exists, the Inspect screen shows a single call to action to train one and does not crash.
- AC-02.6 If camera permission is denied or unavailable, a plain message appears and the upload fallback remains usable.
- AC-02.7 Images are validated (non-empty, readable). A corrupt image shows an error and is not logged as an inspection.
- AC-02.8 The phone is verified working over the local network on the actual demo hardware (Section 13 risk R2).

#### FR-03 Anomaly score

- AC-03.1 Every inspection displays a score from 0.00 to 1.00 to two decimals, computed as defined in Section 7.1.
- AC-03.2 The score is shown on a bar with the threshold marked and the review band shaded.
- AC-03.3 Identical input image and identical model give an identical score across runs (deterministic inference).
- AC-03.4 Held-out good images from training score at or below 0.50 by construction of the normalisation; the test set confirms known defects score above held-out good units on average.
- AC-03.5 Raw score and normalised score are both stored in the log.

#### FR-04 Defect heatmap

- AC-04.1 A heatmap overlay is rendered at the input image's displayed size, with an opacity control (default 50%).
- AC-04.2 Regions at or above the threshold are outlined.
- AC-04.3 For each known defect image in the test set, the outlined region overlaps the true defect location (checked visually by the team on all test defects; target at least 80% of them).
- AC-04.4 For a unit that scores PASS, no region is outlined.
- AC-04.5 The overlay and original are shown side by side on desktop and stacked on phone.
- AC-04.6 The overlay image is saved with the inspection record.

#### FR-05 Three-way verdict

- AC-05.1 The verdict is PASS, REVIEW, or FAIL, determined only by Section 7.2.
- AC-05.2 Boundary behaviour: score exactly at T-delta is REVIEW; score exactly at T+delta is FAIL. Unit tests cover both edges.
- AC-05.3 The verdict is the largest element on screen and is conveyed by text and icon, not colour alone (green PASS, amber REVIEW, red FAIL).
- AC-05.4 Each verdict shows a suggested next action: PASS "Release unit"; REVIEW "Inspect highlighted area manually, then record decision"; FAIL "Set aside and inspect highlighted area".
- AC-05.5 Changing the threshold never alters past log records; it affects only future inspections.

#### FR-06 Tunable threshold

- AC-06.1 Slider range 0.00-1.00, step 0.01, default 0.60 (tuned on the test set, see 7.1). Review half-band delta defaults to 0.05 and is configurable in Settings (range 0.00-0.20).
- AC-06.2 The new value takes effect on the next inspection and persists across restarts.
- AC-06.3 Every change is written to a settings history (old value, new value, timestamp, optional PIN-verified flag).
- AC-06.4 If a PIN is configured, changing threshold, delta, model or data requires it. Without a PIN set, no prompt appears.
- AC-06.5 The Inspect screen always shows the current threshold, so a result is never read against the wrong value.
- AC-06.6 Moving the slider alone does not apply until the supervisor presses Apply (this enables FR-12).

#### FR-07 Inspection logging

- AC-07.1 Every successful inspection writes one record (schema in Section 10) before the result is shown as complete.
- AC-07.2 Record contains inspection ID, timestamp, product ID, model version, raw score, score, threshold, delta, verdict, certainty, explanation, setup status, original image path, overlay path and latency.
- AC-07.3 Write completes in under 100 ms and survives an app crash or restart; killing the process mid-session loses at most the in-flight unit.
- AC-07.4 Inspection IDs are unique and human-readable (for example `INS-20261003-0047`).
- AC-07.5 Optional operator note (max 280 chars) can be added after the result and is saved on the same record.
- AC-07.6 Two rapid consecutive inspections produce two distinct records with no dropped or duplicated rows.

#### FR-08 Today's rejection dashboard

- AC-08.1 Tiles show: Total inspected, Passed, Failed, Pending review, and **Rejection rate** (final FAIL / total inspected).
- AC-08.2 "Today" is the local calendar day of the host machine.
- AC-08.3 Dashboard reflects a new inspection within 1 second of the result appearing.
- AC-08.4 A table lists recent inspections (time, thumbnail, score, verdict, final disposition) with a filter by verdict.
- AC-08.5 With zero inspections today, the rate shows "No inspections yet" rather than 0% or a divide-by-zero error.
- AC-08.6 Rate arithmetic is covered by unit tests with seeded data, including pending reviews.

### P1

#### FR-09 Plain-language explanation and defect-region details

- AC-09.1 Every FAIL and REVIEW carries a one-to-two sentence explanation generated from Section 7.4, naming the location (3x3 grid label such as "bottom-right"), affected area (% of unit), and intensity (High / Medium).
- AC-09.2 Where measurable, it states whether the region is darker or brighter than the nearest normal reference.
- AC-09.3 The explanation **never states a defect type** (no "scratch", "stain", "dent") unless a future classifier is added. A text test scans generated strings against a forbidden-word list.
- AC-09.4 A defect-details panel shows location, area %, intensity and the number of separate regions found.
- AC-09.5 PASS shows "No unusual areas found."
- AC-09.6 Generation is deterministic, template-based, and adds under 50 ms. No LLM and no network call.
- AC-09.7 If the unusual area covers more than 25% of the image, the explanation says "Widespread difference; check lighting and unit position" instead of a location.

#### FR-10 Decision certainty

- AC-10.1 Each result shows Decision Certainty: High, Medium or Low, computed per Section 7.3, with a one-line reason such as "Score is close to the configured limit."
- AC-10.2 Anomaly score and certainty are visually separate and labelled with plain definitions ("How different from normal" vs "How sure the decision is").
- AC-10.3 Every REVIEW result is Low certainty, by definition.
- AC-10.4 Certainty drops one level when setup status was Caution or Poor at capture, or when the highlighted area is widespread (AC-09.7).
- AC-10.5 UI and docs never show certainty as a percentage and never call it a probability.
- AC-10.6 Certainty level is stored in the log.

#### FR-11 Manual-review workflow

- AC-11.1 A REVIEW result shows two buttons: "Accept as PASS" and "Reject as FAIL".
- AC-11.2 The chosen disposition is stored as the unit's final disposition with timestamp. The original system verdict is never overwritten.
- AC-11.3 Pending reviews are visible on the dashboard and can be resolved later from the log table.
- AC-11.4 Final disposition for PASS and FAIL equals the system verdict unless an operator overrides it. An override requires a note.
- AC-11.5 The log distinguishes system verdict from final disposition so override frequency can be reported.

#### FR-12 Threshold impact preview

- AC-12.1 While the slider moves, a panel shows, for a proposed threshold: projected rejection rate, number of units whose verdict would change, and direction ("4 more units would PASS").
- AC-12.2 Computation uses stored normalised scores from the last up to 200 inspections for the active model version, and completes in under 300 ms.
- AC-12.3 With fewer than 20 eligible inspections the panel is replaced by "Not enough inspections to estimate impact (N of 20)". It never shows fabricated figures.
- AC-12.4 The panel states its sample size and window ("Based on last 87 inspections").
- AC-12.5 Includes a plain-language risk line when the threshold is raised ("Subtle defects may be accepted") or lowered ("More good units may be flagged").
- AC-12.6 Nothing changes until Apply is pressed; Cancel restores the previous value.
- AC-12.7 Results match an independent recomputation in a unit test.
- AC-12.8 Scores are counted only from the same model version, since normalisation differs between versions.

#### FR-13 Setup quality check

- AC-13.1 Before and after each capture, a status of OK, Caution or Poor is shown with specific reasons, per Section 7.6.
- AC-13.2 Checks: brightness versus training baseline, blur, and alignment shift versus the training mean image.
- AC-13.3 A guide frame overlay shows where to place the unit.
- AC-13.4 Status is computed in under 100 ms and does not block inspection; Poor shows a warning and requires one extra tap to proceed.
- AC-13.5 Setup status is logged and feeds certainty (AC-10.4).
- AC-13.6 Thresholds for the checks are tuned on the demo setup so a well-placed good unit shows OK at least 9 times in 10.
- AC-13.7 A handheld-capture warning appears if alignment shift is large and repeated across three captures.

#### FR-14 Calibration (add good captures)

- AC-14.1 Supervisor starts Calibrate, captures 5-10 good units in current conditions, and confirms.
- AC-14.2 Calibration appends the new captures' patch features to the memory bank, re-applies coreset reduction, and recomputes the reference score, within 15 seconds on the target laptop.
- AC-14.3 Calibration creates a new model version. The previous version can be restored in one tap.
- AC-14.4 The Inspect screen shows "Last calibrated: X ago".
- AC-14.5 Calibration is blocked with a message if any capture scores above 2x the median score of the others (probable non-good unit).
- AC-14.6 Verified by test: after calibrating under changed lighting, the false-reject rate of good units under that lighting drops compared with before.
- AC-14.7 Calibration never uses a unit flagged FAIL unless the supervisor explicitly marks it good.
- AC-14.8 Out of scope: automatic recalibration. A drift hint ("Scores trending up over last 30 units, consider calibrating") is allowed as P2.

#### FR-15 Mobile-friendly UI

- AC-15.1 Layout is usable at 360 px width with no horizontal scroll.
- AC-15.2 Primary controls are at least 44 px tall.
- AC-15.3 Inspect screen is a single column: camera, verdict, score, details collapsed.
- AC-15.4 Verified on one real Android phone and one iPhone browser (or the devices available at the venue).
- AC-15.5 Body text is at least 16 px; contrast is at least 4.5:1.

#### FR-16 Reference comparison panel

- AC-16.1 Shows scanned unit, nearest normal training image (by feature distance), and heatmap overlay side by side.
- AC-16.2 Nearest reference is determined without re-running the model (precomputed image embeddings), under 150 ms.
- AC-16.3 Collapsed by default on mobile.

#### FR-17 Trend dashboard

- AC-17.1 Chart of rejection rate per hour for today, and per day for the last 7 days.
- AC-17.2 Points with fewer than 5 inspections are shown muted with a note, to avoid noisy percentages.
- AC-17.3 Chart updates within 2 seconds of a new inspection.

#### FR-18 CSV export

- AC-18.1 Download button exports the selected date range as CSV including all fields in FR-07.
- AC-18.2 Export of 1,000 rows completes in under 3 seconds.
- AC-18.3 Opens correctly in Excel (UTF-8 with BOM; ISO 8601 timestamps).
- AC-18.4 PDF export is out of scope.

### P2

#### FR-19 Demo mode and defect gallery

- AC-19.1 Gallery of 3-4 good, 5-8 defective and at least 1 borderline pre-captured images, each inspectable in one click through the normal pipeline.
- AC-19.2 Gallery inspections are logged with a `demo=1` flag and excluded from the real dashboard by default, so a demo never pollutes real data.
- AC-19.3 A visible "Demo mode" banner is shown at all times while active.
- AC-19.4 Gallery contents are real captures from the demo setup, not synthetic edits, and the borderline example genuinely lands in REVIEW at the default threshold.

#### FR-20 Batch upload

Folder or multi-file upload returns a results table (file, score, verdict) and logs each row. Acceptance: 20 images processed with a progress bar, partial failures reported per file.

#### FR-21 Multi-product profiles

Named profiles, each with its own model, threshold and calibration. Acceptance: switching profile loads its model in under 5 seconds and the log records the product ID.

#### FR-22 Audio alert

Short tone on FAIL. Acceptance: off by default, toggle in Settings. Note mobile browsers require a prior user tap before audio plays, so test on the phone.

#### FR-23 Defect severity

Minor / Major / Critical derived from score and affected area with documented cut-offs. Acceptance: cut-offs visible in Settings, severity logged.

#### FR-24 Continuous live mode

Streaming scoring through `streamlit-webrtc`. Acceptance: at least 2 frames per second on the demo laptop with a visible latency indicator. Attempt only if every P0 and P1 item is stable; it is the highest-risk item in this document.

---

## 7. Core Algorithm Specification

### 7.1 Score normalisation

PatchCore yields a raw image score (maximum of the patch anomaly map). Raw scores are not bounded to 0-1, so the framework's own normalisation is not used because it needs labelled anomalous validation data.

- `ref` = maximum raw score over the held-out good images (AC-01.6).
- Normalised score `s = clip(0.5 * raw / ref, 0, 1)`.
- Consequence: the worst held-out good unit sits at 0.50, and a unit twice as anomalous as that reaches 1.00.
- Default threshold T = 0.60 and delta = 0.05. These defaults are confirmed or adjusted on the test set at hour 12 and then fixed.
- Known weakness: with only 5-6 held-out images, `ref` is a noisy estimate. Mitigation: calibration (FR-14) adds good captures and refreshes `ref`.

### 7.2 Verdict zones

- PASS: `s < T - delta`
- REVIEW: `T - delta <= s < T + delta`
- FAIL: `s >= T + delta`
- Final disposition equals the verdict for PASS and FAIL; REVIEW is resolved by the operator (FR-11).

### 7.3 Decision certainty

- Margin `m = |s - T|`.
- Low: `m < delta` (the REVIEW zone). Medium: `delta <= m < 0.20`. High: `m >= 0.20`.
- Downgrade one level if setup status is Caution or Poor, or the highlighted area exceeds 25% of the image.
- Reason string is chosen from a fixed list keyed to which rule fired.
- This is a heuristic indicator of how far a result is from the decision boundary. It is not a calibrated probability and must not be described as one.

### 7.4 Explanation generation

1. Build a hot mask from the pixel-level map where the normalised pixel score is at or above T.
2. Find connected regions; keep those above a minimum size (0.5% of image).
3. For the largest region compute: centroid mapped to a 3x3 grid label, area % of image, peak intensity (High if peak above T + 0.2, else Medium), and mean brightness difference against the nearest normal reference (darker / brighter / similar).
4. Fill a fixed sentence template. Example: "Unusual area in the bottom-right, about 5% of the unit, darker than normal. Inspect this region manually."
5. No defect-type language (AC-09.3).

### 7.5 Calibration mechanism

Extract patch features for the new captures, append to the memory bank, re-run coreset subsampling, recompute `ref` from the combined held-out set plus the new captures, save as a new model version. If the chosen library does not expose the memory bank cleanly, this is the strongest argument for the in-house fallback in Section 9.3.

### 7.6 Setup checks

- Brightness: mean value channel within +/- 25% of the training mean.
- Blur: variance of the Laplacian at least 50% of the training median.
- Alignment: `cv2.phaseCorrelate` shift between the grayscale capture and the training mean image at most 5% of image width.
- Status: OK (all pass), Caution (one fails), Poor (two or more fail). Cut-offs are starting values, tuned on the demo setup.

---

## 8. Non-Functional Requirements

| Category | Requirement |
| --- | --- |
| Performance | Median inference under 500 ms, p95 under 800 ms; model load under 5 s; dashboard refresh under 1 s; 100 consecutive inspections with no memory growth beyond 10% |
| Reliability | 8 hours without restart; SQLite writes committed immediately; restart resumes with last model and settings; a failed inspection never corrupts the log |
| Usability | First inspection under 10 min unaided; at most 2 taps from home to inspect; no ML jargon in the main UI |
| Privacy | All data local; no network calls at runtime; images stay in a local folder; optional PIN |
| Offline | Works with no internet after setup. Pretrained backbone weights are downloaded and cached before the event, and a clean-machine offline test is part of the demo checklist |
| Compatibility | Latest two versions of Chrome, Edge, Safari, Firefox; Windows, macOS, Linux; Python 3.10+ with all dependency versions pinned; no GPU required |
| Accessibility | 4.5:1 contrast; never colour alone; 16 px minimum text; 44 px targets |
| Maintainability | Inference service separated from UI (Section 9), so the model can be swapped; config in one file; core logic unit-tested |
| Observability | Per-inspection latency logged; errors written to a local log file |
| Retention | Logs 90 days, images 30 days (configurable); "Clear all data" requires confirmation |

---

## 9. Architecture

### 9.1 Components

```
[Camera: webcam / phone browser / upload]
        |
[Streamlit UI] <-- threshold, calibration, what-if
        |
[Inference service (pure Python, no Streamlit imports)]
   preprocess -> PatchCore features -> NN search -> anomaly map + raw score
        |
[Post-processing]
   normalise -> verdict -> certainty -> explanation -> setup check -> overlay
        |
[SQLite: inspections, settings, settings_history, models]  +  [images/ folder]
        |
[Dashboard, impact preview, trends, export]
```

### 9.2 Stack

Python 3.10+, PatchCore (Anomalib or in-house, 9.3), OpenCV, torchvision, Streamlit, SQLite via `sqlite3`, pandas, Plotly. All dependency versions pinned in `requirements.txt` at hour 0.

### 9.3 Model-library decision gate (hour 4)

Time-box the Anomalib path to the first 4 hours. Its API has changed repeatedly across versions, and the supplied template contains several calls that do not match current releases (Appendix A). The needs here (custom normalisation, appendable memory bank for calibration, nearest-reference lookup) are easier with direct access to the features. **If Anomalib is not returning a correct score and heatmap by hour 4, switch** to a minimal in-house PatchCore: a pretrained WideResNet-50 truncated at layers 2 and 3, 3x3 local aggregation of patch features, random or greedy coreset, and nearest-neighbour search with `torch.cdist` or `faiss-cpu`. It is roughly 150 lines and gives full control. This is a decision gate, not a plan to do both in parallel.

### 9.4 Streamlit constraints to design around

- Streamlit reruns the script on every interaction: cache the model with `st.cache_resource`, keep state in `st.session_state`, and open SQLite connections per call (not one global connection).
- `st.camera_input` returns a still image; it is not a stream.
- Phone browsers expose the camera only in a secure context. A laptop's `http://192.168.x.x` address will not work. See risk R2 for the mitigation.

---

## 10. Data Requirements

### 10.1 Training data

20-30 good photos of one product, at least 640x480, resized internally. Fixed camera position, distance, background and lighting. Include small natural variation (slight position shifts, minor lighting drift) so normal is not over-narrow. No defective units.

### 10.2 Test data (never used for training)

10-15 good units not in the training set plus 15-25 defective units. Prepare controlled defects: black sticker or mark, surface scratch, label removed or misaligned, stain, tape, rotated or covered print. Capture under the same fixture. If no physical product is ready by hour 6, use an MVTec AD category as a stand-in for development only, not for the final demo.

### 10.3 Schema

**inspections**

| Field | Type | Notes |
| --- | --- | --- |
| id | INTEGER PK | auto |
| uid | TEXT UNIQUE | `INS-YYYYMMDD-NNNN` |
| timestamp | TEXT | ISO 8601 local |
| product_id | TEXT | default "default" |
| model_version | TEXT | FK to models |
| raw_score | REAL | unnormalised |
| score | REAL | 0-1 |
| threshold | REAL | in effect |
| delta | REAL | review half-band in effect |
| verdict | TEXT | PASS / REVIEW / FAIL |
| certainty | TEXT | High / Medium / Low |
| disposition | TEXT | PASS / FAIL / PENDING |
| disposition_by_override | INTEGER | 1 if operator overrode |
| operator_note | TEXT | optional |
| explanation | TEXT | plain language |
| region_label | TEXT | grid label |
| area_pct | REAL | affected area |
| setup_status | TEXT | OK / Caution / Poor |
| image_path / overlay_path | TEXT | local files |
| latency_ms | INTEGER | capture to result |
| demo | INTEGER | 1 for gallery runs |

**models**: version, product_id, created_at, n_images, backbone, coreset_ratio, ref_score, baseline_brightness, baseline_blur, parent_version. **settings**: product_id, threshold, delta, active_model_version, pin_hash (nullable). **settings_history**: id, timestamp, field, old_value, new_value.

Indexes on `timestamp`, `verdict`, `model_version`.

---

## 11. UI Specification

**Inspect (home).** Product name and status chip ("Trained on N images", "Last calibrated X ago"). Setup status strip with reasons. Camera view with guide frame and a large Inspect button. Result: verdict banner, score bar with threshold and review band, certainty, one-line explanation, heatmap beside original, Accept/Reject buttons if REVIEW, collapsible details and reference panel. Today's counts at the bottom.

**Settings.** Threshold slider with live impact panel and Apply/Cancel. Review band setting. Calibrate. Train / Retrain with upload. Model versions with restore. PIN. Data retention and clear.

**Dashboard.** Rejection-rate tile, totals, pending reviews, trend charts, filterable log table with thumbnails, CSV export.

**Demo mode.** Gallery of one-click samples with a permanent banner.

**Principles:** one tap to inspect; verdict is the largest element; plain language ("Unusual area found", not "anomaly map"); never colour alone; mobile-first single column.

---

## 12. Testing Strategy and Traceability

| Type | Covers | Pass condition |
| --- | --- | --- |
| Unit | Normalisation, verdict boundaries, certainty rules, explanation templates and forbidden-word scan, rejection-rate arithmetic, impact preview, SQLite round trip | All pass, boundary cases included |
| Integration | Train, inspect, log, dashboard; restart and reload; calibration and restore | Full cycle gives correct dashboard counts |
| Accuracy | Held-out good and defective sets | AUROC >= 0.95; recall >= 90%; false rejects \< 10%, reported as counts |
| Heatmap localisation | Every test defect inspected visually | >= 80% overlap with real defect |
| Performance | 100 consecutive inspections | Median \< 500 ms, p95 \< 800 ms, no crash or leak |
| Device | Laptop webcam, Android, iPhone, upload fallback | Camera, layout and result work on each |
| Offline | Airplane mode on a clean venv | Entire demo flow works |
| User acceptance | Someone outside the team, no guidance | First inspection under 10 minutes |
| Failure | No model, corrupt image, camera denied, empty log | Clear message, no crash |
| Demo | Full 2-minute script three times in a row, once offline | No manual fixes |

**Traceability:** every AC ID above maps to one test case in the test sheet; any untested AC is flagged as a gap before the hour-44 freeze.

---

## 13. Risks

| ID | Risk | Likelihood | Impact | Mitigation | Fallback |
| --- | --- | --- | --- | --- | --- |
| R1 | PatchCore is sensitive to position, scale and lighting, so handheld phone use gives false rejects | High | High | Fixed fixture or phone stand, guide frame, setup check, calibration | Laptop webcam on a fixed mount |
| R2 | Phone camera blocked because the local HTTP address is not a secure context | High | High | Test at hour 2 on real phones. Serve over local HTTPS with a self-signed certificate, or use Streamlit's upload widget, which opens the phone camera natively | Laptop webcam or upload only |
| R3 | CPU inference exceeds 500 ms | Medium | High | Benchmark by hour 4; smaller input size, smaller coreset, lighter backbone, cache model | Show "Analysing" state; relax target to 800 ms and say so |
| R4 | Small defects missed at low resolution | Medium | High | Choose a demo product whose defects are visible at the working resolution; test defect sizes early | Larger defects in the demo set |
| R5 | Normalisation is unstable with 5-6 held-out images | Medium | Medium | Calibration refresh; verify default T on test set | Manual threshold set at hour 12 |
| R6 | Anomalib version or API mismatch | High | High | Pin versions; hour-4 decision gate (9.3) | In-house PatchCore |
| R7 | Streamlit state or rerun bugs (double inserts, lost state) | Medium | Medium | Session state discipline, per-call DB connections, idempotent writes, test AC-07.6 | Simplify screens |
| R8 | Judges challenge claims (accuracy, cost, "AI explains why") | Medium | Medium | Report counts, cite sources or omit figures, never claim defect type | Honest scope slide |
| R9 | Scope creep | High | Medium | Build order in Section 5; freeze at H24 and H44 | Drop P2 |
| R10 | Live demo failure | Medium | High | Rehearse; gallery; backup recording; second laptop with a pre-trained model | Pre-recorded video |
| R11 | Pretrained weights fail to download at the venue | Medium | High | Cache weights locally and test offline on a clean machine | Ship weights in the repo folder |

**Assumptions:** at least one laptop with Python 3.10+ and 8 GB RAM; a physical demo product and a way to fix the camera position; weights cached before the event. If any assumption fails, tell the team on day one, not at hour 40.

---

## 14. Timeline and Ownership

Suggested split for five people: (1) ML and tuning, (2) inference service and post-processing, (3) Streamlit UI, (4) database, dashboard and impact preview, (5) data capture, testing, demo and documentation. Adjust if the team is smaller; with fewer people, move FR-16, FR-17, FR-18 and FR-19 behind a go/no-go call at hour 36.

| Phase | Hours | Work | Exit checkpoint |
| --- | --- | --- | --- |
| 1 | 0-12 | Environment and pinned deps, capture training and test sets, library gate at H4, camera/HTTPS check at H2, inference script, normalisation, tune T | H12: image in, score and heatmap out in under 500 ms; at least 80% of test defects caught; defaults for T and delta fixed |
| 2 | 12-24 | Streamlit UI, camera input, threshold, three-way verdict, logging, dashboard, review workflow (FR-01 to FR-08, FR-11) | H24: all P0 ACs pass. **P0 scope frozen** |
| 3 | 24-36 | Explanation, certainty, impact preview, setup check, calibration, mobile layout (FR-09, 10, 12, 13, 14, 15) | H36: P1 core works on a real phone |
| 4 | 36-48 | FR-16 to FR-19, bug fixing, performance, testing, rehearsal, backup video | H44: feature freeze. H48: submission |

Hours 44-48 are for testing and rest only. Nobody writes new features after H44.

---

## 15. Demo Plan

**Demo product.** One light-coloured bottle with a printed label, or a packaged box, on a plain background under a fixed light with a shield to remove window glare. Confirm choice by hour 6. Prepare defects: small black sticker, tape, label shifted or removed, scuff mark. Use at least one subtle defect and one obvious one, plus one borderline unit that reliably lands in REVIEW.

**2-minute script**

| Time | Beat |
| --- | --- |
| 0:00-0:15 | Problem: small factories lack both a vision budget and a defect dataset |
| 0:15-0:35 | Show the 25 good photos; the model is ready (pre-trained copy loaded if live training is slow) |
| 0:35-0:55 | Scan a good unit: PASS, low score, no highlighted area |
| 0:55-1:20 | Scan a defective unit: FAIL, heatmap on the defect, plain-language reason, certainty High |
| 1:20-1:40 | Scan the borderline unit: REVIEW, certainty Low, record the operator decision |
| 1:40-1:55 | Move the threshold slider and show the impact preview on logged units, then Apply |
| 1:55-2:00 | Dashboard: today's rejection rate; close |

**Talking points:** needs only good photos; runs offline on a laptop; explains itself and admits uncertainty; supervisor stays in control with a safe preview; every unit is logged and auditable.

**Prepare answers for likely questions:** how it works with no defect data (nearest-neighbour distance to normal patches); what happens when lighting changes (setup check and calibration); false-reject rate (report counts); why it cannot name the defect type (it learns normal only, by design); how it would plug into a line (roadmap, not built).

**Backup:** 2-minute screen recording of a clean run; second laptop with the app and a pre-trained model; printed screenshots of each beat.

---

## 16. Roadmap (not implemented, never claim otherwise)

PLC, conveyor and reject-arm integration; industrial cameras; multi-angle and 3D inspection; defect-type classification trained on factory-labelled data; ERP/MES integration; cloud multi-factory dashboard; role-based login and audit workflow; root-cause analytics tied to machine, supplier and batch; shift-wise analytics; defect clustering; ONNX / OpenVINO edge export; auto-recalibration suggestions.

---

## 17. Definition of Done

1. All P0 acceptance criteria pass.
2. P1 core (FR-09, 10, 11, 12, 13, 14, 15) passes on a real phone.
3. Accuracy reported as counts on a held-out set.
4. Full demo runs three times in a row, once offline.
5. Backup recording and second-laptop copy exist.
6. No claim in the pitch or slides lacks a source or an implementation.

---

## Appendix A: Corrections to the supplied code template

The template is a useful outline but will not run as written against current Anomalib releases. Verify each item against your pinned version before relying on it.

1. `engine.save_checkpoint(...)` is not an `Engine` method; checkpoints are written by the trainer, so use the engine's trainer or the checkpoint path from `fit`.
2. `Engine(device=...)` is not a valid argument in recent versions (accelerator/devices are used instead).
3. `Inferencer(model=model, checkpoint_path=...)` does not match the current inference classes; use the torch inference class or `engine.predict`.
4. `pred_mask` is a binary mask, not the anomaly map. The heatmap needs the anomaly map, and a binary mask will render as a blank image.
5. The framework's normalised `pred_score` depends on validation data that includes anomalies, which this product does not have. Use Section 7.1.
6. `st.image(..., cmap="hot")` is not a valid argument; render the colormap with OpenCV first. `use_column_width` is deprecated in favour of `use_container_width`.
7. A single module-level SQLite connection that is closed at the end of the script breaks under Streamlit's rerun model; open a connection per operation.
8. The template's `score > threshold` binary logic ignores the review zone and the PRD's logging fields.
9. The template trains and tests on MVTec "bottle" only; the Streamlit app never writes images or model versions.

## Appendix B: Glossary

| Term | Meaning |
| --- | --- |
| Anomaly detection | Finding items that differ from a learned notion of normal, without defect examples |
| PatchCore | Stores features of small image patches from good images; flags new patches far from all of them |
| Memory bank / coreset | The stored normal patch features, and the reduced representative subset that keeps inference fast |
| Anomaly score | Normalised 0-1 measure of how different a unit is from normal |
| Threshold / review band | The decision point, and the zone around it where the system asks for a human |
| Decision certainty | Heuristic of how far a result is from the decision boundary; not a probability |
| Final disposition | The unit's end result after any operator review |
| Calibration | Adding fresh good captures to adapt to current conditions |

## Appendix C: References

Roth et al., "Towards Total Recall in Industrial Anomaly Detection" (PatchCore), CVPR 2022. Bergmann et al., MVTec AD dataset, CVPR 2019. Anomalib documentation and repository. Streamlit documentation (camera input, caching, session state).

## Appendix D: Open Questions (answer by hour 2)

1. Which physical product is the demo product, and who captures the images?
2. Is there a fixed mount for the camera or phone?
3. How many people are on the team, and who owns each area?
4. Which phones will be used on the day, and what is the venue network like?
5. Does the demo need a PIN to be shown, or is it left as a hidden setting?