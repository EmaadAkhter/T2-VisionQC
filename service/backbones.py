"""Pluggable frozen feature extractors for PatchCore.

The quality-control engine is product-agnostic: the backbone provides generic
visual understanding, and per-product normality lives in the memory bank.
Two backbones are supported:

- ``wide_resnet50``  : the original ImageNet WideResNet-50 (layers 2+3).
- ``dinov2_*``       : DINOv2 ViT patch tokens (self-supervised, 142M images),
  far stronger at transferring to objects it was never trained on.

Every backbone exposes the same tiny interface: ``input_size``, ``extract``,
``to``, ``train``, ``eval``. ``extract`` returns an ``(N, C, H, W)`` tensor.
"""

from __future__ import annotations

from typing import Sequence

import torch
import torch.nn.functional as F
from torchvision.models import wide_resnet50_2, Wide_ResNet50_2_Weights

# DINOv2 checkpoints hosted by timm. ``reg4`` variants add register tokens,
# which produce cleaner patch features.
DINOV2_MODELS = {
    "dinov2_vits14": "vit_small_patch14_reg4_dinov2.lvd142m",
    "dinov2_vitb14": "vit_base_patch14_reg4_dinov2.lvd142m",
}

DINOV2_PATCH_SIZE = 14


class WideResNet50Backbone:
    """Original PatchCore backbone: WideResNet-50 conv2/conv3 features."""

    name = "wide_resnet50"

    def __init__(self, input_size: tuple[int, int] = (320, 320)):
        model = wide_resnet50_2(weights=Wide_ResNet50_2_Weights.DEFAULT)
        self.conv1 = model.conv1
        self.bn1 = model.bn1
        self.relu = model.relu
        self.maxpool = model.maxpool
        self.layer1 = model.layer1
        self.layer2 = model.layer2
        self.layer3 = model.layer3
        self.input_size = tuple(input_size)
        self.eval()
        for param in self.parameters():
            param.requires_grad = False

    def parameters(self):
        for module in self._modules_list():
            yield from module.parameters()

    def _modules_list(self):
        return [self.conv1, self.bn1, self.layer1, self.layer2, self.layer3]

    def to(self, device):
        for module in self._modules_list():
            module.to(device)
        return self

    def train(self):
        for module in self._modules_list():
            module.train()
        return self

    def eval(self):
        for module in self._modules_list():
            module.eval()
        return self

    def extract(self, images: torch.Tensor) -> torch.Tensor:
        """Concatenate layer2+layer3 features with 3x3 local aggregation."""
        with torch.no_grad():
            x = self.conv1(images)
            x = self.bn1(x)
            x = self.relu(x)
            x = self.maxpool(x)
            x = self.layer1(x)
            feat2 = self.layer2(x)
            feat3 = self.layer3(feat2)

            if feat3.shape[2:] != feat2.shape[2:]:
                feat3 = F.interpolate(
                    feat3, size=feat2.shape[2:], mode="bilinear",
                    align_corners=False,
                )

            features = torch.cat([feat2, feat3], dim=1)
            features = F.avg_pool2d(features, kernel_size=3, stride=1, padding=1)
        return features

    def config(self) -> dict:
        return {"name": self.name, "input_size": list(self.input_size)}


class DINOv2Backbone:
    """DINOv2 ViT patch tokens as generic anomaly-detection features.

    Patch tokens from one or more transformer blocks are concatenated.
    Per-patch L2 normalisation makes the memory-bank distance a cosine
    distance, which is stable across object types.
    """

    name = "dinov2"

    def __init__(
        self,
        model_name: str = DINOV2_MODELS["dinov2_vits14"],
        input_size: int = 448,
        layers: Sequence[int] = (-1,),
        normalize: bool = True,
    ):
        import timm  # imported lazily: only DINOv2 users pay the import cost

        if input_size % DINOV2_PATCH_SIZE != 0:
            raise ValueError(
                f"input_size must be a multiple of {DINOV2_PATCH_SIZE}, "
                f"got {input_size}"
            )
        self.timm_name = model_name
        self.model = timm.create_model(
            model_name, pretrained=True, num_classes=0, img_size=input_size,
        )
        n_blocks = len(self.model.blocks)
        self.layers = tuple(layer % n_blocks for layer in layers)
        self.input_size = (input_size, input_size)
        self.normalize = normalize
        self.eval()
        for param in self.model.parameters():
            param.requires_grad = False

    def parameters(self):
        yield from self.model.parameters()

    def to(self, device):
        self.model.to(device)
        return self

    def train(self):
        self.model.train()
        return self

    def eval(self):
        self.model.eval()
        return self

    def extract(self, images: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            _, intermediates = self.model.forward_intermediates(
                images,
                indices=list(self.layers),
                output_fmt="NCHW",
                intermediates_only=False,
            )
        if isinstance(intermediates, torch.Tensor):
            intermediates = [intermediates]

        target = intermediates[0].shape[2:]
        feats = []
        for feat in intermediates:
            if feat.shape[2:] != target:
                feat = F.interpolate(
                    feat, size=target, mode="bilinear", align_corners=False,
                )
            if self.normalize:
                feat = F.normalize(feat, dim=1)
            feats.append(feat)
        return feats[0] if len(feats) == 1 else torch.cat(feats, dim=1)

    def config(self) -> dict:
        return {
            "name": self.name,
            "timm_name": self.timm_name,
            "input_size": self.input_size[0],
            "layers": list(self.layers),
            "normalize": self.normalize,
        }


def build_backbone(name: str, **kwargs):
    """Factory: backbone name + kwargs -> extractor instance."""
    if name == "wide_resnet50":
        return WideResNet50Backbone(**kwargs)
    if name in DINOV2_MODELS:
        return DINOv2Backbone(model_name=DINOV2_MODELS[name], **kwargs)
    raise ValueError(f"Unknown backbone: {name}")


def dinov2_weights_available(model_name: str | None = None) -> bool:
    """True when DINOv2 weights are already cached locally.

    Used to pick the strongest engine without risking a blocked first-run
    download (packaged/offline installs fall back to WideResNet).
    """
    model_name = model_name or DINOV2_MODELS["dinov2_vits14"]
    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache(
            f"timm/{model_name}", "model.safetensors"
        )
        if isinstance(cached, str):
            return True
    except Exception:  # noqa: BLE001 - offline/old hub versions
        pass
    return False


LARGE_TRAIN_SET = 50  # above this, WideResNet-50 is on par and faster


def resolve_default_engine(n_images: int | None = None) -> tuple[str, dict]:
    """Best available detection engine for a new training run.

    DINOv2 features generalize better on small onboarding sets (the common
    case, and the one the POC showed failing). With plenty of good images
    WideResNet-50 matches its accuracy and is substantially faster. Falls back
    to WideResNet-50 when DINOv2 weights are not cached/bundled (offline).
    """
    if dinov2_weights_available():
        if n_images is None or n_images <= LARGE_TRAIN_SET:
            return "dinov2_vits14", {"input_size": 448}
    return "wide_resnet50", {}


def engine_display_name(backbone_name: str) -> str:
    """Human-readable engine label stored with model metadata."""
    if backbone_name.startswith("dinov2"):
        return "DINOv2 ViT-S/14"
    return "WideResNet-50"


def backbone_from_config(config: dict | None):
    """Rebuild a backbone from a saved checkpoint config.

    Missing/legacy configs fall back to the original WideResNet-50.
    """
    if not config:
        return WideResNet50Backbone()
    name = config.get("name", "wide_resnet50")
    if name == "wide_resnet50":
        return WideResNet50Backbone(
            input_size=tuple(config.get("input_size", (320, 320))),
        )
    if name == "dinov2":
        return DINOv2Backbone(
            model_name=config.get("timm_name", DINOV2_MODELS["dinov2_vits14"]),
            input_size=config.get("input_size", 448),
            layers=tuple(config.get("layers", (-1,))),
            normalize=config.get("normalize", True),
        )
    raise ValueError(f"Unknown backbone config: {config}")
