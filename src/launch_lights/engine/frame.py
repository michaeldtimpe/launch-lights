"""Top-origin 8x8 grid model.

Coordinate convention used everywhere in this codebase EXCEPT inside the
0Fh full-frame SysEx serializer (which the Launchpad Pro defines bottom-up):

    Cell = (row, col)
    row 0 = top, col 0 = left, both 0..7

The vertical flip required by the 0Fh protocol happens in exactly one place:
``device.launchpad_pro.LaunchpadProOut._execute_full_frame``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

Cell = tuple[int, int]


@dataclass(frozen=True)
class RGB:
    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        for ch in (self.r, self.g, self.b):
            if not 0 <= ch <= 63:
                raise ValueError(f"RGB channel out of range 0..63: {self}")


OFF = RGB(0, 0, 0)


@dataclass(frozen=True)
class Frame:
    """Sparse 8x8 grid. Missing cells are "no opinion" — treated as OFF by the
    renderer's last-state diff."""

    cells: dict[Cell, RGB] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "Frame":
        return cls(cells={})

    @classmethod
    def flood(cls, rgb: RGB) -> "Frame":
        return cls(cells={(r, c): rgb for r in range(8) for c in range(8)})

    @classmethod
    def from_iter(cls, items: Iterable[tuple[Cell, RGB]]) -> "Frame":
        return cls(cells=dict(items))

    def with_cell(self, pos: Cell, rgb: RGB) -> "Frame":
        new = dict(self.cells)
        new[pos] = rgb
        return Frame(cells=new)

    def get(self, pos: Cell) -> RGB:
        return self.cells.get(pos, OFF)
