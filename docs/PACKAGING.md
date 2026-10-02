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

pyinstaller --clean --noconfirm packaging/visionqc.spec

# Verify without a display
dist/VisionQC/VisionQC --version
dist/VisionQC/VisionQC --smoke
```

Output: `dist/VisionQC/` (run `dist/VisionQC/VisionQC`). The build is
git-ignored.

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

**macOS**

1. Unzip `VisionQC-macos-arm64.zip`.
2. First launch is blocked by Gatekeeper (unsigned): right-click the app →
   **Open**, or run
   `xattr -dr com.apple.quarantine /path/to/VisionQC.app`.
3. The first-run dialog asks for the Supabase URL and anon key.

**Windows**

1. Unzip `VisionQC-windows-x64.zip`.
2. SmartScreen warns about an unsigned app: **More info → Run anyway**.
3. Same first-run dialog.

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
