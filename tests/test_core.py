"""Unit tests for VisionQC core logic."""

import os
import sys
import tempfile
import numpy as np
import cv2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from service.inference import (
    compute_verdict,
    compute_certainty,
    generate_explanation,
    compute_setup_status,
    DEFAULT_THRESHOLD,
    DEFAULT_DELTA,
)
from db import database as db


# ---------------------------------------------------------------------------
# Verdict tests
# ---------------------------------------------------------------------------

class TestVerdict:
    """Test PASS/REVIEW/FAIL verdict computation."""

    def test_pass_below_threshold(self):
        assert compute_verdict(0.30, 0.60, 0.05) == "PASS"

    def test_review_at_lower_boundary(self):
        # Exactly at T-delta should be REVIEW
        assert compute_verdict(0.55, 0.60, 0.05) == "REVIEW"

    def test_review_in_band(self):
        assert compute_verdict(0.58, 0.60, 0.05) == "REVIEW"
        assert compute_verdict(0.62, 0.60, 0.05) == "REVIEW"

    def test_fail_at_upper_boundary(self):
        # Exactly at T+delta should be FAIL
        assert compute_verdict(0.65, 0.60, 0.05) == "FAIL"

    def test_fail_above_threshold(self):
        assert compute_verdict(0.80, 0.60, 0.05) == "FAIL"

    def test_zero_delta(self):
        # With delta=0, score < T is PASS, >= T is FAIL
        assert compute_verdict(0.59, 0.60, 0.0) == "PASS"
        assert compute_verdict(0.60, 0.60, 0.0) == "FAIL"

    def test_edge_cases(self):
        assert compute_verdict(0.0, 0.60, 0.05) == "PASS"
        assert compute_verdict(1.0, 0.60, 0.05) == "FAIL"


# ---------------------------------------------------------------------------
# Certainty tests
# ---------------------------------------------------------------------------

class TestCertainty:
    """Test decision certainty computation."""

    def test_high_certainty_far_from_threshold(self):
        level, reason = compute_certainty(0.90, "FAIL", 0.60, 0.05)
        assert level == "High"

    def test_medium_certainty_moderate_margin(self):
        level, reason = compute_certainty(0.75, "FAIL", 0.60, 0.05)
        assert level == "Medium"

    def test_low_certainty_near_threshold(self):
        level, reason = compute_certainty(0.62, "REVIEW", 0.60, 0.05)
        assert level == "Low"

    def test_review_always_low(self):
        # Even if margin is large, REVIEW should be Low
        level, _ = compute_certainty(0.56, "REVIEW", 0.60, 0.05)
        assert level == "Low"

    def test_downgrade_for_poor_setup(self):
        level, _ = compute_certainty(0.90, "FAIL", 0.60, 0.05, setup_status="Poor")
        assert level == "Medium"

    def test_downgrade_for_widespread_area(self):
        level, _ = compute_certainty(0.90, "FAIL", 0.60, 0.05, area_pct=30)
        assert level == "Medium"

    def test_downgrade_only_one_level(self):
        # High -> Medium, not High -> Low
        level, _ = compute_certainty(0.90, "FAIL", 0.60, 0.05, setup_status="Poor", area_pct=30)
        assert level == "Medium"


# ---------------------------------------------------------------------------
# Explanation tests
# ---------------------------------------------------------------------------

class TestExplanation:
    """Test plain-language explanation generation."""

    def test_no_areas_found(self):
        # Empty anomaly map
        anomaly_map = np.zeros((100, 100))
        result = generate_explanation(anomaly_map, threshold=0.60)
        assert result["explanation"] == "No unusual areas found."
        assert result["n_regions"] == 0

    def test_defect_detected(self):
        # Create anomaly map with a hot spot
        anomaly_map = np.zeros((100, 100))
        anomaly_map[40:60, 40:60] = 0.8
        result = generate_explanation(anomaly_map, threshold=0.60)
        assert result["n_regions"] >= 1
        assert "Unusual area" in result["explanation"]
        assert result["region_label"] is not None
        assert result["area_pct"] > 0

    def test_widespread_area(self):
        # Large anomaly region
        anomaly_map = np.zeros((100, 100))
        anomaly_map[10:90, 10:90] = 0.8
        result = generate_explanation(anomaly_map, threshold=0.60)
        assert "Widespread" in result["explanation"]

    def test_no_defect_type_words(self):
        # Ensure no defect-type language
        anomaly_map = np.zeros((100, 100))
        anomaly_map[40:60, 40:60] = 0.8
        result = generate_explanation(anomaly_map, threshold=0.60)
        forbidden = ["scratch", "stain", "dent", "crack", "defect"]
        for word in forbidden:
            assert word not in result["explanation"].lower()


# ---------------------------------------------------------------------------
# Setup status tests
# ---------------------------------------------------------------------------

class TestSetupStatus:
    """Test setup quality checks."""

    def test_ok_status(self):
        # Create a normal image
        image = np.random.randint(100, 150, (224, 224, 3), dtype=np.uint8)
        stats = {
            "mean_brightness": 125.0,
            "median_blur": 500.0,
            "mean_image": image,
        }
        status, reasons = compute_setup_status(image, stats)
        assert status == "OK"

    def test_caution_for_blur(self):
        image = np.random.randint(100, 150, (224, 224, 3), dtype=np.uint8)
        # Very blurry image
        image = cv2.GaussianBlur(image, (31, 31), 0)
        stats = {
            "mean_brightness": 125.0,
            "median_blur": 500.0,
            "mean_image": np.random.randint(100, 150, (224, 224, 3), dtype=np.uint8),
        }
        status, reasons = compute_setup_status(image, stats)
        assert status in ("Caution", "Poor")

    def test_no_baseline(self):
        image = np.random.randint(100, 150, (224, 224, 3), dtype=np.uint8)
        status, reasons = compute_setup_status(image, {})
        assert status == "Caution"


# ---------------------------------------------------------------------------
# Database tests
# ---------------------------------------------------------------------------

class TestDatabase:
    """Test SQLite database operations."""

    def setup_method(self):
        """Set up test database."""
        self.test_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.test_db.close()
        # Override database path
        self._original_get_db_path = db.get_db_path
        db.get_db_path = lambda: self.test_db.name
        db.init_db()

    def teardown_method(self):
        """Clean up test database."""
        db.get_db_path = self._original_get_db_path
        os.unlink(self.test_db.name)

    def test_log_inspection(self):
        uid = db.generate_inspection_uid()
        inspection_id = db.log_inspection(
            uid=uid,
            timestamp="2026-10-02T10:00:00",
            product_id="default",
            model_version="v1",
            raw_score=0.5,
            score=0.3,
            threshold=0.6,
            delta=0.05,
            verdict="PASS",
            certainty="High",
            explanation="No unusual areas found.",
            region_label=None,
            area_pct=0.0,
            setup_status="OK",
            image_path="/tmp/test.png",
            overlay_path="/tmp/test_overlay.png",
            latency_ms=100,
        )
        assert inspection_id > 0

    def test_dashboard_stats(self):
        # Log some inspections and set their final dispositions
        for i in range(5):
            uid = f"INS-TEST-{i:04d}"
            inspection_id = db.log_inspection(
                uid=uid,
                timestamp=f"2026-10-02T10:{i:02d}:00",
                product_id="default",
                model_version="v1",
                raw_score=0.5,
                score=0.3 + i * 0.1,
                threshold=0.6,
                delta=0.05,
                verdict="PASS" if i < 3 else "FAIL",
                certainty="High",
                explanation="Test",
                region_label=None,
                area_pct=0.0,
                setup_status="OK",
                image_path="/tmp/test.png",
                overlay_path="/tmp/test_overlay.png",
                latency_ms=100,
            )
            # Set final disposition to match verdict
            final_disposition = "PASS" if i < 3 else "FAIL"
            db.update_disposition(inspection_id, final_disposition)

        stats = db.get_dashboard_stats("2026-10-02")
        assert stats["total"] == 5
        assert stats["passed"] == 3
        assert stats["failed"] == 2

    def test_empty_dashboard(self):
        stats = db.get_dashboard_stats("2026-01-01")
        assert stats["total"] == 0
        assert stats["rejection_rate"] is None

    def test_update_disposition(self):
        uid = db.generate_inspection_uid()
        inspection_id = db.log_inspection(
            uid=uid,
            timestamp="2026-10-02T10:00:00",
            product_id="default",
            model_version="v1",
            raw_score=0.5,
            score=0.58,
            threshold=0.6,
            delta=0.05,
            verdict="REVIEW",
            certainty="Low",
            explanation="Unusual area found",
            region_label="center",
            area_pct=5.0,
            setup_status="OK",
            image_path="/tmp/test.png",
            overlay_path="/tmp/test_overlay.png",
            latency_ms=100,
        )

        db.update_disposition(inspection_id, "PASS", override=True, note="Accepted")

        inspections = db.get_inspections(verdict="REVIEW")
        assert len(inspections) == 1
        assert inspections[0]["disposition"] == "PASS"
        assert inspections[0]["disposition_by_override"] == 1

    def test_settings_persistence(self):
        db.update_settings("default", "threshold", 0.70)
        settings = db.get_settings("default")
        assert settings["threshold"] == 0.70

        history = db.get_settings_history()
        assert len(history) >= 1
        assert history[0]["field"] == "threshold"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegration:
    """Integration tests for the full pipeline."""

    def test_full_inspection_flow(self):
        """Test train -> inspect -> log -> dashboard flow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create dummy training images
            train_paths = []
            for i in range(10):
                img = np.random.randint(100, 150, (224, 224, 3), dtype=np.uint8)
                path = os.path.join(tmpdir, f"train_{i}.png")
                cv2.imwrite(path, img)
                train_paths.append(path)

            # Train model
            from service.inference import PatchCoreModel
            model = PatchCoreModel()
            stats = model.fit(train_paths)
            assert stats["n_training_images"] == 10

            # Predict
            test_img = np.random.randint(100, 150, (224, 224, 3), dtype=np.uint8)
            result = model.predict(test_img)
            assert "raw_score" in result
            assert "normalized_score" in result
            assert "anomaly_map" in result
            assert 0 <= result["normalized_score"] <= 1

            # Compute verdict
            verdict = compute_verdict(result["normalized_score"])
            assert verdict in ("PASS", "REVIEW", "FAIL")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
