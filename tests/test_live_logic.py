"""Auto-capture decision tests (no Qt, no camera)."""

from __future__ import annotations

import pytest

from desktop.live_logic import AutoCaptureDecider


def test_single_fail_does_not_trigger():
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0)
    assert decider.update("FAIL", now=0.0) is False


def test_two_consecutive_fails_trigger_once():
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0)
    assert decider.update("FAIL", now=0.0) is False
    assert decider.update("FAIL", now=0.1) is True
    assert decider.armed is False


def test_disarmed_decider_ignores_failures_even_after_cooldown():
    """One unit sitting in view is logged once, not every cooldown."""
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=1.0)
    decider.update("FAIL", now=0.0)
    assert decider.update("FAIL", now=0.1) is True
    for timestamp in (0.5, 1.5, 5.0, 30.0):
        assert decider.update("FAIL", now=timestamp) is False


def test_good_frames_rearm_after_a_capture():
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0,
                                 rearm_consecutive=2)
    decider.update("FAIL", now=0.0)
    assert decider.update("FAIL", now=0.1) is True
    # One good frame is not enough...
    assert decider.update("PASS", now=0.2) is False
    assert decider.armed is False
    # ...the second one re-arms.
    assert decider.update("PASS", now=0.3) is False
    assert decider.armed is True
    # Next defective unit triggers again once the cooldown has passed.
    assert decider.update("FAIL", now=3.2) is False
    assert decider.update("FAIL", now=3.3) is True


def test_cooldown_blocks_fast_rearm():
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0,
                                 rearm_consecutive=1)
    decider.update("FAIL", now=0.0)
    assert decider.update("FAIL", now=0.1) is True
    assert decider.update("PASS", now=0.2) is False
    assert decider.update("FAIL", now=0.3) is False  # still in cooldown
    assert decider.update("FAIL", now=0.4) is False
    assert decider.update("FAIL", now=3.5) is True   # cooldown expired


def test_pass_resets_fail_streak():
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0)
    assert decider.update("FAIL", now=0.0) is False
    assert decider.update("PASS", now=0.1) is False
    assert decider.update("FAIL", now=0.2) is False


def test_review_captures_after_persistence():
    """Default policy: a REVIEW that persists is auto-logged with evidence."""
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0)
    assert decider.update("REVIEW", now=0.0) is False
    assert decider.update("REVIEW", now=0.1) is True
    assert decider.armed is False


def test_fail_only_mode_ignores_review():
    """Legacy policy: capture_verdicts=("FAIL",) keeps the old behavior."""
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0,
                                 rearm_consecutive=1,
                                 capture_verdicts=("FAIL",))
    decider.update("FAIL", now=0.0)
    assert decider.update("FAIL", now=0.1) is True
    assert decider.update("REVIEW", now=0.2) is False
    assert decider.armed is True


def test_mixed_fail_review_streak_triggers_once():
    decider = AutoCaptureDecider(min_consecutive=3, cooldown_s=3.0)
    assert decider.update("FAIL", now=0.0) is False
    assert decider.update("REVIEW", now=0.1) is False
    assert decider.update("FAIL", now=0.2) is True


def test_pass_resets_review_streak():
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0)
    assert decider.update("REVIEW", now=0.0) is False
    assert decider.update("PASS", now=0.1) is False
    assert decider.update("REVIEW", now=0.2) is False


def test_reset_clears_state():
    decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0)
    decider.update("FAIL", now=0.0)
    assert decider.streak == 1
    decider.reset()
    assert decider.streak == 0
    assert decider.armed is True
    assert decider.update("FAIL", now=0.1) is False


def test_invalid_params():
    with pytest.raises(ValueError):
        AutoCaptureDecider(min_consecutive=0)
    with pytest.raises(ValueError):
        AutoCaptureDecider(cooldown_s=-1)
    with pytest.raises(ValueError):
        AutoCaptureDecider(rearm_consecutive=0)
