"""Live-inspection decision logic (pure Python, unit-testable).

Kept free of Qt and camera code so the auto-capture policy can be tested
without a display or hardware.
"""

from __future__ import annotations

import time


class AutoCaptureDecider:
    """Decides when a live verdict should be logged automatically.

    Fires when a capture-worthy verdict — FAIL or REVIEW by default —
    appears for ``min_consecutive`` processed frames in a row. After a
    capture the decider disarms: the scene must show ``rearm_consecutive``
    non-capture frames (unit removed, next unit arriving) before another
    capture can fire. ``cooldown_s`` is a secondary guard for very fast
    re-arming streams.

    This models the real line behaviour: one unit that needs attention is
    logged once with its evidence images, not once per second. Pass
    ``capture_verdicts=("FAIL",)`` to keep the older FAIL-only policy.
    """

    def __init__(self, min_consecutive: int = 2, cooldown_s: float = 3.0,
                 rearm_consecutive: int = 2,
                 capture_verdicts: tuple[str, ...] = ("FAIL", "REVIEW")):
        if min_consecutive < 1:
            raise ValueError("min_consecutive must be >= 1")
        if cooldown_s < 0:
            raise ValueError("cooldown_s must be >= 0")
        if rearm_consecutive < 1:
            raise ValueError("rearm_consecutive must be >= 1")
        if not capture_verdicts:
            raise ValueError("capture_verdicts must not be empty")
        self.min_consecutive = min_consecutive
        self.cooldown_s = cooldown_s
        self.rearm_consecutive = rearm_consecutive
        self.capture_verdicts = tuple(capture_verdicts)
        self._streak = 0
        self._good_streak = 0
        self._armed = True
        self._last_capture: float | None = None

    @property
    def streak(self) -> int:
        """Consecutive capture-worthy frames seen so far."""
        return self._streak

    @property
    def armed(self) -> bool:
        return self._armed

    def update(self, verdict: str, now: float | None = None) -> bool:
        """Feed one verdict; returns True exactly once per capture trigger."""
        now = time.monotonic() if now is None else now

        if verdict not in self.capture_verdicts:
            self._streak = 0
            self._good_streak += 1
            if self._good_streak >= self.rearm_consecutive:
                self._armed = True
            return False

        self._good_streak = 0
        if not self._armed:
            return False
        self._streak += 1
        if self._streak < self.min_consecutive:
            return False
        if (self._last_capture is not None
                and now - self._last_capture < self.cooldown_s):
            return False

        self._last_capture = now
        self._streak = 0
        self._armed = False
        return True

    def reset(self) -> None:
        self._streak = 0
        self._good_streak = 0
        self._armed = True
        self._last_capture = None
