"""The scheduler skips (does not catch up) when a tick overruns its budget."""
from __future__ import annotations

import time

from launch_lights.engine.scheduler import Scheduler


def test_scheduler_runs_at_target_fps_without_overrun():
    sch = Scheduler(tick=lambda elapsed, dt: None, fps=200.0)

    def stop_after_n():
        if sch.stats.ticks >= 20:
            sch.stop()

    def tick(elapsed, dt):
        stop_after_n()

    sch.tick = tick
    t0 = time.perf_counter()
    sch.run()
    elapsed = time.perf_counter() - t0
    # 20 ticks at 200fps should take ~0.1 s; cap generously
    assert 0.05 < elapsed < 1.0
    assert sch.stats.ticks >= 20
    # No tick overran, so skips should be 0
    assert sch.stats.skips == 0


def test_scheduler_skips_when_tick_overruns():
    sch = Scheduler(tick=lambda elapsed, dt: None, fps=100.0)
    n = [0]

    def slow_tick(elapsed, dt):
        n[0] += 1
        if n[0] == 1:
            # Sleep for several intervals to force skips
            time.sleep(0.1)  # = 10 intervals at 100 fps
        if n[0] >= 5:
            sch.stop()

    sch.tick = slow_tick
    sch.run()
    assert sch.stats.skips > 0
