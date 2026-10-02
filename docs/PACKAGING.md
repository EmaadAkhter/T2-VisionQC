# Desktop Packaging

How the VisionQC desktop app is built and shipped. See `PLATFORM_PLAN.md`
(workstream A) for the full task list.

## Decisions

| Topic | Decision | Rationale |
|---|---|---|
| Bundler | PyInstaller **onedir** | Faster cold start than onefile; data/plugin handling is predictable |
| Python | **3.12** in CI | Wheel availability for PySide6/torch; local dev may be newer |
| Torch | **CPU-only** wheels (`download.pytorch.org/whl/cpu`) | No CUDA payload; the app never needs a GPU |
| OpenCV | `opencv-python-headless` in the desktop build | No GUI deps; smaller |
| Weights | `wide_resnet50_2-9ba9bcbe.pth` bundled | Offline first run at the factory; no download at runtime |
| macOS signing | Ad-hoc (`codesign -s -`) for now | No paid Apple certificate yet; documented bypass |
| macOS privacy | `NSCameraUsageDescription` + `NSMicrophoneUsageDescription` in the spec's `info_plist` | Without them macOS kills the process (TCC) on first camera access; CI verifies the keys |
| Detection engine | DINOv2 ViT-S/14 for onboarding sets ≤ 50 images, WideResNet-50 above | DINOv2 generalizes better with few good images; WideResNet is on par and ~3× faster with plenty of data |
| Windows signing | Unsigned for now | Documented SmartScreen bypass; real signing later |
| CI | GitHub Actions matrix `macos-14` + `windows-latest` | Free for public repos |
| Artifacts | Zips (14-day retention) + Release assets on `v*` tags | Manual builds + versioned releases |

## Data and configuration locations

The packaged app never writes inside its own bundle:

| Item | Location |
|---|---|
| Data directory | `VISIONQC_DATA_DIR` → repo `data/` in dev → OS user-data dir |
| macOS user data | `~/Library/Application Support/VisionQC/` |
| Windows user data | `%APPDATA%\VisionQC\` |
| Linux user data | `~/.local/share/visionqc/` |
| Config | `<data>/config.json` (written by the first-run dialog) |
| Models | `<data>/models/` |
| Evidence images | `<data>/images/` |
| SQLite | `<data>/visionqc.db` |
| Logs | `<data>/logs/` |
| Bundled weights | `<bundle>/torch_home/hub/checkpoints/` via `TORCH_HOME` |
| Bundled DINOv2 | `<bundle>/hf_home/hub/models--timm--*` via `HF_HOME` (staged into `<data>/hf_home` on first run) |

Configuration resolution order: environment variables → user `config.json` →
repo `desktop/config.local.json` (dev) → bundled `config.default.json` →
first-run dialog.

## Local build

```bash
pip install -r requirements-desktop.txt
pip install pyinstaller==6.22.3

# Stage the bundled weights (git-ignored)
mkdir -p packaging/weights/torch_home/hub/checkpoints
cp ~/.cache/torch/hub/checkpoints/wide_resnet50_2-9ba9bcbe.pth \
   packaging/weights/torch_home/hub/checkpoints/

# Stage DINOv2 ViT-S/14 (~84 MB) for the offline default engine
python3 packaging/fetch_dinov2_weights.py

# Regenerate the app icon if needed (macOS)
python3 packaging/make_icon.py

pyinstaller --clean --noconfirm packaging/visionqc.spec

# Verify without a display
dist/VisionQC.app/Contents/MacOS/VisionQC --version   # macOS
dist/VisionQC.app/Contents/MacOS/VisionQC --smoke
dist/VisionQC.app/Contents/MacOS/VisionQC --selftest

# Build the DMG (macOS)
codesign --force --deep --sign - dist/VisionQC.app
chmod +x packaging/make_dmg.sh
packaging/make_dmg.sh dist/VisionQC.app VisionQC-macos-arm64.dmg
```

Outputs: `dist/VisionQC.app` (real macOS application bundle, icon and
metadata included), `dist/VisionQC/` (the same app as a plain onedir folder)
and the DMG with a drag-to-Applications layout.

## CI

`.github/workflows/desktop-build.yml`:

- **Triggers:** manual (`workflow_dispatch`), tags `v*`, pushes/PRs touching
  `desktop/`, `service/`, `db/`, `packaging/`, `paths.py` or
  `requirements-desktop.txt`.
- **Matrix:** `macos-14` (arm64) and `windows-latest` (x64).
- **Steps:** install deps → cache/fetch weights → PyInstaller → `--version` +
  `--smoke` → zip → upload artifact. Tags additionally attach the zips to a
  GitHub Release.
- **Smoke check:** exit code of `VisionQC --smoke` (constructs every window
  offscreen; no network, no display).

## Installing the artifact

**macOS (DMG)**

1. Download `VisionQC-macos-arm64.dmg` from the GitHub Release or the Actions
   run artifacts.
2. Open the DMG and drag **VisionQC** onto the **Applications** shortcut.
3. First launch is blocked by Gatekeeper (unsigned build): right-click the app
   → **Open**, or run
   `xattr -dr com.apple.quarantine /Applications/VisionQC.app`.
4. The first time you open a camera (Inspect page or Cameras preview), macOS
   asks for camera access. **Allow it.** If it was denied earlier, enable
   VisionQC under **System Settings → Privacy & Security → Camera**, or reset
   the prompt from a terminal:
   `tccutil reset Camera com.visionqc.desktop`
5. The app asks for the server URL and anon key only on the very first run.
   After one successful sign-in it opens straight into the last organization
   on every later launch — online or offline.

**Windows**

1. Unzip `VisionQC-windows-x64.zip`.
2. SmartScreen warns about an unsigned app: **More info → Run anyway**.
3. Same first-run dialog and seamless relaunch behaviour.

## Size budget

| Artifact | Budget |
|---|---|
| `dist/VisionQC/` unpacked | ≤ 2.2 GB |
| Zip download | ≤ 1.2 GB |

If the budget is exceeded: trim the excludes list in `packaging/visionqc.spec`
first, then consider dropping `torchvision` by loading the backbone via
`torch.hub`-free code paths.

## Deferred

- Apple Developer ID signing + notarization, Windows code signing.
- Intel-mac job (`macos-13`) if needed.
- Auto-update mechanism.
