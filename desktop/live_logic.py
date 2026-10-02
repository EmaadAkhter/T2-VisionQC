"""Live-inspection decision logic (pure Python, unit-testable).

Kept free of Qt and camera code so the auto-capture policy can be tested
without a display or hardware.
"""

from __future__ import annotations

import time


class AutoCaptureDecider:
    """Decides when a live FAIL should be logged automatically.

    Fires when a FAIL verdict appears for ``min_consecutive`` processed frames
    in a row. After a capture the decider disarms: the scene must show
    ``rearm_consecutive`` non-FAIL frames (unit removed, next unit arriving)
    before another capture can fire. ``cooldown_s`` is a secondary guard for
    very fast re-arming streams.

    This models the real line behaviour: one defective unit in front of the
    camera is logged once, not once per second.
    """

    def __init__(self, min_consecutive: int = 2, cooldown_s: float = 3.0,
                 rearm_consecutive: int = 2):
        if min_consecutive < 1:
            raise ValueError("min_consecutive must be >= 1")
        if cooldown_s < 0:
            raise ValueError("cooldown_s must be >= 0")
        if rearm_consecutive < 1:
            raise ValueError("rearm_consecutive must be >= 1")
        self.min_consecutive = min_consecutive
        self.cooldown_s = cooldown_s
        self.rearm_consecutive = rearm_consecutive
        self._streak = 0
        self._good_streak = 0
        self._armed = True
        self._last_capture: float | None = None

    @property
    def streak(self) -> int:
        """Consecutive FAIL frames seen so far."""
        return self._streak

    @property
    def armed(self) -> bool:
        return self._armed

    def update(self, verdict: str, now: float | None = None) -> bool:
        """Feed one verdict; returns True exactly once per capture trigger."""
        now = time.monotonic() if now is None else now

        if verdict != "FAIL":
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
