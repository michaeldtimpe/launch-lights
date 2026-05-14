"""Algebraic data type returned by the Renderer and consumed by the device.

The split keeps planning (pure compute, easily unit-tested) separate from
transport (MIDI I/O). Add new plan variants when adding new device commands.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from launch_lights.engine.frame import Cell, RGB


@dataclass(frozen=True)
class NoOpPlan:
    """No-op: device state already matches desired frame."""


@dataclass(frozen=True)
class RGBDiffPlan:
    """Sparse update: emit one or more SysEx 0Bh messages (<=78 cells each)."""

    cells: dict[Cell, RGB]


@dataclass(frozen=True)
class RGBFullFramePlan:
    """Full 8x8 dump via SysEx 0Fh. ``grid`` is shape (8, 8, 3), uint8 in 0..63,
    RGB order, row 0 = top (top-origin). The device serializer flips rows."""

    grid: np.ndarray


@dataclass(frozen=True)
class PaletteDiffPlan:
    """Sparse palette-index update via SysEx 0Ah (<=97 cells per message)."""

    cells: dict[Cell, int]  # palette indices 0..127


RenderPlan = NoOpPlan | RGBDiffPlan | RGBFullFramePlan | PaletteDiffPlan
