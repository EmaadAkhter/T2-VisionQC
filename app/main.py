"""VisionQC Streamlit application.

Main entry point for the VisionQC visual inspection assistant.
"""

import os
import sys
import time
import tempfile
from pathlib import Path
from datetime import datetime

import streamlit as st
import numpy as np
import cv2
from PIL import Image

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from service.inference import (
    PatchCoreModel,
    compute_verdict,
    compute_certainty,
    generate_explanation,
    compute_setup_status,
    create_overlay,
    DEFAULT_THRESHOLD,
    DEFAULT_DELTA,
    MIN_TRAIN_IMAGES,
    TARGET_TRAIN_IMAGES,
    MAX_TRAIN_IMAGES,
)
from db import database as db


# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="VisionQC",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

def init_session_state():
    """Initialize session state variables."""
    if "model" not in st.session_state:
        st.session_state.model = None
    if "model_loaded" not in st.session_state:
        st.session_state.model_loaded = False
    if "settings" not in st.session_state:
        st.session_state.settings = db.get_settings()
    if "last_result" not in st.session_state:
        st.session_state.last_result = None
    if "demo_mode" not in st.session_state:
        st.session_state.demo_mode = False
    if "training_images" not in st.session_state:
        st.session_state.training_images = []


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

@st.cache_resource
def load_model_from_path(model_path: str):
    """Load a model from disk (cached)."""
    model = PatchCoreModel()
    model.load(model_path)
    return model


def get_active_model():
    """Get the currently active model."""
    settings = st.session_state.settings
    active_version = settings.get("active_model_version")

    if not active_version:
        return None

    model_meta = db.get_model(active_version)
    if not model_meta:
        return None

    model_path = model_meta.get("model_path")
    if not model_path or not os.path.exists(model_path):
        return None

    return load_model_from_path(model_path)


def save_uploaded_files(uploaded_files, target_dir: str) -> list:
    """Save uploaded files to target directory, return list of paths."""
    os.makedirs(target_dir, exist_ok=True)
    paths = []

    for uploaded_file in uploaded_files:
        file_path = os.path.join(target_dir, uploaded_file.name)
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        paths.append(file_path)

    return paths


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_model(image_paths: list, progress_bar=None):
    """Train a new PatchCore model."""
    model = PatchCoreModel()

    # Create progress callback
    def progress_callback(current, total):
        if progress_bar:
            progress_bar.progress(current / total)

    # Train
    stats = model.fit(image_paths)

    # Save model
    model_dir = os.path.join(os.path.dirname(__file__), "..", "data", "models")
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, f"{model.model_version}.pt")
    model.save(model_path)

    # Save metadata
    db.save_model_metadata(
        version=model.model_version,
        product_id="default",
        created_at=model.created_at,
        n_images=model.n_training_images,
        backbone="WideResNet-50",
        coreset_ratio=model.coreset_ratio,
        ref_score=model.ref_score,
        baseline_brightness=model.training_stats.get("mean_brightness", 0),
        baseline_blur=model.training_stats.get("median_blur", 0),
        parent_version=model.parent_version,
        model_path=model_path,
    )

    # Set as active
    db.update_settings("default", "active_model_version", model.model_version)
    st.session_state.settings = db.get_settings()

    return model, stats


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

def inspect_image(image: np.ndarray, model: PatchCoreModel, settings: dict) -> dict:
    """Run full inspection pipeline on an image."""
    start_time = time.time()

    # Get model prediction
    prediction = model.predict(image)

    # Compute verdict
    threshold = settings.get("threshold", DEFAULT_THRESHOLD)
    delta = settings.get("delta", DEFAULT_DELTA)
    verdict = compute_verdict(prediction["normalized_score"], threshold, delta)

    # Compute setup status
    setup_status, setup_reasons = compute_setup_status(image, model.training_stats)

    # Generate explanation
    explanation_data = generate_explanation(
        prediction["anomaly_map"],
        threshold=threshold,
        ref_score=model.ref_score,
        image_shape=image.shape[:2],
    )

    # Compute certainty
    certainty, certainty_reason = compute_certainty(
        prediction["normalized_score"],
        verdict,
        threshold=threshold,
        delta=delta,
        setup_status=setup_status,
        area_pct=explanation_data["area_pct"],
    )

    # Create overlay
    overlay = create_overlay(image, prediction["anomaly_map"], threshold)

    latency_ms = int((time.time() - start_time) * 1000)

    return {
        "raw_score": prediction["raw_score"],
        "score": prediction["normalized_score"],
        "threshold": threshold,
        "delta": delta,
        "verdict": verdict,
        "certainty": certainty,
        "certainty_reason": certainty_reason,
        "explanation": explanation_data["explanation"],
        "region_label": explanation_data["region_label"],
        "area_pct": explanation_data["area_pct"],
        "intensity": explanation_data["intensity"],
        "n_regions": explanation_data["n_regions"],
        "setup_status": setup_status,
        "setup_reasons": setup_reasons,
        "anomaly_map": prediction["anomaly_map"],
        "overlay": overlay,
        "image": image.copy(),
        "latency_ms": latency_ms,
    }


def log_inspection_result(result: dict, image: np.ndarray, overlay: np.ndarray,
                          model_version: str, demo: int = 0):
    """Save inspection to database and disk."""
    # Save images
    image_dir = os.path.join(os.path.dirname(__file__), "..", "data", "images")
    os.makedirs(image_dir, exist_ok=True)

    uid = db.generate_inspection_uid()
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    image_path = os.path.join(image_dir, f"{uid}_original.png")
    overlay_path = os.path.join(image_dir, f"{uid}_overlay.png")

    cv2.imwrite(image_path, image)
    cv2.imwrite(overlay_path, cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))

    # Log to database
    db.log_inspection(
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
        image_path=image_path,
        overlay_path=overlay_path,
        latency_ms=result["latency_ms"],
        demo=demo,
    )

    return uid


# ---------------------------------------------------------------------------
# UI Components
# ---------------------------------------------------------------------------

def render_verdict_banner(verdict: str):
    """Render a large verdict banner."""
    if verdict == "PASS":
        st.success("## ✅ PASS")
    elif verdict == "REVIEW":
        st.warning("## ⚠️ REVIEW")
    else:
        st.error("## ❌ FAIL")


def render_score_bar(score: float, threshold: float, delta: float):
    """Render score bar with threshold and review band."""
    import plotly.graph_objects as go

    fig = go.Figure()

    # Score bar
    fig.add_trace(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"suffix": "", "font": {"size": 48}},
        title={"text": "Anomaly Score", "font": {"size": 20}},
        gauge={
            "axis": {"range": [0, 1], "tickwidth": 1},
            "bar": {"color": "darkblue"},
            "steps": [
                {"range": [0, threshold - delta], "color": "lightgreen"},
                {"range": [threshold - delta, threshold + delta], "color": "yellow"},
                {"range": [threshold + delta, 1], "color": "salmon"},
            ],
            "threshold": {
                "line": {"color": "red", "width": 4},
                "thickness": 0.75,
                "value": threshold,
            },
        },
    ))

    fig.update_layout(height=300, margin=dict(l=20, r=20, t=50, b=20))
    st.plotly_chart(fig, use_container_width=True)


def render_setup_status(status: str, reasons: list):
    """Render setup quality indicator."""
    if status == "OK":
        st.info(f"**Setup:** ✅ OK — {', '.join(reasons)}")
    elif status == "Caution":
        st.warning(f"**Setup:** ⚠️ Caution — {', '.join(reasons)}")
    else:
        st.error(f"**Setup:** ❌ Poor — {', '.join(reasons)}")


def render_certainty(certainty: str, reason: str):
    """Render decision certainty."""
    icons = {"High": "🟢", "Medium": "🟡", "Low": "🔴"}
    st.markdown(f"**Decision Certainty:** {icons.get(certainty, '')} {certainty}")
    st.caption(reason)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def page_inspect():
    """Main inspection page."""
    st.title("🔍 VisionQC — Inspect")

    # Check for model
    model = get_active_model()

    if model is None:
        st.warning("No model trained yet. Please train a model first.")
        if st.button("Go to Training"):
            st.session_state.page = "train"
            st.rerun()
        return

    # Status chip
    st.caption(f"Trained on {model.n_training_images} images | Model: {model.model_version[:12]}...")

    # Setup status placeholder
    setup_placeholder = st.empty()

    # Camera capture
    st.subheader("Capture Unit")
    col1, col2 = st.columns([2, 1])

    with col1:
        camera_image = st.camera_input("Take a photo of the unit")

    with col2:
        st.markdown("### Or upload an image")
        uploaded_file = st.file_uploader("Choose an image", type=["jpg", "jpeg", "png"])

    # Get image from camera or upload
    image = None
    if camera_image is not None:
        file_bytes = np.asarray(bytearray(camera_image.read()), dtype=np.uint8)
        image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    elif uploaded_file is not None:
        file_bytes = np.asarray(bytearray(uploaded_file.read()), dtype=np.uint8)
        image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

    if image is not None:
        # Show captured image
        st.image(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), caption="Captured unit", use_container_width=True)

        # Inspect button
        if st.button("🔍 Inspect", type="primary", use_container_width=True):
            with st.spinner("Analysing..."):
                result = inspect_image(image, model, st.session_state.settings)

                # Save result
                uid = log_inspection_result(
                    result, image, result["overlay"],
                    model.model_version, demo=st.session_state.demo_mode
                )

                st.session_state.last_result = result
                st.session_state.last_uid = uid

    # Display result
    if st.session_state.last_result:
        result = st.session_state.last_result

        st.divider()
        st.subheader("Result")

        render_verdict_banner(result["verdict"])

        col1, col2 = st.columns(2)

        with col1:
            render_score_bar(result["score"], result["threshold"], result["delta"])
            st.caption(f"Threshold: {result['threshold']:.2f} | Review band: ±{result['delta']:.2f}")

        with col2:
            render_certainty(result["certainty"], result["certainty_reason"])
            st.markdown(f"**Explanation:** {result['explanation']}")

            if result["region_label"]:
                st.caption(f"Location: {result['region_label']} | Area: {result['area_pct']:.1f}% | Regions: {result['n_regions']}")

        # Setup status
        render_setup_status(result["setup_status"], result["setup_reasons"])

        # Images
        st.subheader("Visualisation")
        col1, col2 = st.columns(2)

        with col1:
            st.image(cv2.cvtColor(result["image"], cv2.COLOR_BGR2RGB), caption="Original", use_container_width=True)

        with col2:
            st.image(result["overlay"], caption="Anomaly Heatmap", use_container_width=True)

        # Review workflow
        if result["verdict"] == "REVIEW":
            st.divider()
            st.subheader("Manual Review")
            st.warning("This unit needs manual inspection. Record your decision:")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ Accept as PASS", use_container_width=True):
                    db.update_disposition(
                        st.session_state.last_uid, "PASS",
                        override=True, note="Accepted after manual review"
                    )
                    st.success("Recorded as PASS")
                    st.rerun()

            with col2:
                if st.button("❌ Reject as FAIL", use_container_width=True):
                    db.update_disposition(
                        st.session_state.last_uid, "FAIL",
                        override=True, note="Rejected after manual review"
                    )
                    st.error("Recorded as FAIL")
                    st.rerun()

        # Operator note
        note = st.text_input("Operator note (optional)", max_chars=280)
        if note and st.button("Save Note"):
            # Update the inspection with note
            db.update_disposition(
                st.session_state.last_uid,
                st.session_state.last_result["verdict"] if st.session_state.last_result["verdict"] != "REVIEW" else "PENDING",
                note=note
            )
            st.success("Note saved")

        st.caption(f"Inspection ID: {st.session_state.last_uid} | Latency: {result['latency_ms']}ms")


def page_train():
    """Training page."""
    st.title("🎓 Train Model")

    st.markdown("""
    Upload **20-30 photos of good units** to train the anomaly detection model.
    No defect examples needed — VisionQC learns what "normal" looks like.
    """)

    # Upload
    uploaded_files = st.file_uploader(
        "Upload good unit photos",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        st.info(f"Selected {len(uploaded_files)} files")

        # Validate
        valid_files = []
        errors = []

        for f in uploaded_files:
            if f.size == 0:
                errors.append(f"{f.name}: empty file")
                continue
            valid_files.append(f)

        if errors:
            for e in errors:
                st.error(e)

        if len(valid_files) < MIN_TRAIN_IMAGES:
            st.error(f"Need at least {MIN_TRAIN_IMAGES} valid images")
            return

        if len(valid_files) < TARGET_TRAIN_IMAGES:
            st.warning(f"⚠️ Accuracy drops below {TARGET_TRAIN_IMAGES} photos. You have {len(valid_files)}.")

        if len(valid_files) > MAX_TRAIN_IMAGES:
            st.info(f"More than {MAX_TRAIN_IMAGES} images selected. Returns diminish beyond {MAX_TRAIN_IMAGES}.")

        # Preview
        st.subheader("Preview")
        cols = st.columns(min(5, len(valid_files)))
        for i, f in enumerate(valid_files[:10]):
            with cols[i % 5]:
                st.image(f, caption=f.name, use_container_width=True)

        # Train button
        if st.button("🚀 Train Model", type="primary", use_container_width=True):
            # Save files
            with tempfile.TemporaryDirectory() as tmpdir:
                paths = save_uploaded_files(valid_files, tmpdir)

                # Train
                progress = st.progress(0)
                status = st.empty()

                try:
                    status.text("Training model...")
                    model, stats = train_model(paths, progress)
                    progress.progress(1.0)

                    st.success(f"✅ Model trained successfully!")
                    st.json(stats)

                    st.session_state.model_loaded = True

                    if st.button("Go to Inspect"):
                        st.session_state.page = "inspect"
                        st.rerun()

                except Exception as e:
                    st.error(f"Training failed: {e}")


def page_settings():
    """Settings page."""
    st.title("⚙️ Settings")

    settings = st.session_state.settings

    # Threshold
    st.subheader("Threshold")
    new_threshold = st.slider(
        "Decision threshold",
        min_value=0.0,
        max_value=1.0,
        value=settings.get("threshold", DEFAULT_THRESHOLD),
        step=0.01,
    )

    new_delta = st.slider(
        "Review band (±)",
        min_value=0.0,
        max_value=0.20,
        value=settings.get("delta", DEFAULT_DELTA),
        step=0.01,
    )

    if st.button("Apply Settings"):
        db.update_settings("default", "threshold", new_threshold)
        db.update_settings("default", "delta", new_delta)
        st.session_state.settings = db.get_settings()
        st.success("Settings saved")

    # Impact preview
    st.subheader("Threshold Impact Preview")
    active_version = settings.get("active_model_version")

    if active_version:
        scores = db.get_recent_scores(active_version, limit=200)

        if len(scores) >= 20:
            # Calculate impact
            current_threshold = settings.get("threshold", DEFAULT_THRESHOLD)
            current_delta = settings.get("delta", DEFAULT_DELTA)

            new_pass = sum(1 for s in scores if s < new_threshold - new_delta)
            new_review = sum(1 for s in scores if new_threshold - new_delta <= s < new_threshold + new_delta)
            new_fail = sum(1 for s in scores if s >= new_threshold + new_delta)

            current_pass = sum(1 for s in scores if s < current_threshold - current_delta)
            current_review = sum(1 for s in scores if current_threshold - current_delta <= s < current_threshold + current_delta)
            current_fail = sum(1 for s in scores if s >= current_threshold + current_delta)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Would PASS", new_pass, f"{new_pass - current_pass:+d}")
            with col2:
                st.metric("Would REVIEW", new_review, f"{new_review - current_review:+d}")
            with col3:
                st.metric("Would FAIL", new_fail, f"{new_fail - current_fail:+d}")

            st.caption(f"Based on last {len(scores)} inspections")

            if new_threshold > current_threshold:
                st.warning("⚠️ Raising threshold: Subtle defects may be accepted")
            elif new_threshold < current_threshold:
                st.warning("⚠️ Lowering threshold: More good units may be flagged")
        else:
            st.info(f"Not enough inspections to estimate impact ({len(scores)} of 20)")
    else:
        st.info("No active model")

    # Model versions
    st.subheader("Model Versions")
    models = db.get_all_models()

    if models:
        for m in models:
            with st.expander(f"Model {m['version'][:16]}... ({m['n_images']} images)"):
                st.caption(f"Created: {m['created_at']}")
                st.caption(f"Reference score: {m['ref_score']:.4f}")
                if m["version"] != settings.get("active_model_version"):
                    if st.button(f"Restore {m['version'][:12]}...", key=f"restore_{m['version']}"):
                        db.update_settings("default", "active_model_version", m["version"])
                        st.session_state.settings = db.get_settings()
                        st.rerun()
    else:
        st.info("No models trained yet")

    # Settings history
    st.subheader("Settings History")
    history = db.get_settings_history(limit=20)
    if history:
        for h in history:
            st.caption(f"{h['timestamp']}: {h['field']} changed from {h['old_value']} to {h['new_value']}")
    else:
        st.info("No settings changes yet")


def page_dashboard():
    """Dashboard page."""
    st.title("📊 Dashboard")

    # Date selector
    date = st.date_input("Date", value=datetime.now())
    date_str = date.strftime("%Y-%m-%d")

    # Stats
    stats = db.get_dashboard_stats(date_str)

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("Total Inspected", stats["total"])
    with col2:
        st.metric("Passed", stats["passed"])
    with col3:
        st.metric("Failed", stats["failed"])
    with col4:
        st.metric("Pending Review", stats["pending_review"])
    with col5:
        if stats["rejection_rate"] is not None:
            st.metric("Rejection Rate", f"{stats['rejection_rate']:.1%}")
        else:
            st.metric("Rejection Rate", "No inspections yet")

    # Recent inspections
    st.subheader("Recent Inspections")
    inspections = db.get_inspections(date=date_str, limit=50)

    if inspections:
        for insp in inspections:
            with st.expander(f"{insp['uid']} — {insp['verdict']} ({insp['score']:.2f})"):
                col1, col2 = st.columns([1, 3])
                with col1:
                    if insp.get("image_path") and os.path.exists(insp["image_path"]):
                        st.image(insp["image_path"], width=150)
                with col2:
                    st.caption(f"Time: {insp['timestamp']}")
                    st.caption(f"Score: {insp['score']:.2f} | Threshold: {insp['threshold']:.2f}")
                    st.caption(f"Verdict: {insp['verdict']} | Disposition: {insp['disposition']}")
                    if insp.get("explanation"):
                        st.caption(f"Explanation: {insp['explanation']}")
                    if insp.get("operator_note"):
                        st.caption(f"Note: {insp['operator_note']}")

                    # Resolve pending reviews
                    if insp["disposition"] == "PENDING":
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("Accept", key=f"accept_{insp['id']}"):
                                db.update_disposition(insp["id"], "PASS", override=True)
                                st.rerun()
                        with col2:
                            if st.button("Reject", key=f"reject_{insp['id']}"):
                                db.update_disposition(insp["id"], "FAIL", override=True)
                                st.rerun()
    else:
        st.info("No inspections for this date")

    # Export
    st.subheader("Export")
    if st.button("Export CSV"):
        csv_data = db.export_csv(date_str)
        if csv_data:
            st.download_button(
                "Download CSV",
                data=csv_data.encode("utf-8-sig"),
                file_name=f"visionqc_{date_str}.csv",
                mime="text/csv",
            )
        else:
            st.warning("No data to export")


def page_demo():
    """Demo mode page."""
    st.title("🎬 Demo Mode")

    st.warning("**DEMO MODE ACTIVE** — Inspections are flagged and excluded from the real dashboard.")

    st.session_state.demo_mode = True

    # Demo gallery
    st.subheader("Sample Gallery")
    st.info("Click a sample to inspect it through the normal pipeline.")

    demo_dir = os.path.join(os.path.dirname(__file__), "..", "data", "demo")
    if os.path.exists(demo_dir):
        demo_files = [f for f in os.listdir(demo_dir) if f.endswith((".jpg", ".jpeg", ".png"))]

        if demo_files:
            cols = st.columns(min(4, len(demo_files)))
            for i, fname in enumerate(demo_files):
                with cols[i % 4]:
                    img_path = os.path.join(demo_dir, fname)
                    st.image(img_path, caption=fname, use_container_width=True)
                    if st.button(f"Inspect {fname}", key=f"demo_{fname}"):
                        image = cv2.imread(img_path)
                        model = get_active_model()
                        if model:
                            with st.spinner("Analysing..."):
                                result = inspect_image(image, model, st.session_state.settings)
                                uid = log_inspection_result(
                                    result, image, result["overlay"],
                                    model.model_version, demo=1
                                )
                                st.session_state.last_result = result
                                st.session_state.last_uid = uid
                                st.rerun()
        else:
            st.info("No demo images found. Add images to data/demo/")
    else:
        st.info("Demo directory not found. Create data/demo/ and add sample images.")

    # Show last result
    if st.session_state.last_result:
        result = st.session_state.last_result
        st.divider()
        render_verdict_banner(result["verdict"])
        st.markdown(f"**Score:** {result['score']:.2f}")
        st.markdown(f"**Explanation:** {result['explanation']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Main application entry point."""
    init_session_state()
    db.init_db()

    # Navigation
    st.sidebar.title("VisionQC")
    st.sidebar.caption("Offline Visual Inspection")

    page = st.sidebar.radio(
        "Navigation",
        ["Inspect", "Train", "Dashboard", "Settings", "Demo"],
        index=0,
    )

    if st.session_state.demo_mode:
        st.sidebar.warning("🎬 Demo Mode Active")

    # Render page
    if page == "Inspect":
        page_inspect()
    elif page == "Train":
        page_train()
    elif page == "Dashboard":
        page_dashboard()
    elif page == "Settings":
        page_settings()
    elif page == "Demo":
        page_demo()


if __name__ == "__main__":
    main()
