"""Request limits for the public demo: generous for a person clicking through it, not for a script.

Fixed windows, in memory. One instance serves the demo, so there is nothing to share between processes.
Per-visitor limits key on the client address Netlify reports, which a determined caller can vary; the global
ceiling cannot be varied, and it is what bounds the demo's compute bill.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Window:
    limit: int
    seconds: float
    clock: Callable[[], float] = time.monotonic
    _counts: dict[str, tuple[float, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def hit(self, key: str) -> int:
        """Count one request. Returns 0 if it is allowed, otherwise the seconds until the window resets."""
        now = self.clock()
        with self._lock:
            start, count = self._counts.get(key, (now, 0))
            if now - start >= self.seconds:
                start, count = now, 0
            if count >= self.limit:
                return max(1, math.ceil(self.seconds - (now - start)))
            self._counts[key] = (start, count + 1)
            if len(self._counts) > 10_000:  # forget visitors whose window has ended
                self._counts = {k: v for k, v in self._counts.items() if now - v[0] < self.seconds}
            return 0


@dataclass
class DemoLimits:
    per_visitor: Window = field(default_factory=lambda: Window(300, 300))
    writes_per_visitor: Window = field(default_factory=lambda: Window(60, 300))
    everyone: Window = field(default_factory=lambda: Window(5_000, 300))

    def check(self, visitor: str, write: bool) -> int:
        """0 if the request may proceed, otherwise seconds to wait. Checked narrowest first."""
        for window, key in ((self.writes_per_visitor, visitor) if write else (None, None),
                            (self.per_visitor, visitor), (self.everyone, "*")):
            if window is not None and (wait := window.hit(key)):
                return wait
        return 0
