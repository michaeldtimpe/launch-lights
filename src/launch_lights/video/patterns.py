"""Built-in test patterns producing Frames directly (no video pipeline).

These are used by the ``test`` subcommand and by the ``--source test``
mode in ``run``. They're also useful for manually verifying orientation,
color fidelity, and dithering on the hardware.
"""
from __future__ import annotations

import math
from typing import Callable, Iterable

from launch_lights.engine.frame import OFF, Cell, Frame, RGB


PATTERN_NAMES = ("bars", "gradient", "checker", "flood", "sweep", "orientation")


def _hex_to_rgb6(hex_color: str) -> RGB:
    """Parse '#rrggbb' or 'rrggbb' (8-bit) into a 6-bit RGB (>>2)."""
    s = hex_color.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"expected #rrggbb, got {hex_color!r}")
    r8, g8, b8 = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return RGB(r8 >> 2, g8 >> 2, b8 >> 2)


def bars() -> Frame:
    """8 vertical color bars: red, orange, yellow, green, cyan, blue, magenta, white."""
    palette = (
        RGB(63, 0, 0),     # red
        RGB(63, 24, 0),    # orange
        RGB(63, 63, 0),    # yellow
        RGB(0, 63, 0),     # green
        RGB(0, 63, 63),    # cyan
        RGB(0, 0, 63),     # blue
        RGB(50, 0, 63),    # magenta
        RGB(63, 63, 63),   # white
    )
    return Frame(cells={(r, c): palette[c] for r in range(8) for c in range(8)})


def gradient() -> Frame:
    """Horizontal black -> white gradient. Tests banding + dithering."""
    cells: dict[Cell, RGB] = {}
    for c in range(8):
        v = round(c * 63 / 7)
        for r in range(8):
            cells[(r, c)] = RGB(v, v, v)
    return Frame(cells=cells)


def checker(a: RGB = RGB(40, 0, 0), b: RGB = RGB(0, 0, 40)) -> Frame:
    cells: dict[Cell, RGB] = {}
    for r in range(8):
        for c in range(8):
            cells[(r, c)] = a if (r + c) % 2 == 0 else b
    return Frame(cells=cells)


def flood(color: str = "#ff0000") -> Frame:
    return Frame.flood(_hex_to_rgb6(color))


def orientation() -> Frame:
    """Asymmetric corner pattern. Catches any flip/rotation/transpose error.

      (0,0) RED        (0,7) GREEN
      (7,0) BLUE       (7,7) WHITE
    """
    cells: dict[Cell, RGB] = {(r, c): OFF for r in range(8) for c in range(8)}
    cells[(0, 0)] = RGB(63, 0, 0)
    cells[(0, 7)] = RGB(0, 63, 0)
    cells[(7, 0)] = RGB(0, 0, 63)
    cells[(7, 7)] = RGB(63, 63, 63)
    # Hint arrows along the top edge (R..G) and left edge (R..B) so the
    # asymmetry is obvious from across the room.
    for c in range(1, 7):
        # Top row: fade red -> green
        cells[(0, c)] = RGB(round((7 - c) * 30 / 6), round(c * 30 / 6), 0)
    for r in range(1, 7):
        # Left column: fade red -> blue
        cells[(r, 0)] = RGB(round((7 - r) * 30 / 6), 0, round(r * 30 / 6))
    return Frame(cells=cells)


def sweep(t: float) -> Frame:
    """Animated full-grid hue sweep. ``t`` is elapsed seconds."""
    cells: dict[Cell, RGB] = {}
    for r in range(8):
        for c in range(8):
            # Distance from top-left, scaled to [0, 1]
            phase = ((r + c) / 14.0 + t * 0.3) % 1.0
            cells[(r, c)] = _hsv_to_rgb6(phase, 1.0, 1.0)
    return Frame(cells=cells)


def _hsv_to_rgb6(h: float, s: float, v: float) -> RGB:
    """HSV in [0,1] -> RGB in 0..63."""
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i %= 6
    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q
    return RGB(round(r * 63), round(g * 63), round(b * 63))


# Map from --pattern flag to a callable (t) -> Frame for the test subcommand.
StaticPattern = Callable[[], Frame]
AnimatedPattern = Callable[[float], Frame]


def build_pattern(name: str, *, flood_color: str = "#ff0000") -> AnimatedPattern:
    """Return a (elapsed_seconds) -> Frame callable. Static patterns ignore t."""
    if name == "bars":
        return lambda t: bars()
    if name == "gradient":
        return lambda t: gradient()
    if name == "checker":
        return lambda t: checker()
    if name == "flood":
        return lambda t: flood(flood_color)
    if name == "orientation":
        return lambda t: orientation()
    if name == "sweep":
        return lambda t: sweep(t)
    raise ValueError(f"unknown pattern: {name!r} (known: {PATTERN_NAMES})")
