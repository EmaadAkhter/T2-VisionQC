# PyInstaller spec for the VisionQC desktop app.
# Build:  pyinstaller --clean --noconfirm packaging/visionqc.spec
# Output: dist/VisionQC/ (onedir, run the VisionQC executable inside)

from pathlib import Path
import re
import sys

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent

# Single source of truth for the version (desktop/__init__.py).
_version_match = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (ROOT / "desktop" / "__init__.py").read_text(encoding="utf-8"),
)
VERSION = _version_match.group(1) if _version_match else "0.0.0"

datas = []
binaries = []

# Bundled torch weights (offline first run). Optional for source builds.
weights_dir = ROOT / "packaging" / "weights" / "torch_home"
if weights_dir.exists():
    datas.append((str(weights_dir), "torch_home"))

# Bundled Hugging Face cache with timm DINOv2 weights (offline first run).
hf_weights_dir = ROOT / "packaging" / "weights" / "hf"
if hf_weights_dir.exists():
    datas.append((str(hf_weights_dir), "hf_home"))

# Optional bundled default configuration.
default_config = ROOT / "packaging" / "config.default.json"
if default_config.exists():
    datas.append((str(default_config), "."))

hiddenimports = []
for package in (
    "uvicorn",
    "fastapi",
    "starlette",
    "anyio",
    "httpx",
    "httpcore",
    "h11",
    "websockets",
    "postgrest",
    "supabase",
    "gotrue",
    "storage3",
    "realtime",
    "qrcode",
    "PIL",
    "timm",
    "huggingface_hub",
    "safetensors",
):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:  # package not installed in this environment
        pass

# torchvision registers custom ops (torchvision::nms) in its compiled
# extension; PyInstaller's hook does not reliably collect them for recent
# torchvision releases, so collect the whole package explicitly.
try:
    tv_datas, tv_binaries, tv_hidden = collect_all("torchvision")
    datas += tv_datas
    binaries += tv_binaries
    hiddenimports += tv_hidden
except Exception:  # noqa: BLE001
    hiddenimports += ["torchvision._C", "torchvision.extension"]
    try:
        binaries += collect_dynamic_libs("torchvision")
    except Exception:  # noqa: BLE001
        pass

datas += collect_data_files("qrcode")

excludes = [
    "tkinter",
    "matplotlib",
    "pandas",
    "streamlit",
    "plotly",
    "pytest",
    "IPython",
    "notebook",
    # NOTE: do not exclude torchvision submodules — the package imports
    # datasets/models eagerly and the app fails at runtime with ImportError.
]

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VisionQC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    # Never show a blocking error dialog in CI/headless runs; exit with the
    # error code instead.
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VisionQC",
)

# Real macOS application bundle (icon, metadata, proper activation).
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="VisionQC.app",
        icon=str(ROOT / "packaging" / "icon.icns"),
        bundle_identifier="com.visionqc.desktop",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "VisionQC",
            "CFBundleName": "VisionQC",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "12.0",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            # Required by macOS TCC: without these keys the process is killed
            # the moment OpenCV/AVFoundation touches the camera.
            "NSCameraUsageDescription": (
                "VisionQC uses the camera to capture product images for "
                "visual inspection and anomaly detection. Images are "
                "processed locally on this machine."
            ),
            "NSMicrophoneUsageDescription": (
                "VisionQC may access the microphone only if a video source "
                "requires it; audio is never recorded or uploaded."
            ),
        },
    )
