"""Video -> Frame transformation pipeline.

Order matters:

    BGR uint8 H x W  (cv2.VideoCapture.read())
       -> BGR -> RGB
       -> fit_and_downsample (crop | letterbox | stretch, INTER_AREA)
    RGB uint8 8 x 8
       -> apply_gamma_brightness (cv2.LUT in 8-bit space)
    RGB uint8 8 x 8 (gamma-corrected)
       -> [optional] floyd_steinberg_dither_to_6bit
       -> quantize_to_6bit (>>2)  [if not dithered to 6-bit already]
    RGB uint8 8 x 8 in 0..63
       -> to_frame
    Frame
"""
from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from launch_lights.engine.frame import Frame, RGB

FitMode = Literal["crop", "letterbox", "stretch"]


def bgr_to_rgb(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def fit_and_downsample(img: np.ndarray, fit: FitMode) -> np.ndarray:
    """Return an (8, 8, 3) uint8 image in the same color space as the input."""
    if img.ndim != 3 or img.shape[2] != 3:
        raise ValueError(f"expected H x W x 3, got {img.shape}")
    h, w = img.shape[:2]

    if fit == "stretch":
        return cv2.resize(img, (8, 8), interpolation=cv2.INTER_AREA)

    if fit == "crop":
        side = min(h, w)
        y0 = (h - side) // 2
        x0 = (w - side) // 2
        square = img[y0 : y0 + side, x0 : x0 + side]
        return cv2.resize(square, (8, 8), interpolation=cv2.INTER_AREA)

    if fit == "letterbox":
        if h == 0 or w == 0:
            return np.zeros((8, 8, 3), dtype=img.dtype)
        # Scale so the longer side is 8, then pad to 8x8.
        scale = 8.0 / max(h, w)
        new_h = max(1, min(8, round(h * scale)))
        new_w = max(1, min(8, round(w * scale)))
        small = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        out = np.zeros((8, 8, 3), dtype=img.dtype)
        y = (8 - new_h) // 2
        x = (8 - new_w) // 2
        out[y : y + new_h, x : x + new_w] = small
        return out

    raise ValueError(f"unknown fit mode: {fit!r}")


# --- gamma / brightness ----------------------------------------------------

_GAMMA_LUT_CACHE: dict[tuple[float, float], np.ndarray] = {}


def _gamma_brightness_lut(gamma: float, brightness: float) -> np.ndarray:
    """256-entry uint8 LUT applying gamma then brightness scaling."""
    key = (round(gamma, 4), round(brightness, 4))
    if key not in _GAMMA_LUT_CACHE:
        x = np.arange(256, dtype=np.float32) / 255.0
        # gamma > 1 darkens midtones (compensates for LED non-linear response)
        y = np.power(x, gamma) * brightness
        y = np.clip(y, 0.0, 1.0)
        _GAMMA_LUT_CACHE[key] = np.round(y * 255.0).astype(np.uint8)
    return _GAMMA_LUT_CACHE[key]


def apply_gamma_brightness(img: np.ndarray, gamma: float, brightness: float) -> np.ndarray:
    return cv2.LUT(img, _gamma_brightness_lut(gamma, brightness))


# --- quantization ----------------------------------------------------------


def quantize_to_6bit(img: np.ndarray) -> np.ndarray:
    """8-bit (0..255) -> 6-bit (0..63) by dropping the bottom 2 bits."""
    return img >> 2


def to_frame(img_6bit: np.ndarray) -> Frame:
    """Build a Frame from an (8, 8, 3) uint8 RGB image already in 0..63."""
    if img_6bit.shape != (8, 8, 3):
        raise ValueError(f"expected (8,8,3) image, got {img_6bit.shape}")
    cells: dict[tuple[int, int], RGB] = {}
    for r in range(8):
        for c in range(8):
            cells[(r, c)] = RGB(
                int(img_6bit[r, c, 0]),
                int(img_6bit[r, c, 1]),
                int(img_6bit[r, c, 2]),
            )
    return Frame(cells=cells)
