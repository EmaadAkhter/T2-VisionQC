"""Checks for the user-supplied eight-image segmentation POC set."""

import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "data" / "poc"


def test_manifest_has_eight_distinct_images():
    manifest = json.loads((POC / "manifest.json").read_text())
    images = manifest["images"]
    assert len(images) == 8
    assert len({item["id"] for item in images}) == 8
    assert len({item["file"] for item in images}) == 8
    for item in images:
        path = POC / item["file"]
        assert path.is_file(), f"Missing POC image: {path}"
        with Image.open(path) as image:
            image.verify()


def test_user_component_labels_are_preserved():
    manifest = json.loads((POC / "manifest.json").read_text())
    images = manifest["images"]
    assert all(x["condition"] == "good" for x in images[:5])
    assert images[5]["cap_present"] is False
    assert images[5]["sticker_present"] is True
    assert images[6]["cap_present"] is True
    assert images[6]["sticker_present"] is False
    assert images[7]["cap_present"] is False
    assert images[7]["sticker_present"] is False
