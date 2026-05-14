"""Audio-reactive visualisations, palettes, effects, and a Show director.

Pipeline (per frame):
    primary_scene.paint(state) -> CellMap
    + max-blend each extra scene
    -> trail_decay        (feedback from previous output)
    -> palette_remap      (if active)
    -> hue_rotate         (global degrees, applied in HSV)
    -> invert / complementary (sticky toggles)
    -> mirror             (off | h | v | quad | radial — radial == kaleido)
    -> contrast           (sigmoid)
    -> brightness         (linear scalar)
    -> gamma              (final, before SysEx)
"""
from __future__ import annotations

import colorsys
import math
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from launch_lights.engine.frame import Cell, OFF, RGB


N_BANDS = 8


# ============================================================================
# MusicState
# ============================================================================

@dataclass
class MusicState:
    bands: np.ndarray
    held: np.ndarray
    rms: float
    bass: float
    treble: float
    is_beat: bool
    beat_count: int
    bpm: float
    beat_phase: float          # 0..1 progress to next expected beat
    onset_strength: float      # 0..1 log-scaled bass surge
    spectral_flux: float       # sum of positive band increases this frame
    beat_confidence: float     # 0..1, low = tempo locked, high std intervals
    frames_since_beat: int
    elapsed: float


CellMap = dict[Cell, RGB]


# ============================================================================
# Colour utilities
# ============================================================================

def _clamp6(v: float) -> int:
    return max(0, min(63, int(round(v))))


def _hsv(h: float, s: float, v: float) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return RGB(_clamp6(r * 63), _clamp6(g * 63), _clamp6(b * 63))


def _shift_hue(rgb: RGB, delta_turns: float) -> RGB:
    if rgb.r == 0 and rgb.g == 0 and rgb.b == 0:
        return rgb
    h, s, v = colorsys.rgb_to_hsv(rgb.r / 63.0, rgb.g / 63.0, rgb.b / 63.0)
    return _hsv(h + delta_turns, s, v)


def _scale(rgb: RGB, factor: float) -> RGB:
    return RGB(_clamp6(rgb.r * factor), _clamp6(rgb.g * factor), _clamp6(rgb.b * factor))


# ============================================================================
# Palettes — first-class, pre-rendered to 256-entry LUTs
# ============================================================================

class Palette:
    """A 256-entry RGB LUT addressable by t in [0, 1]."""

    def __init__(self, stops: list[RGB]) -> None:
        if len(stops) < 1:
            raise ValueError("palette needs at least one stop")
        if len(stops) == 1:
            self._lut = np.tile(np.array([[stops[0].r, stops[0].g, stops[0].b]], dtype=np.uint8), (256, 1))
            return
        n = len(stops)
        lut = np.zeros((256, 3), dtype=np.uint8)
        for i in range(256):
            t = i / 255.0
            seg = t * (n - 1)
            idx = int(seg)
            frac = seg - idx
            if idx >= n - 1:
                a = b = stops[-1]
                frac = 0.0
            else:
                a, b = stops[idx], stops[idx + 1]
            lut[i, 0] = round(a.r + (b.r - a.r) * frac)
            lut[i, 1] = round(a.g + (b.g - a.g) * frac)
            lut[i, 2] = round(a.b + (b.b - a.b) * frac)
        self._lut = lut

    def sample(self, t: float) -> RGB:
        i = max(0, min(255, int(round(t * 255))))
        r, g, b = self._lut[i]
        return RGB(int(r), int(g), int(b))


PALETTES: dict[str, Palette] = {
    "neon":      Palette([RGB(63, 0, 63), RGB(0, 63, 63), RGB(63, 63, 0)]),
    "fire":      Palette([RGB(0, 0, 0), RGB(40, 0, 0), RGB(63, 30, 0), RGB(63, 60, 0), RGB(63, 63, 50)]),
    "inferno":   Palette([RGB(0, 0, 5), RGB(20, 0, 30), RGB(60, 0, 30), RGB(63, 30, 0), RGB(63, 63, 0), RGB(63, 63, 50)]),
    "ocean":     Palette([RGB(0, 0, 20), RGB(0, 10, 63), RGB(0, 40, 63), RGB(20, 63, 63)]),
    "forest":    Palette([RGB(0, 10, 0), RGB(0, 40, 0), RGB(30, 63, 0), RGB(63, 63, 20)]),
    "sunset":    Palette([RGB(20, 0, 30), RGB(63, 0, 30), RGB(63, 30, 0), RGB(63, 55, 20)]),
    "ice":       Palette([RGB(0, 0, 20), RGB(20, 32, 63), RGB(40, 50, 63), RGB(63, 63, 63)]),
    "candy":     Palette([RGB(63, 0, 32), RGB(32, 0, 63), RGB(0, 30, 63), RGB(0, 63, 60)]),
    "lava":      Palette([RGB(10, 0, 0), RGB(63, 10, 0), RGB(63, 40, 0), RGB(63, 63, 0)]),
    "spring":    Palette([RGB(0, 30, 0), RGB(0, 63, 30), RGB(30, 63, 0), RGB(63, 63, 30)]),
    "mono_red":  Palette([RGB(0, 0, 0), RGB(20, 0, 0), RGB(40, 0, 0), RGB(63, 0, 0)]),
    "mono_blu":  Palette([RGB(0, 0, 0), RGB(0, 0, 30), RGB(0, 30, 63), RGB(40, 60, 63)]),
    "violet":    Palette([RGB(20, 0, 30), RGB(40, 0, 63), RGB(63, 0, 50), RGB(63, 30, 63)]),
    "synthwave": Palette([RGB(20, 0, 40), RGB(63, 0, 50), RGB(0, 30, 63), RGB(0, 50, 50)]),
    "cyberpunk": Palette([RGB(0, 0, 0), RGB(63, 0, 60), RGB(0, 60, 63), RGB(63, 60, 0)]),
    "toxic":     Palette([RGB(0, 0, 5), RGB(0, 50, 0), RGB(40, 63, 0), RGB(63, 63, 30)]),
    "mono":      Palette([RGB(0, 0, 0), RGB(20, 20, 20), RGB(40, 40, 40), RGB(63, 63, 63)]),
}


# ============================================================================
# 5x7 bitmap font (5 cols stored in low 5 bits, MSB = leftmost column)
# Limited charset for an 8x8 LED grid.
# ============================================================================

FONT_5x7: dict[str, tuple[int, ...]] = {
    " ": (0, 0, 0, 0, 0, 0, 0),
    "A": (0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "B": (0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110),
    "C": (0b01111, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b01111),
    "D": (0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110),
    "E": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111),
    "F": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000),
    "G": (0b01111, 0b10000, 0b10000, 0b10011, 0b10001, 0b10001, 0b01111),
    "H": (0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "I": (0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "J": (0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100),
    "K": (0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001),
    "L": (0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111),
    "M": (0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001),
    "N": (0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001),
    "O": (0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "P": (0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000),
    "Q": (0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101),
    "R": (0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001),
    "S": (0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110),
    "T": (0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100),
    "U": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "V": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100),
    "W": (0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b10101, 0b01010),
    "X": (0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001),
    "Y": (0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100),
    "Z": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111),
    "0": (0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110),
    "1": (0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "2": (0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111),
    "3": (0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110),
    "4": (0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010),
    "5": (0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110),
    "6": (0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110),
    "7": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000),
    "8": (0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110),
    "9": (0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100),
    "!": (0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00000, 0b00100),
    "?": (0b01110, 0b10001, 0b00001, 0b00110, 0b00100, 0b00000, 0b00100),
    ".": (0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00100),
    ",": (0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00100, 0b01000),
    "-": (0b00000, 0b00000, 0b00000, 0b01110, 0b00000, 0b00000, 0b00000),
    ":": (0b00000, 0b00100, 0b00100, 0b00000, 0b00100, 0b00100, 0b00000),
    "/": (0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000),
    "'": (0b00100, 0b00100, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000),
    "+": (0b00000, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0b00000),
    "*": (0b00000, 0b10101, 0b01110, 0b11111, 0b01110, 0b10101, 0b00000),
    "=": (0b00000, 0b00000, 0b11111, 0b00000, 0b11111, 0b00000, 0b00000),
}

# 3x5 micro font
FONT_3x5: dict[str, tuple[int, ...]] = {
    " ": (0, 0, 0, 0, 0),
    "A": (0b010, 0b101, 0b111, 0b101, 0b101),
    "B": (0b110, 0b101, 0b110, 0b101, 0b110),
    "C": (0b011, 0b100, 0b100, 0b100, 0b011),
    "D": (0b110, 0b101, 0b101, 0b101, 0b110),
    "E": (0b111, 0b100, 0b110, 0b100, 0b111),
    "F": (0b111, 0b100, 0b110, 0b100, 0b100),
    "G": (0b011, 0b100, 0b101, 0b101, 0b011),
    "H": (0b101, 0b101, 0b111, 0b101, 0b101),
    "I": (0b111, 0b010, 0b010, 0b010, 0b111),
    "J": (0b001, 0b001, 0b001, 0b101, 0b010),
    "K": (0b101, 0b110, 0b100, 0b110, 0b101),
    "L": (0b100, 0b100, 0b100, 0b100, 0b111),
    "M": (0b101, 0b111, 0b111, 0b101, 0b101),
    "N": (0b101, 0b111, 0b111, 0b111, 0b101),
    "O": (0b010, 0b101, 0b101, 0b101, 0b010),
    "P": (0b110, 0b101, 0b110, 0b100, 0b100),
    "Q": (0b010, 0b101, 0b101, 0b111, 0b011),
    "R": (0b110, 0b101, 0b110, 0b101, 0b101),
    "S": (0b011, 0b100, 0b010, 0b001, 0b110),
    "T": (0b111, 0b010, 0b010, 0b010, 0b010),
    "U": (0b101, 0b101, 0b101, 0b101, 0b111),
    "V": (0b101, 0b101, 0b101, 0b101, 0b010),
    "W": (0b101, 0b101, 0b111, 0b111, 0b101),
    "X": (0b101, 0b101, 0b010, 0b101, 0b101),
    "Y": (0b101, 0b101, 0b010, 0b010, 0b010),
    "Z": (0b111, 0b001, 0b010, 0b100, 0b111),
    "0": (0b010, 0b101, 0b101, 0b101, 0b010),
    "1": (0b010, 0b110, 0b010, 0b010, 0b111),
    "2": (0b110, 0b001, 0b010, 0b100, 0b111),
    "3": (0b110, 0b001, 0b010, 0b001, 0b110),
    "4": (0b101, 0b101, 0b111, 0b001, 0b001),
    "5": (0b111, 0b100, 0b110, 0b001, 0b110),
    "6": (0b011, 0b100, 0b111, 0b101, 0b111),
    "7": (0b111, 0b001, 0b010, 0b010, 0b010),
    "8": (0b111, 0b101, 0b111, 0b101, 0b111),
    "9": (0b111, 0b101, 0b111, 0b001, 0b110),
    "!": (0b010, 0b010, 0b010, 0b000, 0b010),
    "?": (0b110, 0b001, 0b010, 0b000, 0b010),
    ".": (0, 0, 0, 0, 0b010),
    ",": (0, 0, 0, 0b010, 0b100),
    "-": (0, 0, 0b111, 0, 0),
    ":": (0, 0b010, 0, 0b010, 0),
    "/": (0b001, 0b001, 0b010, 0b100, 0b100),
    "'": (0b010, 0b010, 0, 0, 0),
}

FONTS = {"5x7": (FONT_5x7, 5, 7), "3x5": (FONT_3x5, 3, 5)}


# ============================================================================
# Visualisations
# ============================================================================

class Viz:
    name: str = "base"

    def paint(self, state: MusicState) -> CellMap:
        raise NotImplementedError


class SpectrumBars(Viz):
    name = "spectrum"
    _COLS = (
        RGB(63, 0, 0), RGB(63, 0, 0), RGB(63, 32, 0), RGB(63, 48, 0),
        RGB(63, 63, 0), RGB(32, 63, 0), RGB(0, 63, 0), RGB(0, 63, 0),
    )

    def paint(self, s):
        cells = {}
        for c in range(8):
            h = int(round(float(s.held[c]) * 8))
            for r in range(8):
                if (7 - r) < h:
                    cells[(r, c)] = self._COLS[r]
        return cells


class InvertedSpectrum(Viz):
    name = "inverted"

    def paint(self, s):
        cells = {}
        for c in range(8):
            h = int(round(float(s.held[c]) * 8))
            for r in range(h):
                cells[(r, c)] = _hsv(c / 8.0, 1.0, 1.0)
        return cells


class MirrorSpectrum(Viz):
    name = "mirror"

    def paint(self, s):
        cells = {}
        for c in range(8):
            half = int(round(float(s.held[c]) * 4))
            colour = _hsv(c / 8.0, 1.0, 1.0)
            for d in range(half):
                cells[(3 - d, c)] = colour
                cells[(4 + d, c)] = colour
        return cells


class VURing(Viz):
    name = "ring"

    def paint(self, s):
        radius = int(round(s.rms * 4))
        if radius == 0:
            return {}
        col = _hsv((s.elapsed * 0.1) % 1.0, 1.0, 1.0)
        cells = {}
        for r in range(8):
            for c in range(8):
                d = max(abs(r - 3.5), abs(c - 3.5))
                if abs(d - radius + 0.5) < 0.7:
                    cells[(r, c)] = col
        return cells


class BassBlob(Viz):
    name = "blob"

    def paint(self, s):
        size = max(1, int(round(s.bass * 4)))
        col = _hsv(0.0, 1.0, min(1.0, 0.3 + s.bass))
        cells = {}
        for r in range(4 - size, 4 + size):
            for c in range(4 - size, 4 + size):
                if 0 <= r < 8 and 0 <= c < 8:
                    cells[(r, c)] = col
        return cells


class PixelRain(Viz):
    name = "rain"

    def __init__(self):
        self._drops: list[tuple[int, int, RGB, float]] = []
        self._last_spawn = 0.0

    def paint(self, s):
        rate = 4 + 16 * s.bass
        if s.elapsed - self._last_spawn > 1.0 / rate:
            self._last_spawn = s.elapsed
            self._drops.append((0, random.randint(0, 7), _hsv(random.random(), 1.0, 1.0), s.elapsed))
        cells: CellMap = {}
        new = []
        for r, c, col, born in self._drops:
            depth = int((s.elapsed - born) * 6)
            if depth >= 8:
                continue
            new.append((r, c, col, born))
            cells[(depth, c)] = col
        self._drops = new[-32:]
        return cells


class Sparkle(Viz):
    name = "sparkle"

    def __init__(self):
        self._lit: dict[Cell, tuple[float, RGB]] = {}

    def paint(self, s):
        if s.is_beat:
            for _ in range(random.randint(4, 10)):
                cell = (random.randint(0, 7), random.randint(0, 7))
                self._lit[cell] = (s.elapsed, _hsv(random.random(), 1.0, 1.0))
        cells: CellMap = {}
        for cell, (born, col) in list(self._lit.items()):
            age = s.elapsed - born
            if age > 0.8:
                del self._lit[cell]
                continue
            cells[cell] = _scale(col, max(0.0, 1.0 - age / 0.8))
        return cells


class NegativeSparkle(Viz):
    name = "neg_sparkle"

    def __init__(self):
        self._dark: dict[Cell, float] = {}
        self._bg_hue = 0.0

    def paint(self, s):
        if s.is_beat:
            self._bg_hue = (self._bg_hue + 0.13) % 1.0
            for _ in range(random.randint(6, 14)):
                cell = (random.randint(0, 7), random.randint(0, 7))
                self._dark[cell] = s.elapsed
        bg = _hsv(self._bg_hue, 0.6, 0.7)
        cells: CellMap = {(r, c): bg for r in range(8) for c in range(8)}
        for cell, born in list(self._dark.items()):
            if s.elapsed - born > 0.5:
                del self._dark[cell]
                continue
            cells[cell] = OFF
        return cells


class ColorFlood(Viz):
    name = "flood"

    def __init__(self):
        self._hue = random.random()

    def paint(self, s):
        if s.is_beat:
            self._hue = (self._hue + 0.17) % 1.0
        col = _hsv(self._hue, 1.0, 0.3 + s.rms * 0.7)
        return {(r, c): col for r in range(8) for c in range(8)}


class DiagonalSweep(Viz):
    name = "diag"

    def paint(self, s):
        speed = 2.0 + (s.bpm / 60.0 if s.bpm > 0 else 2.0)
        offset = (s.elapsed * speed) % 16
        cells: CellMap = {}
        for r in range(8):
            for c in range(8):
                d = (r + c - offset) % 16
                if d < 3:
                    cells[(r, c)] = _hsv((s.elapsed * 0.05) % 1.0, 1.0, 1.0 - d / 3.0)
        return cells


class ConcentricRings(Viz):
    name = "rings"

    def __init__(self):
        self._rings: list[float] = []

    def paint(self, s):
        if s.is_beat:
            self._rings.append(s.elapsed)
        cells: CellMap = {}
        for born in list(self._rings):
            age = s.elapsed - born
            radius = age * 6.0
            if radius > 6:
                self._rings.remove(born)
                continue
            col = _hsv((born * 0.4) % 1.0, 1.0, max(0.0, 1.0 - radius / 6.0))
            for r in range(8):
                for c in range(8):
                    d = max(abs(r - 3.5), abs(c - 3.5))
                    if abs(d - radius) < 0.7:
                        cells[(r, c)] = col
        return cells


class ColumnSweep(Viz):
    name = "col_sweep"

    def paint(self, s):
        col = int(s.elapsed * 4) % 8
        h = max(1, int(round(s.bass * 8)))
        colour = _hsv((s.elapsed * 0.2) % 1.0, 1.0, 1.0)
        cells: CellMap = {}
        for r in range(8 - h, 8):
            cells[(r, col)] = colour
        return cells


class RowSweep(Viz):
    name = "row_sweep"

    def paint(self, s):
        row = int(s.elapsed * 3) % 8
        colour = _hsv((s.elapsed * 0.15) % 1.0, 1.0, 0.5 + s.rms * 0.5)
        return {(row, c): colour for c in range(8)}


class CheckerFlip(Viz):
    name = "checker"

    def __init__(self):
        self._phase = 0

    def paint(self, s):
        if s.is_beat:
            self._phase ^= 1
        a = _hsv(s.bass, 1.0, 1.0)
        b = _hsv((s.bass + 0.5) % 1.0, 1.0, 1.0)
        return {(r, c): (a if ((r + c + self._phase) & 1) else b) for r in range(8) for c in range(8)}


class Plasma(Viz):
    name = "plasma"

    def paint(self, s):
        t = s.elapsed
        cells: CellMap = {}
        for r in range(8):
            for c in range(8):
                v = (
                    math.sin(c * 0.7 + t * 1.5)
                    + math.sin(r * 0.7 + t * 1.3)
                    + math.sin((r + c) * 0.5 + t * 0.7)
                ) / 3.0
                hue = (v + 1.0) / 2.0
                cells[(r, c)] = _hsv(hue, 1.0, 0.4 + s.rms * 0.6)
        return cells


class BorderRunner(Viz):
    name = "border"

    _PATH = (
        [(0, c) for c in range(8)]
        + [(r, 7) for r in range(1, 8)]
        + [(7, c) for c in range(6, -1, -1)]
        + [(r, 0) for r in range(6, 0, -1)]
    )

    def paint(self, s):
        speed = max(8.0, s.bpm / 60.0 * 14.0) if s.bpm > 0 else 14.0
        i = int(s.elapsed * speed) % len(self._PATH)
        cells: CellMap = {}
        for k in range(3):
            r, c = self._PATH[(i - k) % len(self._PATH)]
            cells[(r, c)] = _hsv((s.elapsed * 0.1) % 1.0, 1.0, 1.0 - k * 0.3)
        return cells


class CrossPulse(Viz):
    name = "cross"

    def paint(self, s):
        col = _hsv((s.elapsed * 0.2) % 1.0, 1.0, 0.4 + s.rms * 0.6)
        cells: CellMap = {}
        for i in range(8):
            cells[(i, i)] = col
            cells[(i, 7 - i)] = col
        return cells


class HeartPulse(Viz):
    name = "heart"

    _HEART = {
        (1, 1), (1, 2), (1, 5), (1, 6),
        (2, 0), (2, 1), (2, 2), (2, 3), (2, 4), (2, 5), (2, 6), (2, 7),
        (3, 0), (3, 1), (3, 2), (3, 3), (3, 4), (3, 5), (3, 6), (3, 7),
        (4, 1), (4, 2), (4, 3), (4, 4), (4, 5), (4, 6),
        (5, 2), (5, 3), (5, 4), (5, 5),
        (6, 3), (6, 4),
    }

    def paint(self, s):
        col = _hsv(0.0, 1.0, 0.3 + s.bass * 0.7)
        return {cell: col for cell in self._HEART}


class EdgeBands(Viz):
    name = "edge"

    def paint(self, s):
        cells: CellMap = {}
        for c in range(8):
            v = float(s.held[c])
            if v > 0.1:
                cells[(0, c)] = _hsv(0.6, 1.0, v)
                cells[(7, c)] = _hsv(0.0, 1.0, v)
        return cells


class Quadrants(Viz):
    name = "quads"

    def paint(self, s):
        levels = [
            float(np.mean(s.held[0:2])),
            float(np.mean(s.held[2:4])),
            float(np.mean(s.held[4:6])),
            float(np.mean(s.held[6:8])),
        ]
        colours = [_hsv(0.0, 1.0, 1.0), _hsv(0.15, 1.0, 1.0), _hsv(0.55, 1.0, 1.0), _hsv(0.75, 1.0, 1.0)]
        cells: CellMap = {}
        quads = [(0, 0), (0, 4), (4, 0), (4, 4)]
        for (qr, qc), lvl, col in zip(quads, levels, colours):
            n = int(round(lvl * 16))
            count = 0
            for r in range(qr, qr + 4):
                for c in range(qc, qc + 4):
                    if count < n:
                        cells[(r, c)] = col
                        count += 1
        return cells


class ScrollingText(Viz):
    """Scrolling banner of user-provided text.

    Config is mutated via `set_config(...)`; the viz instance is shared by the
    Show so config edits land on the next frame without reset.
    """
    name = "text"

    def __init__(self) -> None:
        self._text = "LAUNCH LIGHTS"
        self._font_key = "5x7"
        self._speed = 8.0          # pixels per second
        self._direction = "left"   # "left" | "right"
        self._rebuild()

    def set_config(self, *, text: Optional[str] = None, font: Optional[str] = None,
                   speed: Optional[float] = None, direction: Optional[str] = None) -> None:
        changed_text = False
        if text is not None and text != self._text:
            self._text = text
            changed_text = True
        if font is not None and font != self._font_key and font in FONTS:
            self._font_key = font
            changed_text = True
        if speed is not None:
            self._speed = max(0.0, min(60.0, float(speed)))
        if direction is not None and direction in ("left", "right"):
            self._direction = direction
        if changed_text:
            self._rebuild()

    def _rebuild(self) -> None:
        font, w, h = FONTS[self._font_key]
        glyphs = []
        for ch in self._text.upper():
            rows = font.get(ch, font[" "])
            glyphs.append((rows, w, h))
        # Compose a column buffer: ((rows for col 0 in MSB order), (rows for col 1), ...)
        # Easier: build a 2D mask H rows × total_cols where each cell is 1/0.
        if not glyphs:
            self._mask = np.zeros((h, 8), dtype=np.uint8)
            return
        cols_per_glyph = w + 1  # 1-col gap between glyphs
        total_cols = sum(cols_per_glyph for _ in glyphs) + 8  # trailing pad so it scrolls out
        mask = np.zeros((h, total_cols), dtype=np.uint8)
        x = 0
        for rows, gw, gh in glyphs:
            for ri, row_bits in enumerate(rows):
                for bit in range(gw):
                    if row_bits & (1 << (gw - 1 - bit)):
                        mask[ri, x + bit] = 1
            x += cols_per_glyph
        self._mask = mask
        self._mask_w = total_cols
        self._mask_h = h

    def paint(self, s):
        font, w, h = FONTS[self._font_key]
        # Center vertically: top padding for 7-row font is 0..1 row; 5-row is 1..2.
        top = max(0, (8 - h) // 2)
        if not hasattr(self, "_mask") or self._mask.size == 0:
            return {}
        total = self._mask_w
        offset_f = (s.elapsed * self._speed) % total
        offset = int(offset_f)
        cells: CellMap = {}
        # Visible window is 8 columns wide
        # If direction == "left", text moves leftward -> window position increases over time.
        # If direction == "right", invert.
        if self._direction == "right":
            offset = (total - offset) % total
        # Use the active palette via the Show (handled in the effect stack),
        # but ScrollingText itself emits a single colour using a fixed warm
        # white; downstream palette_remap / hue_rotate / mirror still apply.
        col = RGB(60, 50, 30)
        for vc in range(8):
            src_c = (offset + vc) % total
            for r in range(h):
                if self._mask[r, src_c]:
                    cells[(top + r, vc)] = col
        return cells


class BlackoutScene(Viz):
    """Returns an empty grid — used when the Clear button is held."""
    name = "blackout"

    def paint(self, s):
        return {}


# Hand-authored 8x8 bitmaps for the shape picker. Each entry is the set of
# (row, col) tuples that are lit.
def _bmp(rows: list[str]) -> frozenset:
    out = set()
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            if ch == "#":
                out.add((r, c))
    return frozenset(out)


def _bmp_at(rows: list[str], offset_r: int = 0, offset_c: int = 0) -> frozenset:
    out = set()
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            if ch == "#":
                out.add((r + offset_r, c + offset_c))
    return frozenset(out)


SHAPES: dict[str, frozenset] = {
    "diamond": _bmp([
        "...##...",
        "..####..",
        ".######.",
        "########",
        "########",
        ".######.",
        "..####..",
        "...##...",
    ]),
    "star": _bmp([
        "...##...",
        "...##...",
        "..####..",
        "########",
        "########",
        "..####..",
        "...##...",
        "...##...",
    ]),
    "plus": _bmp([
        "...##...",
        "...##...",
        "...##...",
        "########",
        "########",
        "...##...",
        "...##...",
        "...##...",
    ]),
    "x": _bmp([
        "#......#",
        ".#....#.",
        "..#..#..",
        "...##...",
        "...##...",
        "..#..#..",
        ".#....#.",
        "#......#",
    ]),
    "arrow_up": _bmp([
        "...##...",
        "..####..",
        ".######.",
        "########",
        "...##...",
        "...##...",
        "...##...",
        "...##...",
    ]),
    "arrow_down": _bmp([
        "...##...",
        "...##...",
        "...##...",
        "...##...",
        "########",
        ".######.",
        "..####..",
        "...##...",
    ]),
    "arrow_left": _bmp([
        "...#....",
        "..##....",
        ".###....",
        "########",
        "########",
        ".###....",
        "..##....",
        "...#....",
    ]),
    "arrow_right": _bmp([
        "....#...",
        "....##..",
        "....###.",
        "########",
        "########",
        "....###.",
        "....##..",
        "....#...",
    ]),
    "circle": _bmp([
        "..####..",
        ".#....#.",
        "#......#",
        "#......#",
        "#......#",
        "#......#",
        ".#....#.",
        "..####..",
    ]),
    "square": _bmp([
        "########",
        "#......#",
        "#......#",
        "#......#",
        "#......#",
        "#......#",
        "#......#",
        "########",
    ]),
    "triangle_up": _bmp([
        "...##...",
        "...##...",
        "..####..",
        "..####..",
        ".######.",
        ".######.",
        "########",
        "########",
    ]),
    "triangle_down": _bmp([
        "########",
        "########",
        ".######.",
        ".######.",
        "..####..",
        "..####..",
        "...##...",
        "...##...",
    ]),
    "lightning": _bmp([
        "...###..",
        "..###...",
        "..##....",
        ".######.",
        "....####",
        "....###.",
        "...###..",
        "..##....",
    ]),
    "check": _bmp([
        "........",
        "......##",
        ".....##.",
        "....##..",
        "...##...",
        "#.##....",
        "###.....",
        ".#......",
    ]),
    "ring": _bmp([
        "..####..",
        ".######.",
        "##....##",
        "##....##",
        "##....##",
        "##....##",
        ".######.",
        "..####..",
    ]),
}


EMOJIS: dict[str, frozenset] = {
    "heart": _bmp([
        ".##..##.",
        "########",
        "########",
        ".######.",
        "..####..",
        "...##...",
        "........",
        "........",
    ]),
    "smiley": _bmp([
        ".######.",
        "#......#",
        "#.#..#.#",
        "#.#..#.#",
        "#......#",
        "#.#..#.#",
        "#..##..#",
        ".######.",
    ]),
    "sad": _bmp([
        ".######.",
        "#......#",
        "#.#..#.#",
        "#.#..#.#",
        "#......#",
        "#..##..#",
        "#.#..#.#",
        ".######.",
    ]),
    "skull": _bmp([
        ".######.",
        "########",
        "#.####.#",
        "#.####.#",
        "########",
        "..####..",
        ".#.##.#.",
        "#.#..#.#",
    ]),
    "music": _bmp([
        ".....###",
        "....#..#",
        "...#...#",
        "...#...#",
        "...#...#",
        "...#....",
        "####....",
        "###.....",
    ]),
    "ghost": _bmp([
        "..####..",
        ".######.",
        "#.####.#",
        "#.####.#",
        "########",
        "########",
        "########",
        "#.##.##.",
    ]),
    "fire": _bmp([
        "...##...",
        "..####..",
        "..#####.",
        ".######.",
        ".#######",
        "########",
        "########",
        ".######.",
    ]),
    "sun": _bmp([
        "#...#...",
        ".#..#..#",
        "..####..",
        "########",
        "########",
        "..####..",
        "#..#..#.",
        "...#...#",
    ]),
    "mini_smiley": _bmp_at([
        ".###.",
        "#.#.#",
        "#...#",
        "#.#.#",
        ".###.",
    ], offset_r=2, offset_c=2),
    "mini_sad": _bmp_at([
        ".###.",
        "#.#.#",
        "#...#",
        ".#.#.",
        ".###.",
    ], offset_r=2, offset_c=2),
    "mini_skull": _bmp_at([
        ".###.",
        "#####",
        "#.#.#",
        ".###.",
        "#.#.#",
    ], offset_r=2, offset_c=2),
    "mini_heart": _bmp_at([
        "#.#.#",
        "#####",
        ".###.",
        "..#..",
        ".....",
    ], offset_r=2, offset_c=2),
    "mini_star": _bmp_at([
        "..#..",
        "..#..",
        "#####",
        ".###.",
        ".#.#.",
    ], offset_r=2, offset_c=2),
}


# Combined lookup — Shape viz indexes either set by name.
ALL_BITMAPS: dict[str, frozenset] = {**SHAPES, **EMOJIS}


class Shape(Viz):
    """Static shape display. Brightness pulses with bass."""
    name = "shape"

    def __init__(self) -> None:
        self._shape_name = "heart"
        self._hue = 0.0  # red default

    def set_shape(self, name: str) -> None:
        if name in ALL_BITMAPS:
            self._shape_name = name

    def paint(self, s):
        col = _hsv(self._hue, 1.0, 0.3 + s.bass * 0.7)
        return {cell: col for cell in ALL_BITMAPS[self._shape_name]}


# Master list — used by the auto-rotator and the panel. ScrollingText and
# BlackoutScene are pickable but not part of the rotation pool.
_ROTATING_VIZES: list[type[Viz]] = [
    SpectrumBars, InvertedSpectrum, MirrorSpectrum, VURing, BassBlob,
    PixelRain, Sparkle, NegativeSparkle, ColorFlood, DiagonalSweep,
    ConcentricRings, ColumnSweep, RowSweep, CheckerFlip, Plasma,
    BorderRunner, CrossPulse, HeartPulse, EdgeBands, Quadrants,
]
_EXTRA_VIZES: list[type[Viz]] = [ScrollingText, BlackoutScene, Shape]
ALL_VIZES: list[type[Viz]] = _ROTATING_VIZES + _EXTRA_VIZES


# ============================================================================
# Effects — post-processing on CellMap
# ============================================================================

Effect = Callable[[CellMap, MusicState], CellMap]


def fx_identity(cells: CellMap, s: MusicState) -> CellMap:
    return cells


def fx_brightness_pulse(cells: CellMap, s: MusicState) -> CellMap:
    factor = 0.4 + 0.6 * s.rms
    return {cell: _scale(rgb, factor) for cell, rgb in cells.items()}


def fx_negative(cells: CellMap, s: MusicState) -> CellMap:
    bg = _hsv((s.elapsed * 0.05) % 1.0, 0.5, 0.6)
    out: CellMap = {}
    for r in range(8):
        for c in range(8):
            cur = cells.get((r, c), OFF)
            out[(r, c)] = OFF if (cur.r or cur.g or cur.b) else bg
    return out


def fx_channel_red(cells, s):
    return {cell: RGB(rgb.r, 0, 0) for cell, rgb in cells.items()}


def fx_channel_green(cells, s):
    return {cell: RGB(0, rgb.g, 0) for cell, rgb in cells.items()}


def fx_channel_blue(cells, s):
    return {cell: RGB(0, 0, rgb.b) for cell, rgb in cells.items()}


def fx_palette_remap(palette: Palette) -> Effect:
    def apply(cells, s):
        out: CellMap = {}
        for cell, rgb in cells.items():
            if rgb.r == 0 and rgb.g == 0 and rgb.b == 0:
                out[cell] = OFF
                continue
            brightness = (rgb.r + rgb.g + rgb.b) / (63 * 3)
            out[cell] = palette.sample(brightness)
        return out
    return apply


GENERIC_EFFECTS: list[Effect] = [
    fx_brightness_pulse, fx_negative,
    fx_channel_red, fx_channel_green, fx_channel_blue,
]


# ============================================================================
# Pipeline transforms — applied in fixed order in Show.paint
# ============================================================================

def _apply_trail(cells: CellMap, prev: CellMap, decay: float) -> CellMap:
    if decay <= 0.0:
        return cells
    out: CellMap = dict(cells)
    for cell, prgb in prev.items():
        cur = out.get(cell, OFF)
        # Max-blend current with decayed previous.
        decayed = _scale(prgb, decay)
        out[cell] = RGB(max(cur.r, decayed.r), max(cur.g, decayed.g), max(cur.b, decayed.b))
    return out


def _apply_hue_rotate(cells: CellMap, deg: float) -> CellMap:
    if deg == 0:
        return cells
    turns = deg / 360.0
    return {cell: _shift_hue(rgb, turns) for cell, rgb in cells.items()}


def _apply_invert(cells: CellMap) -> CellMap:
    fg = RGB(63, 63, 63)
    out: CellMap = {}
    for r in range(8):
        for c in range(8):
            cur = cells.get((r, c), OFF)
            out[(r, c)] = OFF if (cur.r or cur.g or cur.b) else fg
    return out


def _apply_complementary(cells: CellMap) -> CellMap:
    return {cell: _shift_hue(rgb, 0.5) for cell, rgb in cells.items()}


def _apply_mirror(cells: CellMap, mode: str) -> CellMap:
    if mode == "off":
        return cells
    grid = np.zeros((8, 8, 3), dtype=np.uint8)
    for (r, c), rgb in cells.items():
        grid[r, c] = (rgb.r, rgb.g, rgb.b)
    if mode == "horizontal":
        grid = np.maximum(grid, grid[:, ::-1])
    elif mode == "vertical":
        grid = np.maximum(grid, grid[::-1, :])
    elif mode == "quad":
        grid = np.maximum(grid, grid[:, ::-1])
        grid = np.maximum(grid, grid[::-1, :])
    elif mode == "radial":
        # Kaleido: take top-left 4x4 quadrant and reflect into all four.
        q = grid[:4, :4].copy()
        grid[:4, :4] = q
        grid[:4, 4:] = q[:, ::-1]
        grid[4:, :4] = q[::-1, :]
        grid[4:, 4:] = q[::-1, ::-1]
    out: CellMap = {}
    for r in range(8):
        for c in range(8):
            v = grid[r, c]
            if v[0] or v[1] or v[2]:
                out[(r, c)] = RGB(int(v[0]), int(v[1]), int(v[2]))
    return out


def _apply_contrast(cells: CellMap, contrast: float) -> CellMap:
    if contrast == 1.0:
        return cells
    out: CellMap = {}
    for cell, rgb in cells.items():
        def s(v):
            t = v / 63.0
            t = (t - 0.5) * contrast + 0.5
            return _clamp6(max(0.0, min(1.0, t)) * 63)
        out[cell] = RGB(s(rgb.r), s(rgb.g), s(rgb.b))
    return out


def _apply_gamma_lut(cells: CellMap, lut: np.ndarray) -> CellMap:
    return {cell: RGB(int(lut[rgb.r]), int(lut[rgb.g]), int(lut[rgb.b])) for cell, rgb in cells.items()}


def _build_gamma_lut(gamma: float) -> np.ndarray:
    lut = np.zeros(64, dtype=np.uint8)
    for i in range(64):
        lut[i] = round(((i / 63.0) ** gamma) * 63)
    return lut


# ============================================================================
# Show — director
# ============================================================================

@dataclass
class _SceneConfig:
    primary: Viz
    secondary: Optional[Viz]
    blend: str
    effects: list[Effect]
    until: float


def blend_max(a: CellMap, b: CellMap) -> CellMap:
    out = dict(a)
    for cell, rgb in b.items():
        cur = out.get(cell, OFF)
        out[cell] = RGB(max(cur.r, rgb.r), max(cur.g, rgb.g), max(cur.b, rgb.b))
    return out


def blend_mask(a: CellMap, b: CellMap) -> CellMap:
    out: CellMap = {}
    for cell, rgb in a.items():
        bcell = b.get(cell, OFF)
        if bcell.r or bcell.g or bcell.b:
            out[cell] = rgb
    return out


class Show:
    """Auto-rotating director with locking, mutators, and a full effect stack."""

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)
        self._scene: Optional[_SceneConfig] = None
        self._locked_name: Optional[str] = None
        self._extra_names: list[str] = []
        self._viz_cache: dict[str, Viz] = {}

        # Effect stack state
        self._brightness: float = 1.0
        self._palette_name: Optional[str] = None     # None = no remap
        self._hue_deg: float = 0.0
        self._contrast: float = 1.0
        self._gamma: float = 2.2
        self._gamma_lut = _build_gamma_lut(self._gamma)
        self._trail_decay: float = 0.0
        self._mirror: str = "off"
        self._invert: bool = False
        self._complementary: bool = False
        self._blackout: bool = False
        self._beat_multiplier: float = 1.0

        self._prev_cells: CellMap = {}

    def paint(self, state: MusicState) -> CellMap:
        if self._blackout:
            self._prev_cells = {}
            return {}

        # Apply beat multiplier to the BPM seen by viz code (without mutating the
        # detected value globally).
        if self._beat_multiplier != 1.0:
            state = MusicState(
                bands=state.bands, held=state.held, rms=state.rms, bass=state.bass,
                treble=state.treble, is_beat=state.is_beat, beat_count=state.beat_count,
                bpm=state.bpm * self._beat_multiplier,
                beat_phase=state.beat_phase, onset_strength=state.onset_strength,
                spectral_flux=state.spectral_flux, beat_confidence=state.beat_confidence,
                frames_since_beat=state.frames_since_beat, elapsed=state.elapsed,
            )

        # Pick primary
        if self._locked_name is not None:
            cells = self._viz_for(self._locked_name).paint(state)
        else:
            if self._scene is None or state.elapsed >= self._scene.until:
                self._rotate(state)
            s = self._scene
            cells = s.primary.paint(state)
            if s.secondary is not None:
                other = s.secondary.paint(state)
                cells = blend_max(cells, other) if s.blend == "max" else blend_mask(cells, other)
            for fx in s.effects:
                cells = fx(cells, state)

        # Mutator extras (blended on top)
        for name in self._extra_names:
            cells = blend_max(cells, self._viz_for(name).paint(state))

        # Effect pipeline
        cells = _apply_trail(cells, self._prev_cells, self._trail_decay)
        if self._palette_name and self._palette_name in PALETTES:
            cells = fx_palette_remap(PALETTES[self._palette_name])(cells, state)
        cells = _apply_hue_rotate(cells, self._hue_deg)
        if self._invert:
            cells = _apply_invert(cells)
        if self._complementary:
            cells = _apply_complementary(cells)
        cells = _apply_mirror(cells, self._mirror)
        cells = _apply_contrast(cells, self._contrast)
        if self._brightness != 1.0:
            cells = {cell: _scale(rgb, self._brightness) for cell, rgb in cells.items()}
        cells = _apply_gamma_lut(cells, self._gamma_lut)

        # Stash for next frame's trail decay
        self._prev_cells = cells
        return cells

    def paint_passthrough(self, cells_in: CellMap, state: MusicState) -> CellMap:
        """Effect-stack only — for the webcam-show source.

        Skips primary/secondary/extra/scene rotation; just runs the post stack.
        """
        if self._blackout:
            self._prev_cells = {}
            return {}
        cells = dict(cells_in)
        cells = _apply_trail(cells, self._prev_cells, self._trail_decay)
        if self._palette_name and self._palette_name in PALETTES:
            cells = fx_palette_remap(PALETTES[self._palette_name])(cells, state)
        cells = _apply_hue_rotate(cells, self._hue_deg)
        if self._invert:
            cells = _apply_invert(cells)
        if self._complementary:
            cells = _apply_complementary(cells)
        cells = _apply_mirror(cells, self._mirror)
        cells = _apply_contrast(cells, self._contrast)
        if self._brightness != 1.0:
            cells = {cell: _scale(rgb, self._brightness) for cell, rgb in cells.items()}
        cells = _apply_gamma_lut(cells, self._gamma_lut)
        self._prev_cells = cells
        return cells

    def _rotate(self, state: MusicState) -> None:
        primary = self._rng.choice(_ROTATING_VIZES)()
        secondary: Optional[Viz] = None
        blend = "single"
        if self._rng.random() < 0.25:
            secondary = self._rng.choice(_ROTATING_VIZES)()
            blend = self._rng.choice(["max", "mask"])
        effects: list[Effect] = []
        n_effects = self._rng.choices([0, 1, 2], weights=[3, 4, 2])[0]
        for _ in range(n_effects):
            choice = self._rng.choice(GENERIC_EFFECTS)
            effects.append(choice)
        duration = self._rng.uniform(20.0, 40.0)
        self._scene = _SceneConfig(primary, secondary, blend, effects, state.elapsed + duration)

    def _viz_for(self, name: str) -> Viz:
        if name not in self._viz_cache:
            for cls in ALL_VIZES:
                if cls.name == name:
                    self._viz_cache[name] = cls()
                    return self._viz_cache[name]
            raise ValueError(f"unknown viz: {name!r}")
        return self._viz_cache[name]

    # --- live-control setters ----------------------------------------------

    def set_locked(self, name: Optional[str]) -> None:
        if name is None or any(c.name == name for c in ALL_VIZES):
            self._locked_name = name

    def set_extras(self, names: list[str]) -> None:
        self._extra_names = [n for n in names if any(c.name == n for c in ALL_VIZES)]

    def set_brightness(self, value: float) -> None:
        self._brightness = max(0.0, min(1.5, float(value)))

    def set_palette(self, name: Optional[str]) -> None:
        self._palette_name = name if (name is None or name in PALETTES) else None

    def set_hue(self, degrees: float) -> None:
        self._hue_deg = max(-180.0, min(180.0, float(degrees)))

    def set_contrast(self, value: float) -> None:
        self._contrast = max(0.5, min(2.5, float(value)))

    def set_gamma(self, value: float) -> None:
        g = max(1.0, min(3.0, float(value)))
        if g != self._gamma:
            self._gamma = g
            self._gamma_lut = _build_gamma_lut(g)

    def set_trail(self, value: float) -> None:
        self._trail_decay = max(0.0, min(0.95, float(value)))

    def set_mirror(self, mode: str) -> None:
        if mode in ("off", "horizontal", "vertical", "quad", "radial"):
            self._mirror = mode

    def set_invert(self, on: bool) -> None:
        self._invert = bool(on)

    def set_complementary(self, on: bool) -> None:
        self._complementary = bool(on)

    def set_blackout(self, on: bool) -> None:
        self._blackout = bool(on)

    def set_beat_multiplier(self, value: float) -> None:
        if value in (0.5, 1.0, 2.0):
            self._beat_multiplier = value

    def set_text(self, *, text: Optional[str] = None, font: Optional[str] = None,
                 speed: Optional[float] = None, direction: Optional[str] = None) -> None:
        viz = self._viz_for("text")
        if isinstance(viz, ScrollingText):
            viz.set_config(text=text, font=font, speed=speed, direction=direction)

    def set_shape(self, name: str) -> None:
        viz = self._viz_for("shape")
        if isinstance(viz, Shape):
            viz.set_shape(name)

    def current_primary_name(self) -> str:
        if self._blackout:
            return "blackout"
        if self._locked_name == "blackout":
            return "none"
        if self._locked_name is not None:
            return self._locked_name
        if self._scene is not None:
            return self._scene.primary.name
        return "—"

    def snapshot(self) -> dict:
        return {
            "primary": self.current_primary_name(),
            "extras": list(self._extra_names),
            "palette": self._palette_name,
            "hue": self._hue_deg,
            "contrast": self._contrast,
            "gamma": self._gamma,
            "trail": self._trail_decay,
            "mirror": self._mirror,
            "invert": self._invert,
            "complementary": self._complementary,
            "blackout": self._blackout,
            "beat_multiplier": self._beat_multiplier,
            "brightness": self._brightness,
        }
