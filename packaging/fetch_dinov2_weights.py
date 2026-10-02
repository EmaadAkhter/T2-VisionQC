#!/usr/bin/env python3
"""Stage timm DINOv2 weights under ``packaging/weights/hf`` for offline builds.

The packaged desktop app must never download model weights at the factory.
This script fills a Hugging Face cache layout that the PyInstaller spec bundles
and ``paths.ensure_model_home()`` copies to a writable location on first run.

Run before ``pyinstaller packaging/visionqc.spec``:
    python3 packaging/fetch_dinov2_weights.py          # default ViT-S/14 (~84 MB)
    python3 packaging/fetch_dinov2_weights.py --all    # also ViT-B/14 (~330 MB)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.backbones import DINOV2_MODELS  # noqa: E402

DEFAULT_ALIASES = ("dinov2_vits14",)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="stage every DINOv2 variant (large bundle)")
    args = parser.parse_args()

    aliases = DINOV2_MODELS.keys() if args.all else DEFAULT_ALIASES
    target = ROOT / "packaging" / "weights" / "hf"
    target.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(target)

    from huggingface_hub import hf_hub_download

    for alias in aliases:
        model_name = DINOV2_MODELS[alias]
        path = hf_hub_download(
            repo_id=f"timm/{model_name}", filename="model.safetensors",
        )
        size_mb = Path(path).stat().st_size / (1024 * 1024)
        print(f"staged {alias}: {path} ({size_mb:.1f} MB)")

    print(f"weights ready under {target}")


if __name__ == "__main__":
    main()