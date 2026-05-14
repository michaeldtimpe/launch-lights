"""Pure planning: Frame -> RenderPlan. No MIDI I/O lives here.

The transport (``device.launchpad_pro.LaunchpadProOut.execute``) consumes the
plan. The Renderer owns the only mutable "last state" — what the device is
believed to currently display — and decides whether to emit a sparse update,
a full-frame dump, or nothing at all.
"""
from __future__ import annotations

from typing import Literal, Protocol

import numpy as np

from launch_lights.engine.frame import OFF, Cell, Frame, RGB
from launch_lights.engine.plan import (
    NoOpPlan,
    PaletteDiffPlan,
    RenderPlan,
    RGBDiffPlan,
    RGBFullFramePlan,
)


class PaletteQuantizer(Protocol):
    def nearest(self, rgb: RGB) -> int: ...


# Threshold above which we emit a single 0Fh full-frame dump instead of
# per-cell 0Bh updates. One full-frame SysEx is ~199 inner bytes; one 0Bh
# message with N cells is ~5+1+N*4 bytes. Crossover for "is full-frame
# cheaper than diff" sits well below 64 cells. Live video changes most cells
# most of the time, so the prefer_full_frame fast path is the default for
# video sources.
DIFF_TO_FULL_FRAME_THRESHOLD = 16

_ALL_CELLS: tuple[Cell, ...] = tuple((r, c) for r in range(8) for c in range(8))


class Renderer:
    def __init__(
        self,
        *,
        mode: Literal["rgb", "palette"] = "rgb",
        palette: PaletteQuantizer | None = None,
        prefer_full_frame: bool = True,
    ) -> None:
        if mode == "palette" and palette is None:
            raise ValueError("palette mode requires a PaletteQuantizer")
        self.mode = mode
        self.palette = palette
        self.prefer_full_frame = prefer_full_frame
        self.last_rgb: dict[Cell, RGB] = {pos: OFF for pos in _ALL_CELLS}
        self.last_palette: dict[Cell, int] = {pos: 0 for pos in _ALL_CELLS}

    # --- public ------------------------------------------------------------

    def plan(self, frame: Frame) -> RenderPlan:
        if self.mode == "rgb":
            return self._plan_rgb(frame)
        return self._plan_palette(frame)

    def commit(self, plan: RenderPlan) -> None:
        """Update ``last_*`` to reflect a plan that was just transmitted."""
        if isinstance(plan, NoOpPlan):
            return
        if isinstance(plan, RGBDiffPlan):
            self.last_rgb.update(plan.cells)
            return
        if isinstance(plan, RGBFullFramePlan):
            g = plan.grid
            for r in range(8):
                for c in range(8):
                    self.last_rgb[(r, c)] = RGB(int(g[r, c, 0]), int(g[r, c, 1]), int(g[r, c, 2]))
            return
        if isinstance(plan, PaletteDiffPlan):
            self.last_palette.update(plan.cells)
            return
        raise TypeError(f"unknown plan type: {type(plan).__name__}")

    def blackout(self) -> None:
        """Reset last-state to all-off (call after device.blackout())."""
        for pos in _ALL_CELLS:
            self.last_rgb[pos] = OFF
            self.last_palette[pos] = 0

    # --- internals ---------------------------------------------------------

    def _plan_rgb(self, frame: Frame) -> RenderPlan:
        if self.prefer_full_frame:
            grid = self._assemble_grid(frame)
            # Skip if every cell already matches.
            if self._grid_matches_last(grid):
                return NoOpPlan()
            return RGBFullFramePlan(grid=grid)

        changed: dict[Cell, RGB] = {}
        for pos in _ALL_CELLS:
            wanted = frame.cells.get(pos, OFF)
            if self.last_rgb[pos] != wanted:
                changed[pos] = wanted
        if not changed:
            return NoOpPlan()
        if len(changed) >= DIFF_TO_FULL_FRAME_THRESHOLD:
            return RGBFullFramePlan(grid=self._assemble_grid(frame))
        return RGBDiffPlan(cells=changed)

    def _plan_palette(self, frame: Frame) -> RenderPlan:
        assert self.palette is not None
        changed: dict[Cell, int] = {}
        for pos in _ALL_CELLS:
            wanted = self.palette.nearest(frame.cells.get(pos, OFF))
            if self.last_palette[pos] != wanted:
                changed[pos] = wanted
        if not changed:
            return NoOpPlan()
        return PaletteDiffPlan(cells=changed)

    def _assemble_grid(self, frame: Frame) -> np.ndarray:
        """Build a contiguous (8,8,3) uint8 grid for a full-frame dump.

        Missing cells fall back to the renderer's current last_rgb so that
        partial frames don't blank the display."""
        out = np.zeros((8, 8, 3), dtype=np.uint8)
        for r in range(8):
            for c in range(8):
                rgb = frame.cells.get((r, c))
                if rgb is None:
                    rgb = self.last_rgb[(r, c)]
                out[r, c, 0] = rgb.r
                out[r, c, 1] = rgb.g
                out[r, c, 2] = rgb.b
        return out

    def _grid_matches_last(self, grid: np.ndarray) -> bool:
        for r in range(8):
            for c in range(8):
                last = self.last_rgb[(r, c)]
                if grid[r, c, 0] != last.r or grid[r, c, 1] != last.g or grid[r, c, 2] != last.b:
                    return False
        return True
