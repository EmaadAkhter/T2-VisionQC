"""Download MVTec AD categories from the Voxel51 HuggingFace mirror.

Reconstructs the standard MVTec directory structure:
    <root>/<category>/train/good/*.png
    <root>/<category>/test/<defect_type>/*.png
    <root>/<category>/ground_truth/<defect_type>/*_mask.png

Usage:
    python3 tests/fetch_mvtec_hf.py bottle cable capsule
    python3 tests/fetch_mvtec_hf.py --all
"""

import os
import sys
import json
import argparse
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

HF_BASE = "https://huggingface.co/datasets/Voxel51/mvtec-ad/resolve/main/"
SAMPLES_URL = HF_BASE + "samples.json"
ALL_CATEGORIES = [
    "bottle", "cable", "capsule", "carpet", "grid", "hazelnut", "leather",
    "metal_nut", "pill", "screw", "tile", "toothbrush", "transistor",
    "wood", "zipper",
]
DEFAULT_ROOT = os.path.join(os.path.dirname(__file__), "..", "data", "mvtec_hf")


def fetch(url: str, dest: str, retries: int = 3):
    """Download one file with retries and a timeout."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    last_err = None
    for _ in range(retries):
        try:
            tmp = dest + ".tmp"
            req = urllib.request.Request(url, headers={"User-Agent": "visionqc"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 64)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dest)
            return dest
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"Failed to download {url}: {last_err}")


def load_samples():
    req = urllib.request.Request(SAMPLES_URL, headers={"User-Agent": "visionqc"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))["samples"]


def plan_category(samples, category: str, root: str):
    """Return list of (url, dest) download tasks for one category."""
    cat_root = os.path.join(root, category)
    tasks = []
    n = {"train": 0, "test_good": 0, "test_defect": 0, "masks": 0}

    for s in samples:
        if s.get("category", {}).get("label") != category:
            continue
        split = s["split"]
        defect = s["defect"]["label"]
        src = s["filepath"]
        name = os.path.basename(src)

        if split == "train":
            dest = os.path.join(cat_root, "train", "good", name)
            n["train"] += 1
        elif defect == "good":
            dest = os.path.join(cat_root, "test", "good", name)
            n["test_good"] += 1
        else:
            dest = os.path.join(cat_root, "test", defect, name)
            n["test_defect"] += 1

        tasks.append((HF_BASE + src, dest))

        if "defect_mask" in s and defect != "good":
            stem = name.rsplit(".", 1)[0]
            mask_dest = os.path.join(cat_root, "ground_truth", defect,
                                     f"{stem}_mask.png")
            tasks.append((HF_BASE + s["defect_mask"]["mask_path"], mask_dest))
            n["masks"] += 1

    return tasks, n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("categories", nargs="*", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    categories = ALL_CATEGORIES if args.all else (args.categories or ["bottle"])
    root = os.path.abspath(args.root)

    print("Fetching MVTec AD sample index...")
    samples = load_samples()
    print(f"  {len(samples)} samples in index")

    for category in categories:
        tasks, n = plan_category(samples, category, root)
        if not tasks:
            print(f"[{category}] not found in mirror, skipping")
            continue

        done = 0
        print(f"[{category}] {n['train']} train, {n['test_good']} test-good, "
              f"{n['test_defect']} test-defect, {n['masks']} masks")

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(fetch, url, dest) for url, dest in tasks]
            for fut in as_completed(futures):
                fut.result()
                done += 1
                if done % 50 == 0 or done == len(tasks):
                    print(f"  {done}/{len(tasks)}", end="\r", flush=True)
        print(f"  done: {len(tasks)} files -> {os.path.join(root, category)}")

    print("\nAll requested categories downloaded.")
    print(f"Root: {root}")


if __name__ == "__main__":
    main()
