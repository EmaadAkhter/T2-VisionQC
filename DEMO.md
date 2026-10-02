# VisionQC — Demo Runbook

A 5-minute end-to-end walkthrough of the platform: local cloud, desktop app,
live inference and the phone companion.

## One-time setup (before the demo)

```bash
# Terminal 1 — control plane (local stack, or use --hosted)
supabase start
python3 tools/write_supabase_env.py

# Verify the backend is healthy
python3 tools/test_rls.py                # expect: 20/20 checks passed

# Terminal 2 — web admin console
cd admin-web && npm install && npm run dev    # http://localhost:5173

# Terminal 3 — desktop app
python3 -m desktop.launcher
```

Packaged macOS app: the first time you open a camera, macOS asks for camera
access — click **Allow**. To reset the prompt during rehearsal:
`tccutil reset Camera com.visionqc.desktop`.

Web console: sign up (first user) → **create organization** → invite the
demo users with roles. The desktop asks only for email and password and
auto-joins the assigned organization.

Mobile (optional): install the debug APK from
`mobile/build/app/outputs/flutter-apk/app-debug.apk`, keep the phone on the
same Wi-Fi as the laptop.

## Demo script (5 minutes)

| Time | Beat | What to show |
|---|---|---|
| 0:00–0:20 | Problem | Small factories have no vision budget and no defect dataset. VisionQC needs only good photos. |
| 0:20–1:00 | Access model | Web console: organization, invite by email + role, change/remove access. Desktop asks only for email + password; an invited user joins automatically. Mention row-level isolation: every org sees only its own data. |
| 1:00–1:40 | Train | Train on 20–30 good bottle photos. Point out build time (~10 s on CPU) and model versions. |
| 1:40–2:40 | Inspect | Inspect a good unit → PASS, "No unusual areas found." Then a defective unit → FAIL with heatmap on the defect and a plain-language explanation. Show the REVIEW band and Accept/Reject. |
| 2:40–3:20 | Cameras | Register a line camera, live preview. Show the **Mobile pairing** card with QR and 6-digit code. |
| 3:20–4:10 | Phone streaming | Pair the phone, start streaming, show continuous verdicts coming back from the desktop. Note frames stay on the LAN. |
| 4:10–4:40 | KPI + sync | Today's totals, rejection rate, pending reviews. Sync is automatic now — show the status bar ("all synced" / "N pending") and the evidence policy in Settings. |
| 4:40–5:00 | Close | Runs offline, CPU-only, local data. Known limits: transparent objects need a fixed fixture; Windows build pending; generalized segmentation next. |

## Talking points

- **Normal-only training** — no defect images required, which is the core
  difference from supervised inspection tools.
- **Explainable verdicts** — location, area and intensity; never a fabricated
  defect type.
- **Local inference** — frames and models stay on the factory machine. The
  cloud stores accounts, configuration and inspection metadata (optionally
  selected evidence images), not live video.
- **Resilient by design** — SQLite WAL + sync outbox; the desktop keeps
  working offline and syncs later without duplicates (unique org + uid).
- **Honest numbers** — MVTec AD benchmark: mean AUROC 0.976, 90.5% recall at
  the tuned threshold, 0.9% false rejects (see BENCHMARK.md).

## Backup plan

- Pre-trained model already active (Train is fast, but keep it as backup).
- Keep two test images (one good, one defective) in the file picker for the
  upload path if the webcam misbehaves.
- Phone streaming fails → demo the desktop Inspect page instead; pairing card
  still shows the architecture.
- Cloud sync fails → show the offline badge and local KPI; explain the
  outbox retry.

## Known limitations (say them before a judge asks)

- Transparent/reflective objects need a controlled fixture or background
  model; generic segmentation is not yet product-agnostic (Phase E).
- Windows installer not yet built; macOS build is the current artifact.
- iOS build requires a full Xcode installation; Android APK is ready.
- Screw-type micro-defects are the known weak spot of the anomaly model.
