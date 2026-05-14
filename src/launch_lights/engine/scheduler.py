"""Drift-corrected fixed-rate loop.

Ported from k2-lights's scheduler.py and simplified — no audio, no beat
phase, no message rate cap. The scheduler does one job: call ``tick(now)``
at ``fps`` Hz, skipping ticks (not catching up) when behind by more than
one interval.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class SchedulerStats:
    ticks: int = 0
    skips: int = 0
    max_drift_s: float = 0.0


class Scheduler:
    def __init__(
        self,
        tick: Callable[[float, float], None],
        *,
        fps: float = 30.0,
    ) -> None:
        """
        ``tick(elapsed, dt)`` is called once per frame on the calling thread.
        ``elapsed`` is seconds since ``run()`` started, ``dt`` is seconds
        since the previous tick (perf_counter delta).
        """
        if fps <= 0:
            raise ValueError(f"fps must be positive: {fps}")
        self.tick = tick
        self.interval = 1.0 / fps
        self._stop = False
        self.stats = SchedulerStats()

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        start = time.perf_counter()
        next_tick = start
        last_now = start
        while not self._stop:
            now = time.perf_counter()
            elapsed = now - start
            dt = now - last_now
            last_now = now

            try:
                self.tick(elapsed, dt)
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick raised")

            self.stats.ticks += 1

            next_tick += self.interval
            slack = next_tick - time.perf_counter()
            if slack < -self.interval:
                skipped = int(-slack // self.interval)
                self.stats.skips += skipped
                next_tick += skipped * self.interval
                slack = next_tick - time.perf_counter()
            drift = max(0.0, -slack)
            if drift > self.stats.max_drift_s:
                self.stats.max_drift_s = drift
            if slack > 0:
                time.sleep(slack)
