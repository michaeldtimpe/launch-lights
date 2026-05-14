"""Floyd-Steinberg dither produces valid 6-bit output and conserves brightness."""
from __future__ import annotations

import numpy as np

from launch_lights.util.color import floyd_steinberg_to_6bit


def test_output_shape_and_range():
    img = np.full((8, 8, 3), 128, dtype=np.uint8)
    out = floyd_steinberg_to_6bit(img)
    assert out.shape == (8, 8, 3)
    assert out.dtype == np.uint8
    assert out.max() <= 63
    assert out.min() >= 0


def test_solid_white_quantizes_to_63():
    img = np.full((8, 8, 3), 255, dtype=np.uint8)
    out = floyd_steinberg_to_6bit(img)
    # Some cells may round to 62 due to clamping at 252; the bulk should be 63.
    assert out.max() == 63
    assert np.mean(out) > 60


def test_solid_black_stays_black():
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    out = floyd_steinberg_to_6bit(img)
    assert np.all(out == 0)


def test_midgray_average_is_close_to_32():
    # 128/255 ~ 0.502 -> in 6-bit: 0.502 * 63 ~ 31.6
    img = np.full((8, 8, 3), 128, dtype=np.uint8)
    out = floyd_steinberg_to_6bit(img)
    # Mean across the 8x8 grid should be within ~2 of the true value
    assert abs(np.mean(out) - 31.6) < 2.5


def test_horizontal_gradient_dithers_to_unique_values():
    """A smooth gradient should produce more than 2 distinct 6-bit levels —
    that's the whole point of dithering."""
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    for c in range(8):
        img[:, c] = int(c * 255 / 7)
    out = floyd_steinberg_to_6bit(img)
    unique = len(np.unique(out[..., 0]))
    assert unique >= 4
