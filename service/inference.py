"""Core PatchCore inference service.

Pure Python module with no Streamlit dependencies.
Handles training, inference, model save/load, and calibration.

PatchCore (Roth et al., CVPR 2022), product-agnostic:
- Pluggable frozen backbone (WideResNet-50 by default, DINOv2 for
  cross-product generalization) via :mod:`service.backbones`
- Greedy coreset subsampling of patch features
- Nearest-neighbour search; image score = max of the patch anomaly map
"""

import os
import hashlib
import time
from typing import Optional, Tuple, Dict, Any, List
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
import cv2

from service.backbones import backbone_from_config, build_backbone


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 0.46  # tuned on MVTec AD test set (see BENCHMARK.md)
DEFAULT_DELTA = 0.05
MIN_TRAIN_IMAGES = 5
TARGET_TRAIN_IMAGES = 20
MAX_TRAIN_IMAGES = 30
INPUT_SIZE = (320, 320)
IMAGE_MEAN = [0.485, 0.456, 0.406]
IMAGE_STD = [0.229, 0.224, 0.225]
COSET_RATIO = 0.01
MAX_CORESET_CANDIDATES = 60000
MIN_CORESET_POINTS = 500
MAX_CORESET_POINTS = 2000
DEVICE = "cpu"
RANDOM_SEED = 42
LOO_MAX_IMAGES = 30   # leave-one-out reference calibration up to this set size
KFOLD = 5             # cross-validation folds for larger onboarding sets


def auto_device() -> str:
    """Best available device for interactive inference.

    Honors ``VISIONQC_DEVICE`` (tests/CI pin ``cpu``); otherwise CUDA, then
    Apple MPS, then CPU. The model default stays CPU for headless service use;
    the desktop app opts in via this helper.
    """
    override = os.environ.get("VISIONQC_DEVICE")
    if override:
        return override
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


# ---------------------------------------------------------------------------
# Image loading helpers
# ---------------------------------------------------------------------------

def load_image_bgr(path: str) -> np.ndarray:
    """Load an image from disk as BGR (OpenCV convention)."""
    img = cv2.imread(path)
    if img is None:
        raise ValueError(f"Cannot read image: {path}")
    return img


def preprocess_image(image_bgr: np.ndarray,
                     size: Tuple[int, int] = INPUT_SIZE) -> torch.Tensor:
    """Convert a BGR image to a normalized (1, 3, H, W) tensor."""
    img_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    img_resized = cv2.resize(img_rgb, size)
    img_tensor = torch.from_numpy(img_resized).permute(2, 0, 1).float() / 255.0
    mean = torch.tensor(IMAGE_MEAN).view(3, 1, 1)
    std = torch.tensor(IMAGE_STD).view(3, 1, 1)
    img_tensor = (img_tensor - mean) / std
    return img_tensor.unsqueeze(0)


class ImageDataset(Dataset):
    """Dataset for loading images from disk."""

    def __init__(self, image_paths: List[str], size: Tuple[int, int] = INPUT_SIZE):
        self.image_paths = image_paths
        self.size = size

    def __len__(self):
        return len(self.image_paths)

    def __getitem__(self, idx):
        image = load_image_bgr(self.image_paths[idx])
        return preprocess_image(image, self.size).squeeze(0), self.image_paths[idx]


# ---------------------------------------------------------------------------
# PatchCore model
# ---------------------------------------------------------------------------

class PatchCoreModel:
    """PatchCore anomaly detection model with a pluggable backbone."""

    def __init__(self, coreset_ratio: float = COSET_RATIO,
                 backbone: str = "wide_resnet50",
                 backbone_kwargs: Optional[Dict[str, Any]] = None):
        self.device = torch.device(DEVICE)
        self.coreset_ratio = coreset_ratio
        self.backbone_name = backbone
        self.backbone_kwargs = dict(backbone_kwargs or {})
        self.backbone = build_backbone(backbone, **self.backbone_kwargs)
        self.input_size = self.backbone.input_size
        self.memory_bank = None          # (M, C) tensor
        self.ref_score = None            # float
        self.model_version = None
        self.training_stats = {}
        self.n_training_images = 0
        self.created_at = None
        self.parent_version = None

    # -- backbone -----------------------------------------------------------

    def _use_backbone_config(self, config: Optional[dict]) -> None:
        """Rebuild the backbone from a saved config (legacy -> WideResNet)."""
        if self.backbone.config() == config:
            return
        backbone = backbone_from_config(config)
        self.backbone = backbone
        self.backbone_name = backbone.config().get("name", "wide_resnet50")
        self.backbone_kwargs = {
            k: v for k, v in backbone.config().items() if k != "name"
        }
        self.input_size = backbone.input_size

    def parameters(self):
        return self.backbone.parameters()

    def to(self, device):
        self.backbone.to(device)
        self.device = torch.device(device)
        return self

    def train(self):
        self.backbone.train()
        return self

    def eval(self):
        self.backbone.eval()
        return self

    def extract_features(self, images: torch.Tensor) -> torch.Tensor:
        """Extract patch features of shape (N, C, H, W)."""
        return self.backbone.extract(images)

    # -- training -----------------------------------------------------------

    def _extract_features_for_paths(self, image_paths: List[str],
                                    batch_size: int = 8) -> torch.Tensor:
        """Extract features for all images; returns (N, C, H, W) on CPU."""
        dataset = ImageDataset(image_paths, size=self.input_size)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=False)
        features = []
        for batch, _ in loader:
            batch = batch.to(self.device)
            feats = self.extract_features(batch)
            features.append(feats.cpu())
        return torch.cat(features, dim=0)

    def _coreset_sampling(self, points: torch.Tensor,
                          max_candidates: int = MAX_CORESET_CANDIDATES,
                          max_points: int = MAX_CORESET_POINTS) -> torch.Tensor:
        """Greedy coreset subsampling over patch feature points.

        Randomly subsamples candidate points first to keep the greedy pass
        fast, then selects a representative subset using an efficient
        matrix-vector distance computation.

        Args:
            points: (P, C) tensor of patch features
        Returns:
            coreset: (M, C) tensor
        """
        P = points.shape[0]
        if P > max_candidates:
            g = torch.Generator().manual_seed(RANDOM_SEED)
            idx = torch.randperm(P, generator=g)[:max_candidates]
            candidates = points[idx]
        else:
            candidates = points

        N = candidates.shape[0]
        M = int(N * self.coreset_ratio)
        M = max(MIN_CORESET_POINTS, min(M, max_points))
        if M >= N:
            return candidates

        norms = (candidates * candidates).sum(dim=1)  # (N,)
        selected = [0]
        min_dist = torch.full((N,), float("inf"))
        for _ in range(1, M):
            q = candidates[selected[-1]]
            dist_sq = norms + (q * q).sum() - 2.0 * (candidates @ q)
            dist = dist_sq.clamp_min(0).sqrt()
            min_dist = torch.minimum(min_dist, dist)
            selected.append(int(torch.argmax(min_dist).item()))
        return candidates[selected]

    def _features_to_points(self, features: torch.Tensor) -> torch.Tensor:
        """Convert (N, C, H, W) features to (N*H*W, C) patch vectors."""
        N, C, H, W = features.shape
        return features.permute(0, 2, 3, 1).reshape(N * H * W, C)

    def _score_image(self, features: torch.Tensor, bank: torch.Tensor,
                     score_mask: np.ndarray | None = None) -> torch.Tensor:
        """Compute the patch anomaly map of one image against a bank.

        Args:
            features: (1, C, H, W)
            bank: (M, C)
            score_mask: optional boolean mask at any resolution; patches whose
                centre falls outside the mask are scored as zero. This is the
                canonical product region: missing parts inside it still produce
                anomalies, which is why the adaptive per-frame mask is never
                used here.
        Returns:
            (H, W) tensor of patch distances
        """
        _, C, H, W = features.shape
        points = self._features_to_points(features)         # (H*W, C)
        bank = bank.to(points.device)
        dists = torch.cdist(points, bank)                   # (H*W, M)
        min_dists = dists.min(dim=1)[0]                     # (H*W,)
        patch_map = min_dists.reshape(H, W)
        if score_mask is not None:
            resized = cv2.resize(
                score_mask.astype(np.uint8), (W, H),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)
            mask_tensor = torch.from_numpy(resized.astype(np.float32))
            patch_map = patch_map * mask_tensor.to(patch_map.device)
        return patch_map

    def _reference_scores(self, all_features: torch.Tensor,
                          holdout_indices: List[int],
                          train_indices: List[int],
                          score_mask: np.ndarray | None = None,
                          max_candidates: int = 20000,
                          max_points: int = 500) -> List[float]:
        """Raw scores of held-out images against a bank built without them."""
        if not holdout_indices:
            return []
        train_points = self._features_to_points(all_features[train_indices])
        bank = self._coreset_sampling(train_points,
                                      max_candidates=max_candidates,
                                      max_points=max_points)
        scores = []
        for i in holdout_indices:
            patch_map = self._score_image(all_features[i:i + 1], bank,
                                          score_mask=score_mask)
            scores.append(float(patch_map.max()))
        return scores

    def _cross_validated_ref_scores(self, all_features: torch.Tensor,
                                    score_mask: np.ndarray | None = None
                                    ) -> Tuple[List[float], str]:
        """Honest reference scores for tiny onboarding sets.

        A single holdout split against a smaller preliminary bank inflates the
        reference (the bank is weaker than the final one), which hides defects.
        Leave-one-out (tiny sets) and 5-fold (larger sets) score every good
        image against a bank that never saw it, so ``ref_score`` reflects the
        real deployment distribution.
        """
        n = all_features.shape[0]
        if n <= LOO_MAX_IMAGES:
            scores: List[float] = []
            for i in range(n):
                others = [j for j in range(n) if j != i]
                scores.extend(self._reference_scores(
                    all_features, [i], others, score_mask))
            return scores, "leave-one-out"

        rng = np.random.default_rng(RANDOM_SEED)
        fold_of = rng.permutation(n) % KFOLD
        scores = []
        for fold in range(KFOLD):
            holdout = [i for i in range(n) if fold_of[i] == fold]
            train = [i for i in range(n) if fold_of[i] != fold]
            scores.extend(self._reference_scores(
                all_features, holdout, train, score_mask))
        return scores, f"{KFOLD}-fold"

    def fit(self, image_paths: List[str],
            holdout_indices: Optional[List[int]] = None,
            score_mask: np.ndarray | None = None) -> Dict[str, Any]:
        """Train the model on good images.

        1. Extract patch features once for every image.
        2. Derive ``ref_score`` (the worst-good reference) by cross-validation:
           leave-one-out for sets up to ``LOO_MAX_IMAGES``, 5-fold above that.
           Callers may pass an explicit ``holdout_indices`` split instead.
        3. Build the final memory bank on all images.

        Normalised score maps the worst held-out good unit to 0.50, so the
        default PASS threshold sits below that and everything above goes to
        REVIEW/FAIL.

        `score_mask` restricts reference scoring to the canonical product
        region so the normalisation matches runtime scoring.
        """
        if len(image_paths) < MIN_TRAIN_IMAGES:
            raise ValueError(
                f"Need at least {MIN_TRAIN_IMAGES} images, got {len(image_paths)}"
            )

        torch.manual_seed(RANDOM_SEED)
        np.random.seed(RANDOM_SEED)

        # Extract features once for all images
        all_features = self._extract_features_for_paths(image_paths)
        H, W = all_features.shape[2], all_features.shape[3]

        if holdout_indices is not None:
            train_indices = [i for i in range(len(image_paths))
                             if i not in holdout_indices]
            ref_scores = self._reference_scores(
                all_features, list(holdout_indices), train_indices, score_mask)
            calibration = "holdout"
        else:
            ref_scores, calibration = self._cross_validated_ref_scores(
                all_features, score_mask)

        self.ref_score = max(ref_scores) if ref_scores else 1.0
        if self.ref_score <= 0:
            self.ref_score = 1.0

        # Final bank on all images
        all_points = self._features_to_points(all_features)
        self.memory_bank = self._coreset_sampling(all_points)

        # Training statistics (computed on all training-time images)
        self.training_stats = self._compute_training_stats(image_paths)
        self.n_training_images = len(image_paths)
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.model_version = self._generate_version()

        return {
            "model_version": self.model_version,
            "n_training_images": self.n_training_images,
            "n_holdout": len(ref_scores),
            "calibration": calibration,
            "ref_score": self.ref_score,
            "memory_bank_size": int(self.memory_bank.shape[0]),
            "feature_map_size": [H, W],
            "training_stats": {k: v for k, v in self.training_stats.items()
                               if k != "mean_image"},
        }

    def _compute_training_stats(self, image_paths: List[str]) -> Dict[str, Any]:
        """Compute brightness, blur, and mean image baselines."""
        brightness_values = []
        blur_values = []
        images = []

        for path in image_paths:
            img = load_image_bgr(path)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            brightness_values.append(float(np.mean(gray)))
            blur_values.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
            images.append(cv2.resize(img, INPUT_SIZE))

        if not images:
            return {"mean_brightness": 0.0, "median_blur": 0.0, "mean_image": None}

        mean_image = np.mean(images, axis=0).astype(np.uint8)

        return {
            "mean_brightness": float(np.mean(brightness_values)),
            "median_blur": float(np.median(blur_values)),
            "mean_image": mean_image,
        }

    def _generate_version(self) -> str:
        timestamp = time.strftime("%Y%m%d%H%M%S")
        suffix = hashlib.md5(str(time.time()).encode()).hexdigest()[:6]
        return f"v{timestamp}-{suffix}"

    # -- inference ----------------------------------------------------------

    def predict(self, image_bgr: np.ndarray,
                score_mask: np.ndarray | None = None) -> Dict[str, Any]:
        """Predict anomaly score and heatmap for a single image.

        `score_mask` (optional) restricts scoring to the canonical product
        region; missing parts inside that region still raise the score.
        Returns raw_score (max of patch map), normalized_score (0-1) and
        the (H, W) anomaly map.
        """
        if self.memory_bank is None:
            raise RuntimeError("Model not trained or loaded")

        self.eval()
        tensor = preprocess_image(image_bgr, self.input_size).to(self.device)

        with torch.no_grad():
            features = self.extract_features(tensor)          # (1, C, H, W)
            patch_map = self._score_image(features, self.memory_bank,
                                          score_mask=score_mask)

        raw_score = float(patch_map.max())
        normalized = float(np.clip(0.5 * raw_score / self.ref_score, 0.0, 1.0))

        return {
            "raw_score": raw_score,
            "normalized_score": normalized,
            "anomaly_map": patch_map.cpu().numpy(),
        }

    # -- persistence --------------------------------------------------------

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save({
            "memory_bank": self.memory_bank,
            "ref_score": self.ref_score,
            "model_version": self.model_version,
            "training_stats": self.training_stats,
            "n_training_images": self.n_training_images,
            "created_at": self.created_at,
            "parent_version": self.parent_version,
            "coreset_ratio": self.coreset_ratio,
            "backbone": self.backbone.config(),
        }, path)

    def load(self, path: str):
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        self.memory_bank = checkpoint["memory_bank"]
        self.ref_score = checkpoint["ref_score"]
        self.model_version = checkpoint["model_version"]
        self.training_stats = checkpoint["training_stats"]
        self.n_training_images = checkpoint["n_training_images"]
        self.created_at = checkpoint["created_at"]
        self.parent_version = checkpoint.get("parent_version")
        self.coreset_ratio = checkpoint.get("coreset_ratio", COSET_RATIO)
        self._use_backbone_config(checkpoint.get("backbone"))
        self.to(self.device)
        self.eval()
        return self


# ---------------------------------------------------------------------------
# Verdict / certainty / explanation / setup checks
# ---------------------------------------------------------------------------

def create_overlay(image: np.ndarray, anomaly_map: np.ndarray, threshold: float,
                   opacity: float = 0.5,
                   ref_score: Optional[float] = None) -> np.ndarray:
    """Create a heatmap overlay of the anomaly map on a BGR image (RGB out).

    When ``ref_score`` is given the map is calibrated exactly like the image
    score (0.5 * raw / ref), so a passing unit renders calm instead of having
    its tiny residual differences stretched to full red by min-max scaling.
    """
    h, w = image.shape[:2]
    map_resized = cv2.resize(anomaly_map, (w, h))

    if ref_score and ref_score > 0:
        calibrated = np.clip(0.5 * map_resized / ref_score, 0.0, 1.0)
        map_normalized = (calibrated * 255).astype(np.uint8)
    else:
        map_min, map_max = map_resized.min(), map_resized.max()
        if map_max > map_min:
            map_normalized = (((map_resized - map_min) / (map_max - map_min)) * 255
                              ).astype(np.uint8)
        else:
            map_normalized = np.zeros_like(map_resized, dtype=np.uint8)

    heatmap = cv2.applyColorMap(map_normalized, cv2.COLORMAP_JET)
    heatmap = cv2.cvtColor(heatmap, cv2.COLOR_BGR2RGB)
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    return cv2.addWeighted(image_rgb, 1 - opacity, heatmap, opacity, 0)


def compute_verdict(score: float, threshold: float = DEFAULT_THRESHOLD,
                    delta: float = DEFAULT_DELTA) -> str:
    """Compute PASS/REVIEW/FAIL verdict from normalized score."""
    if delta == 0:
        return "PASS" if score < threshold else "FAIL"
    if score < threshold - delta:
        return "PASS"
    elif score < threshold + delta:
        return "REVIEW"
    else:
        return "FAIL"


def compute_certainty(score: float, verdict: str, threshold: float = DEFAULT_THRESHOLD,
                      delta: float = DEFAULT_DELTA, setup_status: str = "OK",
                      area_pct: float = 0.0) -> Tuple[str, str]:
    """Compute decision certainty level and reason."""
    margin = abs(score - threshold)

    if verdict == "REVIEW":
        return "Low", "Score is in the review zone."

    if margin < delta:
        level = "Low"
        reason = "Score is close to the configured limit."
    elif margin < 0.20:
        level = "Medium"
        reason = "Score is moderately far from the configured limit."
    else:
        level = "High"
        reason = "Score is far from the configured limit."

    if setup_status in ("Caution", "Poor") or area_pct > 25:
        if level == "High":
            level = "Medium"
            reason = "Setup quality or widespread area reduces certainty."
        elif level == "Medium":
            level = "Low"
            reason = "Setup quality or widespread area reduces certainty."

    return level, reason


def generate_explanation(anomaly_map: np.ndarray, threshold: float = DEFAULT_THRESHOLD,
                         ref_score: Optional[float] = None,
                         image_shape: Tuple[int, int] = None) -> Dict[str, Any]:
    """Generate plain-language explanation from an anomaly map.

    When `ref_score` is provided the map is converted to the same calibrated
    score scale as the image verdict (pixel score = clip(0.5 * raw / ref, 0, 1)),
    so a passing unit cannot produce a "widespread difference" message from
    per-image stretching. Without `ref_score`, legacy min-max normalisation is
    used (kept for tests and callers without a model reference).
    """
    if image_shape is None:
        image_shape = anomaly_map.shape

    if ref_score and ref_score > 0:
        normalized_map = np.clip(0.5 * anomaly_map / ref_score, 0.0, 1.0)
    else:
        map_min, map_max = anomaly_map.min(), anomaly_map.max()
        if map_max > map_min:
            normalized_map = (anomaly_map - map_min) / (map_max - map_min)
        else:
            normalized_map = np.zeros_like(anomaly_map)

    hot_mask = normalized_map >= threshold

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        hot_mask.astype(np.uint8), connectivity=8
    )

    min_size = 0.005 * anomaly_map.shape[0] * anomaly_map.shape[1]
    valid_regions = []
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area >= min_size:
            valid_regions.append({
                "label": i,
                "area": area,
                "centroid": centroids[i],
            })

    if not valid_regions:
        return {
            "explanation": "No unusual areas found.",
            "region_label": None,
            "area_pct": 0.0,
            "intensity": None,
            "n_regions": 0,
        }

    valid_regions.sort(key=lambda r: r["area"], reverse=True)
    largest = valid_regions[0]

    total_pixels = anomaly_map.shape[0] * anomaly_map.shape[1]
    area_pct = (largest["area"] / total_pixels) * 100

    cx, cy = largest["centroid"]
    h, w = anomaly_map.shape
    row = min(max(int(cy / (h / 3)), 0), 2)
    col = min(max(int(cx / (w / 3)), 0), 2)
    row_labels = ["top", "middle", "bottom"]
    col_labels = ["left", "center", "right"]
    region_label = f"{row_labels[row]}-{col_labels[col]}"

    peak_score = normalized_map[labels == largest["label"]].max()
    intensity = "High" if peak_score > threshold + 0.2 else "Medium"

    if area_pct > 25:
        explanation = "Widespread difference; check lighting and unit position."
    else:
        explanation = (
            f"Unusual area in the {region_label}, about {area_pct:.0f}% of the unit. "
            "Inspect this region manually."
        )

    return {
        "explanation": explanation,
        "region_label": region_label,
        "area_pct": area_pct,
        "intensity": intensity,
        "n_regions": len(valid_regions),
    }


def compute_setup_status(image: np.ndarray,
                         training_stats: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Compute setup quality status from an image and training baseline."""
    reasons = []
    failures = 0

    if not training_stats or "mean_brightness" not in training_stats:
        return "Caution", ["No training baseline available"]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    current_brightness = float(np.mean(gray))
    current_blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    baseline_brightness = training_stats["mean_brightness"]
    if baseline_brightness > 0:
        brightness_ratio = current_brightness / baseline_brightness
        if brightness_ratio < 0.75 or brightness_ratio > 1.25:
            reasons.append(
                f"Brightness differs from training baseline ({brightness_ratio:.2f}x)"
            )
            failures += 1

    baseline_blur = training_stats.get("median_blur", 0)
    if baseline_blur > 0:
        blur_ratio = current_blur / baseline_blur
        if blur_ratio < 0.5:
            reasons.append(
                f"Image is blurrier than training baseline ({blur_ratio:.2f}x)"
            )
            failures += 1

    if "mean_image" in training_stats and training_stats["mean_image"] is not None:
        mean_img = training_stats["mean_image"]
        mean_gray = cv2.cvtColor(mean_img, cv2.COLOR_BGR2GRAY)
        current_resized = cv2.resize(gray, (mean_gray.shape[1], mean_gray.shape[0]))
        try:
            shift, _ = cv2.phaseCorrelate(np.float32(mean_gray),
                                          np.float32(current_resized))
            shift_magnitude = float(np.sqrt(shift[0] ** 2 + shift[1] ** 2))
            max_shift = 0.05 * gray.shape[1]
            if shift_magnitude > max_shift:
                reasons.append(
                    f"Unit position shifted from training baseline ({shift_magnitude:.1f}px)"
                )
                failures += 1
        except cv2.error:
            pass

    if failures == 0:
        return "OK", ["All setup checks passed"]
    elif failures == 1:
        return "Caution", reasons
    else:
        return "Poor", reasons
