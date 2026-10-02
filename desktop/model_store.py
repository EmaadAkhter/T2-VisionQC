"""Shared model and inspection helpers for the desktop pages."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import paths
from db import database as db
from service.foreground import (
    BackgroundModel,
    foreground_from_border,
    missing_region_fraction,
)
from service.inference import (
    PatchCoreModel,
    compute_certainty,
    compute_setup_status,
    compute_verdict,
    create_overlay,
    generate_explanation,
)


class ProfileArtifacts:
    """Loaded profile files (canonical mask, regions, background model)."""

    def __init__(self, profile: dict):
        self.profile = profile
        self.mask = self._load_mask(profile.get("canonical_mask_path"))
        self.regions = self._load_mask(profile.get("presence_regions_path"))
        self.background = self._load_background(
            profile.get("background_model_path")
        )
        self.coverage_floor = float(profile.get("coverage_floor") or 0.9)
        self.threshold = float(profile.get("threshold") or 0.55)
        self.delta = float(profile.get("delta") or 0.05)

    @staticmethod
    def _load_mask(path: str | None):
        if not path or not os.path.exists(path):
            return None
        mask = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        return None if mask is None else (mask > 127)

    @staticmethod
    def _load_background(path: str | None):
        if not path or not os.path.exists(path):
            return None
        try:
            return BackgroundModel.load(path)
        except Exception:  # noqa: BLE001
            return None


class ModelStore:
    """Loads and caches the active local model and profile."""

    def __init__(self):
        self._model: PatchCoreModel | None = None
        self._version: str | None = None
        self._artifacts: ProfileArtifacts | None = None
        self._profile_id: str | None = None
        self._artifacts_signature: tuple | None = None

    def active_version(self) -> str | None:
        return db.get_settings().get("active_model_version")

    def active_profile(self) -> dict | None:
        return db.get_active_profile()

    @staticmethod
    def _mtime(path: str | None) -> float | None:
        try:
            return os.path.getmtime(path) if path else None
        except OSError:
            return None

    def artifacts(self) -> ProfileArtifacts | None:
        profile = self.active_profile()
        if not profile:
            self._artifacts = None
            self._profile_id = None
            self._artifacts_signature = None
            return None
        # Cache on file mtimes too: editing the mask must take effect
        # without restarting the app or retraining.
        signature = (
            profile["id"],
            self._mtime(profile.get("canonical_mask_path")),
            self._mtime(profile.get("presence_regions_path")),
            self._mtime(profile.get("background_model_path")),
        )
        if (self._artifacts is not None
                and self._artifacts_signature == signature):
            return self._artifacts
        self._artifacts = ProfileArtifacts(profile)
        self._profile_id = profile["id"]
        self._artifacts_signature = signature
        return self._artifacts

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
                   settings: dict,
                   artifacts: ProfileArtifacts | None = None) -> dict:
    """Full local pipeline: predict, verdict, certainty, explanation, overlay.

    When a product profile is active, scoring is restricted to the canonical
    region and the adaptive foreground mask drives two extra checks:
    - No product / invalid capture (almost none of the expected region visible)
    - Missing expected area (presence regions not covered), which is the
      component-agnostic substitute for hardcoded cap/sticker rules.
    """
    start = time.time()

    threshold = artifacts.threshold if artifacts else settings.get("threshold", 0.46)
    delta = artifacts.delta if artifacts else settings.get("delta", 0.05)
    score_mask = artifacts.mask if artifacts else None

    prediction = model.predict(image, score_mask=score_mask)
    score = prediction["normalized_score"]

    verdict = compute_verdict(score, threshold, delta)
    setup_status, setup_reasons = compute_setup_status(image, model.training_stats)
    explanation = generate_explanation(
        prediction["anomaly_map"], threshold, ref_score=model.ref_score
    )
    certainty, certainty_reason = compute_certainty(
        score, verdict, threshold, delta, setup_status, explanation["area_pct"]
    )

    no_product = False
    missing_fraction = 0.0
    if artifacts is not None:
        if artifacts.background is not None:
            foreground = artifacts.background.foreground_mask(image)
        else:
            # No empty-scene model: the border-color fallback still gives a
            # product-agnostic foreground estimate at runtime.
            foreground = foreground_from_border(image)
        if artifacts.mask is not None and artifacts.mask.sum() > 0:
            visible = np.logical_and(foreground, artifacts.mask).sum()
            coverage = visible / artifacts.mask.sum()
            if coverage < 0.02:
                no_product = True
        if artifacts.regions is not None and artifacts.regions.sum() > 0:
            missing_fraction = missing_region_fraction(
                foreground, artifacts.regions, artifacts.coverage_floor
            )
            if missing_fraction > 0.2:
                verdict = "FAIL"
                certainty = "Low"
                certainty_reason = "Expected product area is missing."
                explanation = {
                    "explanation": (
                        "Expected product area missing or displaced "
                        f"(about {missing_fraction * 100:.0f}% of the expected "
                        "region absent). Inspect this unit."
                    ),
                    "region_label": explanation.get("region_label"),
                    "area_pct": explanation.get("area_pct", 0.0),
                }
            elif missing_fraction > 0.05 and verdict == "PASS":
                verdict = "REVIEW"
                certainty = "Low"
                certainty_reason = "Part of the expected area is not visible."
                explanation = {
                    "explanation": (
                        "Part of the expected product area is not visible "
                        f"(about {missing_fraction * 100:.0f}% absent). "
                        "Inspect this unit."
                    ),
                    "region_label": explanation.get("region_label"),
                    "area_pct": explanation.get("area_pct", 0.0),
                }

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
        "region_label": explanation.get("region_label"),
        "area_pct": explanation.get("area_pct", 0.0),
        "n_regions": explanation.get("n_regions", 0),
        "setup_status": setup_status,
        "setup_reasons": setup_reasons,
        "overlay": overlay,
        "latency_ms": int((time.time() - start) * 1000),
        "no_product": no_product,
        "missing_fraction": missing_fraction,
        "profile_id": artifacts.profile["id"] if artifacts else None,
    }


def log_inspection(result: dict, image: np.ndarray, model_version: str,
                   source: str = "edge", camera_id: str | None = None) -> str:
    """Persist one inspection locally (SQLite + image evidence)."""
    images_dir = paths.images_dir()
    uid = db.generate_inspection_uid()
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    image_path = images_dir / f"{uid}_original.png"
    overlay_path = images_dir / f"{uid}_overlay.png"
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
        camera_id=camera_id,
    )
    if result["verdict"] in ("PASS", "FAIL"):
        db.update_disposition(row_id, result["verdict"])
    result["row_id"] = row_id
    return uid
