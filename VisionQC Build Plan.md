# VisionQC - Build Plan

**Team Nexify | TechForge Hackathon 2026 | Derived from VisionQC Master PRD v2.0**

Small-step execution plan. Each step is checkable. Requirement IDs in brackets map back to the PRD; do not start a step before the previous phase exit checkpoint passes.

Hard rules: P0 freeze at H24, feature freeze at H44. When short on time, cut from the bottom (P2, then FR-16/17/18) at the H36 go/no-go.

---

## Phase 0 - Setup (H0-2)

- [ ] **0.1** Lock the demo product (light-coloured bottle/box, plain background). Confirm by H6. *(Appendix D)*
- [ ] **0.2** Secure a fixed mount for the laptop webcam or phone. *(R1)*
- [ ] **0.3** Create repo skeleton + Python 3.10+ venv; write and pin `requirements.txt`. *(9.2)*
- [ ] **0.4** Test the phone camera over the LAN (H2). If the `http://192.168.x.x` address blocks it, stand up local HTTPS with a self-signed cert, or fall back to the upload widget. *(R2, 9.4, AC-02.8)*
- [ ] **0.5** Download and cache pretrained backbone weights; verify a clean-machine offline run. *(R11, NFR Offline)*
- [ ] **0.6** Answer Appendix D open questions; assign the five workstreams. *(§14)*

**Exit H2:** repo builds, phone camera path confirmed or fallback chosen, weights cached.

---

## Phase 1 - Core inference (H2-12)

- [ ] **1.1** Capture 20-30 good training images (≥640x480, fixed fixture, natural variation). *(10.1, FR-01)*
- [ ] **1.2** Capture test sets: 10-15 held-out good + 15-25 defective, controlled defects. If no product by H6, use an MVTec category for development only. *(10.2)*
- [ ] **1.3** Write one script: image in → PatchCore raw score + anomaly map out. *(FR-03, FR-04)*
- [ ] **1.4** Implement normalisation `s = clip(0.5 * raw / ref)` with held-out reference. *(7.1, AC-01.6, AC-03.4)*
- [ ] **1.5** Implement verdict zones PASS / REVIEW / FAIL. *(7.2, FR-05)*
- [ ] **1.6** Tune default T and delta on the test set; then fix them. *(7.1, AC-06.1)*
- [ ] **1.7** **H4 decision gate:** if Anomalib is not returning a correct score and heatmap, switch to the in-house ~150-line PatchCore (WideResNet-50, coreset, NN search). *(9.3, R6)*
- [ ] **1.8** Benchmark CPU inference; if >500 ms, shrink input/coreset or lighten backbone. *(R3, NFR Performance)*

**Exit H12:** image in → score + heatmap out in <500 ms; ≥80% of test defects caught; T and delta fixed.

---

## Phase 2 - P0 app (H12-24)

- [ ] **2.1** Extract the inference service as a pure-Python module (no Streamlit imports). *(9.1, NFR Maintainability)*
- [ ] **2.2** Onboarding: upload 20-30 photos, validate, thumbnail grid, train, save model + reference + stats + version ID. *(FR-01)*
- [ ] **2.3** Inspect screen: camera capture + upload fallback, "Analysing" state, no-model CTA, permission handling. *(FR-02)*
- [ ] **2.4** Result view: score bar with threshold and review band, heatmap overlay with opacity, original beside/stacked. *(FR-03, FR-04)*
- [ ] **2.5** Verdict banner (largest element, text + icon, never colour alone) with suggested action. *(FR-05)*
- [ ] **2.6** SQLite schema: inspections, models, settings, settings_history; indexes; per-call connections. *(10.3, 9.4)*
- [ ] **2.7** Log every inspection with full schema + human-readable `INS-YYYYMMDD-NNNN`; optional note. *(FR-07)*
- [ ] **2.8** Dashboard tiles (Total, Passed, Failed, Pending review, Rejection rate) + filterable log table. *(FR-08)*
- [ ] **2.9** Tunable threshold slider with persistence, settings history, current-threshold display, Apply gate. *(FR-06)*
- [ ] **2.10** Run all P0 acceptance criteria; fix gaps. → **Freeze P0 at H24.** *(§5, §14)*

**Exit H24:** all P0 ACs pass; P0 scope frozen.

---

## Phase 3 - P1 core (H24-36)

- [ ] **3.1** Explanation generator: hot mask, connected regions, 3x3 grid label, area %, intensity, darker/brighter; fixed templates; no defect-type words. *(FR-09, 7.4)*
- [ ] **3.2** Decision certainty (High/Medium/Low) with reason string; downgrade on Caution/Poor or widespread area; never a percentage. *(FR-10, 7.3)*
- [ ] **3.3** Review workflow: Accept/Reject buttons, final disposition, pending resolution, override note, system-vs-final distinction. *(FR-11)*
- [ ] **3.4** Threshold impact preview: projected rate, changed count, direction, risk line, sample size, Apply/Cancel. *(FR-12)*
- [ ] **3.5** Setup quality check: brightness, blur, alignment; OK/Caution/Poor; guide frame; handheld warning; feeds certainty. *(FR-13, 7.6)*
- [ ] **3.6** Calibration: capture 5-10 good, append memory bank, re-coreset, recompute `ref`, new version + restore; block probable non-good. *(FR-14, 7.5)*
- [ ] **3.7** Mobile layout: single column, 360 px, 44 px targets, 16 px text, no horizontal scroll. *(FR-15)*

**Exit H36:** P1 core works on a real phone.

---

## Phase 4 - P2 + polish (H36-48)

- [ ] **4.1** H36 go/no-go: keep or cut FR-16, FR-17, FR-18, FR-19. *(§14)*
- [ ] **4.2** Reference comparison panel (nearest normal image by precomputed embeddings, <150 ms). *(FR-16)*
- [ ] **4.3** Trend dashboard (per hour today, per day 7 days, muted low-sample points). *(FR-17)*
- [ ] **4.4** CSV export (UTF-8 BOM, ISO 8601, date range). *(FR-18)*
- [ ] **4.5** Demo mode and defect gallery (`demo=1`, excluded from dashboard, permanent banner). *(FR-19)*
- [ ] **4.6** Optional P2: batch upload, multi-product, audio alert, severity, live mode (FR-20-24) only if all P0/P1 stable.
- [ ] **4.7** Full test pass: unit, integration, accuracy (as counts), heatmap localisation, performance, device, offline, failure, UAT. *(§12)*
- [ ] **4.8** Performance + memory check: 100 consecutive inspections, no leak. *(NFR Performance)*
- [ ] **4.9** **Freeze features at H44.** *(§14)*
- [ ] **4.10** Rehearse the 2-minute demo 3× in a row, once offline; record backup video; prep second laptop. *(§15, R10)*

**Exit H48:** submission. Definition of Done met (P0 pass, P1 core on phone, counts reported, 3 clean demo runs, backup exists, no unsourced claims). *(§17)*

---

## Roles (five people - adjust to team size)

1. ML and tuning
2. Inference service and post-processing
3. Streamlit UI
4. Database, dashboard, impact preview
5. Data capture, testing, demo, documentation
