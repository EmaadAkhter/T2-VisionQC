# Platform Plan — Packaging, Sync, Generalization

Deep step-by-step plan for the three workstreams, in the agreed order:

1. **A. Desktop packaging via GitHub Actions** (macOS + Windows artifacts)
2. **B. Sync** (automatic, resilient, evidence-aware)
3. **C. Generalization** (any product, user-approved mask at setup, automatic runtime)

Status legend: `[ ]` not started · each step has an explicit **done when**.

---

# Workstream A — Desktop packaging via GitHub Actions

## A0. Decisions and constraints (freeze before coding)

| Decision | Choice | Why |
|---|---|---|
| Bundler | PyInstaller **onedir** (not onefile) | Faster startup, easier plugin/data handling |
| Python | **3.12** in CI | Bleeding-edge 3.14 risks wheel gaps for PySide6/torch |
| Torch wheels | **CPU-only** (`download.pytorch.org/whl/cpu`) | Avoids ~2 GB CUDA payload |
| OpenCV | **opencv-python-headless** in the desktop build | No GUI deps needed; smaller |
| Weights | Bundle `wide_resnet50_2-9ba9bcbe.pth` (275 MB) | Offline first run, no download at the factory |
| macOS signing | Ad-hoc `codesign -s -` | No paid Apple cert yet; document Gatekeeper bypass |
| Windows signing | Unsigned | Document SmartScreen bypass; real signing deferred |
| CI triggers | `workflow_dispatch`, tags `v*`, PRs touching `desktop/**` | Manual + release builds |
| Artifacts | Zip per OS, 14-day retention; Release assets on tags | Free for public repo |
| Size budget | onedir ≤ 2.2 GB, zip ≤ 1.2 GB | Track in CI output |

**Steps**
- [ ] A0.1 Record the decisions above in `docs/PACKAGING.md`.
- [ ] A0.2 Create `requirements-desktop.txt` with **pinned** versions (PySide6, supabase, fastapi, uvicorn, qrcode, httpx, opencv-python-headless, numpy, Pillow, torch, torchvision) and a comment pointing at the CPU index URL.
- **Done when:** a fresh venv installs from it and `python -m desktop.main` starts.

## A1. Make the code packaging-ready

The current code assumes the repo layout (`ROOT/data`, `desktop/config.local.json`). A packaged app is read-only, so paths and config must move to user-writable locations.

- [ ] A1.1 Add `desktop/paths.py`:
  - `app_data_dir()`: `VISIONQC_DATA_DIR` env → `~/Library/Application Support/VisionQC` (mac) / `%APPDATA%\VisionQC` (win) / `~/.local/share/visionqc` (linux)
  - `models_dir()`, `images_dir()`, `db_path()`, `session_path()`, `config_path()`
  - Create dirs on first use.
- [ ] A1.2 Refactor path users to `paths.py`: `db/database.py`, `desktop/model_store.py`, `desktop/auth.py` (session), `desktop/config.py`.
  - Keep backward compatibility: if the old repo `data/` exists, keep using it (dev), else use the user dir.
- [ ] A1.3 Config resolution order: `VISIONQC_SUPABASE_URL/ANON_KEY` env → user config file → bundled `config.default.json` → first-run dialog.
- [ ] A1.4 First-run dialog (in `desktop/main.py`): fields for Supabase URL + anon key, "Test connection" button, "Save". Explains where to get values (edge setup guide).
- [ ] A1.5 Weights: bundle `wide_resnet50_2-*.pth`; at startup set `TORCH_HOME` to a directory containing it (bundle read-only is fine). Never trigger a download in the packaged app; show a clear error if weights are missing.
- [ ] A1.6 Add `desktop/launcher.py`:
  - `--smoke`: set `QT_QPA_PLATFORM=offscreen`, construct `QApplication` + `MainWindow` with a fake session, exit 0.
  - `--version`: print version and exit.
  - `--data-dir`: override data directory.
- [ ] A1.7 Version constant in `desktop/__init__.py` (`__version__ = "0.3.0"`), shown in the window title and `--version`.
- **Done when:** `python -m desktop.launcher --smoke` passes on macOS with the repo `data/` removed and a temp `VISIONQC_DATA_DIR`.

## A2. PyInstaller spec

- [ ] A2.1 `packaging/visionqc.spec`:
  - Entry: `desktop/launcher.py`
  - `datas`: bundled weights, `qrcode` data, any assets, `config.default.json`
  - `hiddenimports`/`collect_all`: `uvicorn` (loops, protocols), `fastapi`, `starlette`, `postgrest`, `supabase`, `httpx`, `httpcore`, `h11`, `anyio`, `qrcode`, `PIL`, `cv2`, `torch`, `torchvision`
  - `excludes`: `tkinter`, `matplotlib`, `pandas`, `streamlit`, `plotly`, `torchvision.datasets`, `torchvision.models.detection` (desktop doesn't use Mask R-CNN/DeepLab), test packages
  - `console=False` for the GUI; keep a debug console build target for CI smoke output
- [ ] A2.2 Build locally on macOS and fix missing-module errors iteratively (`pyinstaller --clean packaging/visionqc.spec`).
- [ ] A2.3 Verify the built app: launch, sign in against local Supabase, train, inspect — with the repo hidden from the working directory.
- [ ] A2.4 Record the onedir/zip sizes and startup time (cold start target < 15 s on an M-series laptop).
- **Done when:** `dist/VisionQC.app` (or onedir folder) runs standalone on a clean macOS user account.

## A3. GitHub Actions workflow

- [ ] A3.1 `.github/workflows/desktop-build.yml`:
  - `on: workflow_dispatch`, `push: tags: ['v*']`, `pull_request: paths: ['desktop/**','service/**','db/**','packaging/**','requirements-desktop.txt']`
  - Matrix: `macos-14` (arm64) and `windows-latest` (x64)
  - Steps: checkout → `actions/setup-python@v5` (3.12) → `actions/cache` for pip and the torch hub checkpoint (keyed by pinned versions + weight hash) → install `requirements-desktop.txt` + pinned PyInstaller → ensure weights (download once, cached; fail if missing) → run spec → **smoke run** (`--smoke`, offscreen on mac; `QT_QPA_PLATFORM=offscreen` equivalent on Windows) → zip artifact → print sizes → `actions/upload-artifact@v4`
- [ ] A3.2 Release job (tag only): download both artifacts, attach to a GitHub Release with auto notes and SHA256 checksums.
- [ ] A3.3 Add a `packaging/README.md` with per-OS install steps:
  - macOS: right-click → Open, or `xattr -dr com.apple.quarantine VisionQC.app`
  - Windows: More info → Run anyway; note SmartScreen
  - First-run config instructions
- [ ] A3.4 Add a status badge to the repo README.
- [ ] A3.5 Backend CI job (used by workstream B tests): `.github/workflows/backend-tests.yml` — start Supabase CLI (`supabase start`), run `tools/test_rls.py` and `tools/test_sync.py`, plus `pytest tests/test_core.py`.
- **Done when:** a manual workflow run produces downloadable macOS + Windows artifacts, both pass the smoke step, and the badge is green.

## A4. Acceptance criteria — Workstream A

- [ ] Workflow green on both runners from a clean checkout (no local caches).
- [ ] Download → extract → launch with **no Python, no browser, no repo** on a clean machine.
- [ ] First-run dialog saves config; app reaches the login screen against local or hosted Supabase.
- [ ] Offline first run (no internet): app launches, explains that weights are bundled, allows local-only work.
- [ ] Smoke step runs in CI in under 60 s per OS.
- [ ] Artifact sizes within budget; checksums published on releases.
- [ ] Known limitations documented: unsigned builds, Intel-mac build deferred (add `macos-13` job when needed).

## A5. Risks and mitigations

| Risk | Mitigation |
|---|---|
| PyInstaller misses a hidden import | `--smoke` in CI catches it before release; iterative `collect_all` list |
| Torch bundle too large / slow upload | CPU wheels, excludes, measure; if > budget, drop `torchvision` model zoo and keep only what inference uses |
| macOS Gatekeeper blocks testers | Ad-hoc signing + documented bypass; plan a real Developer ID cert before customer pilots |
| CI minutes/cache misses | Cache pip + weights; public repo minutes are free |
| User data path changes break dev flow | Backward-compatible `paths.py` (repo `data/` still wins in dev) |

---

# Workstream B — Sync

## B0. Current state and gaps

Working today: manual "Sync to cloud" button, idempotent upsert by `(org_id, uid)`, SQLite `synced` flag, 36/36 verified push with zero duplicates.

Gaps to close:
1. No automatic/background sync, no retry/backoff.
2. Disposition changes after sync are not propagated.
3. UID collision risk across two edge stations (`INS-YYYYMMDD-NNNN` is per-machine).
4. No evidence-image upload (only metadata).
5. Settings and model metadata are local-only.
6. Cameras/lines/products require cloud (no offline creation queue).
7. No sync status beyond a manual label; no error detail or retry schedule.
8. No automated sync tests in CI.

## B1. Local schema upgrades (SQLite)

- [ ] B1.1 Add columns to `inspections`: `station_id TEXT`, `local_revision INTEGER DEFAULT 0`, `synced_at TEXT`, `sync_attempts INTEGER DEFAULT 0`, `last_sync_error TEXT`, `next_retry_at TEXT`, `evidence_synced INTEGER DEFAULT 0`.
- [ ] B1.2 Increment `local_revision` in `update_disposition()` and any mutation (note edits).
- [ ] B1.3 `station_id`: generated once per install (`<hostname-short>-<4 random>`), stored in `settings`; new UIDs become `INS-YYYYMMDD-NNNN-<station>`.
- [ ] B1.4 Backfill migration for existing rows: station `default`, revision 0, evidence_synced 0.
- [ ] B1.5 New helpers: `get_pending_sync(limit)`, `mark_synced(ids, at)`, `mark_sync_failed(id, error, next_retry_at)`, `count_by_sync_state()`.
- **Done when:** unit tests cover the state transitions on a temp DB.

## B2. Cloud schema upgrades (Supabase migrations)

- [ ] B2.1 `inspections`: add `station_id TEXT`, `updated_at timestamptz default now()`, `revision INTEGER DEFAULT 0`; keep unique `(org_id, uid)`.
- [ ] B2.2 Trigger: bump `updated_at`/`revision` on update.
- [ ] B2.3 New table `edge_stations` (id, org_id, name, last_seen_at, app_version) with RLS (members read; operator+ upsert own station).
- [ ] B2.4 Storage: confirm policy allows `{org_id}/{camera_id}/{uid}_overlay.png`; add size/type guidance (JPEG/PNG ≤ 2 MB).
- [ ] B2.5 RLS test additions: station upsert allowed for operator, cross-org denied.
- **Done when:** `supabase db reset` applies cleanly and `tools/test_rls.py` still passes (extended count).

## B3. Sync engine (`desktop/sync.py`)

- [ ] B3.1 `SyncEngine(QObject)` with:
  - triggers: timer (default 60 s), post-inspection debounce (5 s), manual `sync_now()`, network-recovery kick
  - exponential backoff on failure: 30 s → 1 m → 2 m → 5 m → 15 m cap, ±20% jitter; reset on success
  - single-flight guard (no overlapping runs)
- [ ] B3.2 Push algorithm:
  1. select rows where `synced = 0 OR local_revision > cloud revision`
  2. upsert in batches of 200 (`on_conflict=org_id,uid`, `ignore_duplicates=false` so updates land)
  3. mark succeeded rows synced with `synced_at`; failures get attempts/error/next_retry
  4. emit progress signal (`pushed`, `failed`, `remaining`)
- [ ] B3.3 Station registration: upsert `edge_stations` on startup and after each sync; store `last_seen_at`, app version.
- [ ] B3.4 Offline detection: on `URLError`/timeout → mark engine offline, keep queueing, schedule retry; distinguish auth errors (surface immediately, pause sync until re-login).
- [ ] B3.5 Logging: append-only sync log file (`sync.log`) with batch outcomes for support.
- **Done when:** simulated 36 h offline queue drains with zero duplicates and correct dispositions.

## B4. Evidence uploads

- [ ] B4.1 Org-level setting `evidence_policy`: `none` (default) | `overlay` | `original+overlay`; stored in `settings`, editable in desktop Settings page.
- [ ] B4.2 Upload path: `{org_id}/{camera_id}/{uid}_overlay.png` and `_original.png`; JPEG q80 for originals > 1 MB.
- [ ] B4.3 Upload after metadata batch; failures retried independently and do not block metadata (`evidence_synced` flag).
- [ ] B4.4 Cloud `inspections` rows get storage paths (`image_path`, `overlay_path` as storage keys, not local paths) when uploaded.
- [ ] B4.5 UI: per-inspection evidence state in KPI table (local only / uploaded / pending).
- **Done when:** policy `none` uploads zero images; `overlay` uploads only overlays; storage RLS verified cross-org.

## B5. Settings and model metadata sync

- [ ] B5.1 Push `settings` (threshold, delta, active model version) on change; pull on login with cloud as org policy; local edits allowed with a "differs from cloud" note.
- [ ] B5.2 Push `models` metadata rows (version, n_images, ref_score, backbone, metrics) — metadata only, never weights.
- [ ] B5.3 Model activation: record `activated_by` + `activated_at` in cloud for audit.
- **Done when:** changing threshold on desktop A appears on desktop B after login/sync.

## B6. UI integration

- [ ] B6.1 Status bar: state dot (idle/syncing/offline/error), pending count, last success time; tooltip with next retry.
- [ ] B6.2 KPI page: sync panel (last success, next retry, failures with messages, "Retry now"), evidence policy selector.
- [ ] B6.3 Settings page: station name/ID display, sync interval, evidence policy, "Reset sync state" (danger, confirmation).
- **Done when:** pulling the network visibly moves the app to offline state and back without user action.

## B7. Multi-station safety

- [ ] B7.1 Two-station simulation test: two SQLite DBs, same org, overlapping timestamp ranges; sync both; assert no overwrites (station-suffixed UIDs) and correct counts.
- [ ] B7.2 Document the UID format and collision rule in `docs/SYNC.md`.
- **Done when:** the simulation passes and cloud counts equal the sum of both stations.

## B8. Tests and acceptance — Workstream B

Automated (`tools/test_sync.py`, runs in the backend CI job):
- [ ] duplicate push (same batch twice) → no duplicates
- [ ] disposition updated after sync → cloud reflects the new disposition
- [ ] failure injection (mock HTTP 500) → backoff schedule, then success drains queue
- [ ] offline 36 h simulation → all rows arrive exactly once
- [ ] evidence policy none/overlay → storage object counts match
- [ ] two-station UID safety
- [ ] settings push/pull round-trip
- [ ] station upsert + last_seen

Acceptance:
- [ ] Killing the network mid-sync never loses a committed inspection.
- [ ] No duplicate `(org_id, uid)` rows after any retry sequence.
- [ ] Sync resumes automatically after network recovery without user action.
- [ ] Evidence policy is honored and visible in the UI.
- [ ] All tests green in CI.

---

# Workstream C — Generalization (any product, no code changes)

## C0. Principles

1. **Nothing product-specific in code**: no class names, no colors, no part names, no fixed boxes.
2. **Approval at setup only**: the operator approves the canonical mask once per camera view; runtime is fully automatic.
3. **Canonical region scoring**: score inside the *expected* product region, not the *detected* region — otherwise a missing part shrinks the mask and hides its own defect.
4. **Foundation models are setup-time helpers only** (mask proposal); the runtime path uses the lightweight local pipeline.

## C1. Product profile model

- [ ] C1.1 SQLite `product_profiles`: id, product_id, camera_id, name, canonical_mask_path, background_model_path, orientation_ref JSON, threshold, delta, model_version, status (draft/active), created_at.
- [ ] C1.2 Cloud `profiles` table mirror (metadata only) + RLS (quality_manager+ write).
- [ ] C1.3 `settings` gains `active_profile_id` per (org, product, camera).
- **Done when:** profiles can be created, listed, activated, deleted locally and mirrored to cloud.

## C2. Onboarding wizard (desktop UI, ≤10 minutes)

- [ ] C2.1 Step 1 — Profile: name, product, camera, view label.
- [ ] C2.2 Step 2 — Background (optional but recommended): capture 5–10 empty-scene frames; build background model (median + per-pixel std).
- [ ] C2.3 Step 3 — Good units: capture/import 20–30 good images (reuse Train page capture).
- [ ] C2.4 Step 4 — Mask proposal (see C3): show overlay + editable mask.
- [ ] C2.5 Step 5 — Approval: brush add/remove + polygon fill + undo; "Approve mask" required to continue.
- [ ] C2.6 Step 6 — Build: train PatchCore on the approved region (full frames, masked scoring), compute reference score, store profile; activate.
- [ ] C2.7 Re-run/repair flow: re-open wizard for an existing profile (re-capture background, re-approve mask, rebuild).
- **Done when:** a new product + camera can be onboarded end-to-end without touching code or files by hand.

## C3. Mask proposal engine (`service/foreground.py`)

- [ ] C3.1 Background model: `build_from_frames(frames)` → median image + std map; `foreground_mask(frame)` → `|frame - median| > k·std` (k default 3, tuned at setup), morphological open/close, fill holes, keep components above 1% area.
- [ ] C3.2 Proposal: union of foreground masks across the good frames, intersected with a loose bounding region; optional SAM refinement (setup-time helper, loaded only in the wizard, never bundled in the runtime path).
- [ ] C3.3 Fallback when no background frames exist: propose from the good-set variance (regions that are stable across good units) + manual polygon.
- [ ] C3.4 Quality metrics (computed at setup and on demand):
  - mask IoU vs approved mask on held-out good frames
  - background false activation: fraction of foreground outside the approved mask
  - stability: per-frame mask area variance
- [ ] C3.5 Orientation reference: canonical mask centroid + principal axis; store; runtime alignment optional (fixed-camera default off).
- **Done when:** metrics are computed for the 8-image POC set and reported in the wizard.

## C4. Runtime pipeline changes

- [ ] C4.1 Scoring mask: patches scored only inside the canonical mask (dilated by 2% to include edges); heatmap/explanation limited to the same region.
- [ ] C4.2 Adaptive mask used only for alignment/health, never for cropping the scored region.
- [ ] C4.3 "No product" state: if adaptive foreground coverage < 2% of the canonical region for N frames → `No product / Invalid capture` (never PASS/FAIL).
- [ ] C4.4 Setup status (brightness/blur/alignment) compared against the profile's baselines instead of global defaults.
- [ ] C4.5 Multi-view: one profile per (camera, view); Inspect page resolves profile by active camera.
- **Done when:** the full pipeline runs from a profile with no hardcoded product knowledge.

## C5. Component-agnostic part checks (v1.1, after C4 passes)

- [ ] C5.1 From the good set, learn "presence regions": sub-regions of the canonical mask that are foreground in ≥95% of good frames.
- [ ] C5.2 Runtime: if a presence region's foreground coverage drops below the learned lower bound → missing-part anomaly with location/area explanation (no color rules).
- [ ] C5.3 Validate on images 6–8 (cap missing, sticker missing, both) using the anomaly signal + presence regions only.
- **Done when:** missing parts are flagged on 3/3 labelled examples without any color/part heuristics.

## C6. Migration and validation on the existing bottle flow

- [ ] C6.1 Create a bottle profile from the 8 POC images + MVTec bottle training frames (background from MVTec good frames if no empty scene).
- [ ] C6.2 Run the 8-image smoke set: expected — 5 good PASS/REVIEW, 3 missing-part defects flagged (FAIL or REVIEW), background-only frame → No product.
- [ ] C6.3 Keep the old flow as fallback when no profile exists; document the difference.
- [ ] C6.4 Report mask IoU vs the approved mask and background false activation in `data/poc/GENERALIZATION_RESULTS.md`.
- **Done when:** results are recorded with honest numbers and the fallback path is regression-tested.

## C7. Tests and acceptance — Workstream C

Automated:
- [ ] unit: background model, mask cleanup, metric computation, presence regions
- [ ] integration: onboard **a different object type** (e.g., MVTec `metal_nut` or a box) with zero code changes; measure IoU vs approved mask on held-out frames
- [ ] regression: existing unit suite + MVTec benchmark still pass

Acceptance:
- [ ] New product + camera onboarded in ≤10 minutes, no code changes.
- [ ] Median mask IoU ≥0.90 vs the approved mask; every approved pose ≥0.80.
- [ ] Background false activation ≤5% of the canonical region area.
- [ ] Missing-part examples (images 6–8) flagged without color rules.
- [ ] Background-only frames produce No product, never PASS/FAIL.
- [ ] Runtime stays within the latency budget (≤500 ms median on the target laptop).

## C8. Risks — Workstream C

| Risk | Mitigation |
|---|---|
| Transparent objects still blend into the background | Prefer controlled backdrop/backlight at setup; background model handles edges/refraction; document fixture requirement |
| Background drift over a shift | Rebuild background from a rolling median in wizard only; setup check warns on drift; calibration re-run |
| Mask editing UX is slow to build | Start with brush + polygon + undo; skip fancy tools |
| SAM dependency/licence | Setup-time only, optional; app works without it; verify licence before bundling |
| Existing bottle model regressions | Fallback path + regression suite |

---

# Execution order and milestones

| Milestone | Content | Exit |
|---|---|---|
| M1 | A0–A3 (paths, launcher, spec, workflow) | CI produces macOS + Windows artifacts; smoke green |
| M2 | A4–A5 (release job, docs, badge) | Tag `v0.3.0` publishes signed-off (ad-hoc) artifacts |
| M3 | B1–B3 (schema, engine, station) | 36 h offline simulation passes locally |
| M4 | B4–B8 (evidence, settings, UI, tests, CI job) | Backend CI green; sync acceptance met |
| M5 | C1–C2 (profiles + wizard) | New product onboarded with approved mask |
| M6 | C3–C4 (foreground engine + runtime) | Metrics computed; runtime profile-driven |
| M7 | C5–C7 (parts, migration, tests) | 8-image acceptance + new-object integration test |

Dependencies: M3 depends on M1 only for CI convenience (sync tests can run locally first). M5 can start in parallel with M4 once B1 lands (profiles reuse the settings/outbox patterns).

## First 48 hours (concrete)

1. A0.2 + A1.1–A1.3: `requirements-desktop.txt`, `paths.py`, config resolution.
2. A1.6: `launcher.py --smoke` + local PyInstaller trial.
3. A3.1: push workflow, trigger a manual run, fix hidden-import errors until green.
4. B1.1–B1.5: SQLite sync columns + helpers + unit tests.

## Open items for you

1. Approve unsigned/ad-hoc builds for now (real Apple/Windows certificates later), or provide certificates.
2. Confirm the evidence default stays **none** (metadata only) until you enable uploads.
3. Confirm macOS arm64 + Windows x64 as the first two targets (Intel mac deferred).
