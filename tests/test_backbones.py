"""Backbone registry tests: config roundtrips and legacy compatibility."""

from __future__ import annotations

import numpy as np
import pytest

from service.backbones import (
    DINOV2_MODELS,
    WideResNet50Backbone,
    backbone_from_config,
    build_backbone,
)


def test_unknown_backbone_raises():
    with pytest.raises(ValueError):
        build_backbone("not_a_backbone")


def test_unknown_backbone_config_raises():
    with pytest.raises(ValueError):
        backbone_from_config({"name": "mystery"})


def test_wide_resnet_config_roundtrip():
    backbone = WideResNet50Backbone()
    config = backbone.config()
    assert config["name"] == "wide_resnet50"
    rebuilt = backbone_from_config(config)
    assert isinstance(rebuilt, WideResNet50Backbone)
    assert tuple(rebuilt.input_size) == tuple(backbone.input_size)


def test_legacy_config_defaults_to_wide_resnet():
    # Old checkpoints have no "backbone" field at all.
    rebuilt = backbone_from_config(None)
    assert isinstance(rebuilt, WideResNet50Backbone)


def test_wide_resnet_extract_shapes():
    backbone = WideResNet50Backbone(input_size=(160, 160))
    import torch

    feats = backbone.extract(torch.zeros(2, 3, 160, 160))
    assert feats.shape[0] == 2
    assert feats.shape[2:] == (20, 20)  # /8: layer2 resolution


@pytest.mark.slow
def test_dinov2_config_roundtrip_downloads_weights():
    """Requires network on first run; verifies the timm path end to end."""
    backbone = build_backbone(
        "dinov2_vits14", input_size=224, layers=(-1,),
    )
    config = backbone.config()
    assert config["timm_name"] == DINOV2_MODELS["dinov2_vits14"]
    assert config["layers"] == [11]

    import torch

    feats = backbone.extract(torch.zeros(1, 3, 224, 224))
    assert feats.shape[0] == 1
    assert feats.shape[2:] == (16, 16)  # 224 / patch 14

    rebuilt = backbone_from_config(config)
    assert rebuilt.config() == config

    # Per-patch normalisation makes every patch vector unit length.
    norms = np.linalg.norm(feats[0].numpy(), axis=0)
    assert np.allclose(norms, 1.0, atol=1e-4)
