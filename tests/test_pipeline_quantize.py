"""Gamma + brightness + 8->6-bit quantization."""
from __future__ import annotations

import numpy as np

from launch_lights.video.pipeline import (
    apply_gamma_brightness,
    quantize_to_6bit,
    to_frame,
)


def test_quantize_drops_bottom_two_bits():
    img = np.array([[[255, 128, 0]]], dtype=np.uint8)
    out = quantize_to_6bit(img)
    assert out[0, 0].tolist() == [63, 32, 0]


def test_gamma_unity_is_identity():
    img = np.arange(256, dtype=np.uint8).reshape(16, 16, 1)
    img = np.repeat(img, 3, axis=2)
    out = apply_gamma_brightness(img, gamma=1.0, brightness=1.0)
    # gamma=1, brightness=1 -> y = x; allow rounding tolerance of 1
    assert np.all(np.abs(out.astype(int) - img.astype(int)) <= 1)


def test_gamma_two_darkens_midtones():
    img = np.full((4, 4, 3), 128, dtype=np.uint8)
    out = apply_gamma_brightness(img, gamma=2.2, brightness=1.0)
    # 128/255=0.502; 0.502^2.2 ≈ 0.218; *255 ≈ 55
    assert 50 <= out[0, 0, 0] <= 60


def test_brightness_zero_blanks():
    img = np.full((4, 4, 3), 255, dtype=np.uint8)
    out = apply_gamma_brightness(img, gamma=1.0, brightness=0.0)
    assert np.all(out == 0)


def test_to_frame_round_trip():
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    img[0, 0] = [10, 20, 30]
    img[7, 7] = [40, 50, 60]
    frame = to_frame(img)
    assert frame.cells[(0, 0)].r == 10
    assert frame.cells[(0, 0)].g == 20
    assert frame.cells[(0, 0)].b == 30
    assert frame.cells[(7, 7)].r == 40
