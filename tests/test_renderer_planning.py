"""Plan computation tests — no I/O, no device, no MIDI."""
from __future__ import annotations

import numpy as np

from launch_lights.engine.frame import OFF, Frame, RGB
from launch_lights.engine.plan import (
    NoOpPlan,
    RGBDiffPlan,
    RGBFullFramePlan,
)
from launch_lights.engine.renderer import DIFF_TO_FULL_FRAME_THRESHOLD, Renderer


def test_first_frame_in_full_frame_mode_emits_full_frame():
    r = Renderer(mode="rgb", prefer_full_frame=True)
    frame = Frame.flood(RGB(10, 20, 30))
    plan = r.plan(frame)
    assert isinstance(plan, RGBFullFramePlan)
    assert plan.grid.shape == (8, 8, 3)
    assert plan.grid[0, 0].tolist() == [10, 20, 30]


def test_full_frame_mode_returns_noop_when_unchanged():
    r = Renderer(mode="rgb", prefer_full_frame=True)
    frame = Frame.flood(RGB(10, 20, 30))
    r.commit(r.plan(frame))
    assert isinstance(r.plan(frame), NoOpPlan)


def test_diff_mode_returns_noop_when_nothing_changes():
    r = Renderer(mode="rgb", prefer_full_frame=False)
    frame = Frame.empty()
    # last_rgb starts all OFF, frame is also all OFF -> no change
    assert isinstance(r.plan(frame), NoOpPlan)


def test_diff_mode_single_cell_emits_rgb_diff():
    r = Renderer(mode="rgb", prefer_full_frame=False)
    frame = Frame.empty().with_cell((3, 4), RGB(63, 0, 0))
    plan = r.plan(frame)
    assert isinstance(plan, RGBDiffPlan)
    assert plan.cells == {(3, 4): RGB(63, 0, 0)}


def test_diff_mode_promotes_to_full_frame_above_threshold():
    r = Renderer(mode="rgb", prefer_full_frame=False)
    # All these RGBs are non-zero so every cell genuinely differs from OFF
    cells = {(r_, c): RGB(8 + c, 8 + r_, 8) for r_ in range(2) for c in range(8)}
    assert len(cells) == DIFF_TO_FULL_FRAME_THRESHOLD
    frame = Frame(cells=cells)
    plan = r.plan(frame)
    assert isinstance(plan, RGBFullFramePlan)


def test_commit_updates_last_rgb_for_diff_plan():
    r = Renderer(mode="rgb", prefer_full_frame=False)
    frame = Frame.empty().with_cell((0, 0), RGB(63, 0, 0))
    plan = r.plan(frame)
    r.commit(plan)
    assert r.last_rgb[(0, 0)] == RGB(63, 0, 0)
    # Second time around, that cell is no longer different
    assert isinstance(r.plan(frame), NoOpPlan)


def test_commit_updates_last_rgb_for_full_frame_plan():
    r = Renderer(mode="rgb", prefer_full_frame=True)
    frame = Frame.flood(RGB(5, 5, 5))
    r.commit(r.plan(frame))
    assert r.last_rgb[(7, 7)] == RGB(5, 5, 5)


def test_blackout_resets_last_state():
    r = Renderer(mode="rgb", prefer_full_frame=True)
    r.commit(r.plan(Frame.flood(RGB(63, 63, 63))))
    r.blackout()
    for pos, rgb in r.last_rgb.items():
        assert rgb == OFF


def test_palette_mode_requires_quantizer():
    import pytest
    with pytest.raises(ValueError):
        Renderer(mode="palette", palette=None)


class _IdentityPalette:
    """Test palette that hashes RGB to a deterministic index 0..127."""
    def nearest(self, rgb: RGB) -> int:
        return (rgb.r + rgb.g + rgb.b) % 128


def test_palette_mode_emits_palette_diff():
    from launch_lights.engine.plan import PaletteDiffPlan
    r = Renderer(mode="palette", palette=_IdentityPalette())
    frame = Frame.empty().with_cell((0, 0), RGB(10, 20, 30))
    plan = r.plan(frame)
    assert isinstance(plan, PaletteDiffPlan)
    assert plan.cells == {(0, 0): (10 + 20 + 30) % 128}
