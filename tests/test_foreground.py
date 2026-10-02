"""Unit tests for product-agnostic foreground modelling."""

import numpy as np
import pytest

from service.foreground import (
    BackgroundModel,
    background_false_activation,
    cleanup_mask,
    foreground_from_border,
    mask_metrics,
    missing_region_fraction,
    presence_regions,
    proposal_from_frames,
    proposal_quality_warnings,
    propose_presence_regions,
    region_coverage,
)

SIZE = 320


def _background(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(128, 3, (SIZE, SIZE, 3)), 0, 255).astype(np.uint8)


def _with_rect(base: np.ndarray, x0: int, y0: int, x1: int, y1: int,
               value: int = 230) -> np.ndarray:
    image = base.copy()
    image[y0:y1, x0:x1] = value
    return image


def _rect_mask(x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    mask = np.zeros((SIZE, SIZE), dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


class TestBackgroundModel:
    def test_detects_object(self):
        frames = [_background(i) for i in range(5)]
        model = BackgroundModel.build(frames)
        object_frame = _with_rect(frames[0], 100, 120, 200, 240)
        mask = model.foreground_mask(object_frame)
        truth = _rect_mask(100, 120, 200, 240)
        iou = mask_metrics(mask, truth)["iou"]
        assert iou > 0.7, f"IoU too low: {iou}"

    def test_ignores_empty_scene(self):
        frames = [_background(i) for i in range(5)]
        model = BackgroundModel.build(frames)
        mask = model.foreground_mask(_background(99))
        assert mask.sum() == 0

    def test_save_load_roundtrip(self, tmp_path):
        frames = [_background(i) for i in range(4)]
        model = BackgroundModel.build(frames)
        path = tmp_path / "bg.npz"
        model.save(path)
        loaded = BackgroundModel.load(path)
        assert np.allclose(model.median, loaded.median)

    def test_requires_frames(self):
        with pytest.raises(ValueError):
            BackgroundModel.build([_background(0), _background(1)])


class TestProposal:
    def test_union_covers_positions(self):
        frames = [_background(i) for i in range(5)]
        model = BackgroundModel.build(frames)
        good = [
            _with_rect(frames[0], 80, 100, 160, 220),
            _with_rect(frames[1], 120, 100, 200, 220),
            _with_rect(frames[2], 100, 100, 180, 220),
        ]
        # Default (>=50% of frames): the region the product reliably occupies.
        common = proposal_from_frames(good, background=model)
        assert common[150, 140]      # overlap of all three positions
        assert not common[150, 90]   # only one frame -> not "reliable"

        # Low threshold behaves like a union of observed positions.
        union = proposal_from_frames(good, background=model,
                                     coverage_fraction=0.3)
        assert union[150, 90]
        assert union[150, 190]

    def test_border_fallback(self):
        frames = [_background(i) for i in range(4)]
        good = [_with_rect(frames[0], 90, 110, 210, 230)]
        proposal = proposal_from_frames(good, background=None)
        truth = _rect_mask(90, 110, 210, 230)
        assert mask_metrics(proposal, truth)["iou"] > 0.6

    def test_foreground_from_border_plain(self):
        frame = _background(7)
        assert foreground_from_border(frame).sum() == 0


class TestMetrics:
    def test_identical(self):
        mask = _rect_mask(50, 50, 150, 150)
        assert mask_metrics(mask, mask)["iou"] == 1.0

    def test_disjoint(self):
        assert mask_metrics(_rect_mask(0, 0, 50, 50),
                            _rect_mask(200, 200, 250, 250))["iou"] == 0.0

    def test_false_activation(self):
        frames = [_background(i) for i in range(4)]
        model = BackgroundModel.build(frames)
        frame = _with_rect(frames[0], 100, 100, 200, 200)
        approved = _rect_mask(100, 100, 200, 200)
        assert background_false_activation(frame, approved, model) < 0.2


class TestPresenceRegions:
    def test_region_and_missing_part(self):
        good_masks = [_rect_mask(100, 100, 220, 220) for _ in range(6)]
        regions = presence_regions(good_masks)
        assert regions.sum() > 0

        intact = _rect_mask(100, 100, 220, 220)
        assert region_coverage(intact, regions) > 0.95
        assert missing_region_fraction(intact, regions, 0.9) == 0.0

        # A missing part: the lower half of the product is absent.
        missing = _rect_mask(100, 100, 220, 160)
        coverage = region_coverage(missing, regions)
        assert coverage < 0.6
        assert missing_region_fraction(missing, regions, 0.9) > 0.2


class TestCleanup:
    def test_fills_holes_and_drops_specks(self):
        mask = _rect_mask(100, 100, 200, 200).astype(np.uint8)
        mask[140:160, 140:160] = 0          # hole
        mask[10:14, 10:14] = 1              # speck
        cleaned = cleanup_mask(mask)
        assert cleaned[150, 150]            # hole filled
        assert not cleaned[12, 12]          # speck dropped
        assert cleaned[150, 120]            # body kept

    def test_border_barrier_does_not_swallow_frame(self):
        # A bar that touches the top and bottom edges partitions the
        # background. The half that the flood seed cannot reach must stay
        # background; before the padding fix it was filled as a "hole"
        # (a 2% mask turned into 100%).
        mask = np.zeros((SIZE, SIZE), dtype=np.uint8)
        mask[:, 100:120] = 1
        cleaned = cleanup_mask(mask)
        assert cleaned.mean() < 0.10, cleaned.mean()
        assert cleaned[160, 110]            # the bar is kept
        assert not cleaned[160, 200]        # trapped background is not filled


class TestProposePresenceRegions:
    def test_drops_border_clutter(self):
        good = []
        for _ in range(6):
            frame = _rect_mask(100, 100, 220, 220)
            frame[0:40, 0:40] = True        # clutter touching the corner
            good.append(frame)
        regions, used = propose_presence_regions(
            good, approved_mask=np.ones((SIZE, SIZE), dtype=bool))
        assert used == 0.95
        assert regions[160, 160]
        assert not regions[20, 20]

    def test_falls_back_when_fill_is_too_strict(self):
        good = [_rect_mask(100, 100, 220, 220) for _ in range(4)]
        good.append(_rect_mask(10, 10, 60, 60))   # product elsewhere
        regions, used = propose_presence_regions(good, approved_mask=None)
        assert used == 0.8
        assert regions[160, 160]

    def test_empty_when_nothing_is_stable(self):
        good = [_rect_mask(10 + i * 50, 10, 60 + i * 50, 60)
                for i in range(5)]
        regions, used = propose_presence_regions(good)
        assert used is None
        assert not regions.any()

    def test_intersects_with_approved_mask(self):
        good = [_rect_mask(100, 100, 220, 220) for _ in range(6)]
        approved = _rect_mask(0, 0, 160, 160)
        regions, used = propose_presence_regions(good, approved_mask=approved)
        assert used == 0.95
        assert regions[120, 120]
        assert not regions[200, 200]


class TestProposalWarnings:
    def test_warns_on_unstable_framing(self):
        good = [_rect_mask(10 + i * 50, 10, 60 + i * 50, 60)
                for i in range(5)]
        proposal = _rect_mask(50, 50, 100, 100)
        regions, used = propose_presence_regions(good)
        warnings = proposal_quality_warnings(good, proposal, regions, used)
        assert any("no stable product regions" in w for w in warnings)
        assert any("covers only" in w for w in warnings)

    def test_warns_on_invisible_frame(self):
        good = [_rect_mask(100, 100, 220, 220) for _ in range(5)]
        good.append(np.zeros((SIZE, SIZE), dtype=bool))   # nothing detected
        proposal = _rect_mask(100, 100, 220, 220)
        regions, used = propose_presence_regions(good, approved_mask=proposal)
        warnings = proposal_quality_warnings(good, proposal, regions, used)
        assert any("background detection is unreliable" in w for w in warnings)

    def test_no_warnings_for_stable_set(self):
        good = [_rect_mask(100, 100, 220, 220) for _ in range(6)]
        proposal = _rect_mask(100, 100, 220, 220)
        regions, used = propose_presence_regions(good, approved_mask=proposal)
        assert proposal_quality_warnings(good, proposal, regions, used) == []
