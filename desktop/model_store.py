"""Shared model and inspection helpers for the desktop pages."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from db import database as db
from service.inference import (
    PatchCoreModel,
    compute_certainty,
    compute_setup_status,
    compute_verdict,
    create_overlay,
    generate_explanation,
)

ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT / "data" / "images"
MODELS_DIR = ROOT / "data" / "models"


class ModelStore:
    """Loads and caches the active local model."""

    def __init__(self):
        self._model: PatchCoreModel | None = None
        self._version: str | None = None

    def active_version(self) -> str | None:
        return db.get_settings().get("active_model_version")

    def get(self) -> PatchCoreModel | None:
        version = self.active_version()
        if not version:
            self._model = None
            self._version = None
            return None
        if self._model is not None and self._version == version:
            return self._model
        meta = db.get_model(version)
        if not meta or not meta.get("model_path"):
            return None
        path = meta["model_path"]
        if not os.path.exists(path):
            return None
        model = PatchCoreModel()
        model.load(path)
        self._model = model
        self._version = version
        return model


def run_inspection(image: np.ndarray, model: PatchCoreModel,
                   settings: dict) -> dict:
    """Full local pipeline: predict, verdict, certainty, explanation, overlay."""
    start = time.time()
    prediction = model.predict(image)
    score = prediction["normalized_score"]
    threshold = settings.get("threshold", 0.46)
    delta = settings.get("delta", 0.05)

    verdict = compute_verdict(score, threshold, delta)
    setup_status, setup_reasons = compute_setup_status(image, model.training_stats)
    explanation = generate_explanation(
        prediction["anomaly_map"], threshold, ref_score=model.ref_score
    )
    certainty, certainty_reason = compute_certainty(
        score, verdict, threshold, delta, setup_status, explanation["area_pct"]
    )
    overlay = create_overlay(image, prediction["anomaly_map"], threshold)

    return {
        "raw_score": prediction["raw_score"],
        "score": score,
        "threshold": threshold,
        "delta": delta,
        "verdict": verdict,
        "certainty": certainty,
        "certainty_reason": certainty_reason,
        "explanation": explanation["explanation"],
        "region_label": explanation["region_label"],
        "area_pct": explanation["area_pct"],
        "n_regions": explanation["n_regions"],
        "setup_status": setup_status,
        "setup_reasons": setup_reasons,
        "overlay": overlay,
        "latency_ms": int((time.time() - start) * 1000),
    }


def log_inspection(result: dict, image: np.ndarray, model_version: str,
                   source: str = "edge") -> str:
    """Persist one inspection locally (SQLite + image evidence)."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    uid = db.generate_inspection_uid()
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    image_path = IMAGES_DIR / f"{uid}_original.png"
    overlay_path = IMAGES_DIR / f"{uid}_overlay.png"
    cv2.imwrite(str(image_path), image)
    cv2.imwrite(str(overlay_path),
                cv2.cvtColor(result["overlay"], cv2.COLOR_RGB2BGR))

    row_id = db.log_inspection(
        uid=uid,
        timestamp=timestamp,
        product_id="default",
        model_version=model_version,
        raw_score=result["raw_score"],
        score=result["score"],
        threshold=result["threshold"],
        delta=result["delta"],
        verdict=result["verdict"],
        certainty=result["certainty"],
        explanation=result["explanation"],
        region_label=result["region_label"],
        area_pct=result["area_pct"],
        setup_status=result["setup_status"],
        image_path=str(image_path),
        overlay_path=str(overlay_path),
        latency_ms=result["latency_ms"],
        demo=0,
    )
    if result["verdict"] in ("PASS", "FAIL"):
        db.update_disposition(row_id, result["verdict"])
    result["row_id"] = row_id
    return uid
