"""Aspect-fit modes: crop / letterbox / stretch."""
from __future__ import annotations

import numpy as np

from launch_lights.video.pipeline import bgr_to_rgb, fit_and_downsample


def _solid(h: int, w: int, color=(255, 128, 64)) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = color
    return img


def test_stretch_returns_8x8():
    img = _solid(1080, 1920)
    out = fit_and_downsample(img, "stretch")
    assert out.shape == (8, 8, 3)
    # Solid color should survive
    assert np.allclose(out, np.array([255, 128, 64], dtype=np.uint8), atol=1)


def test_crop_returns_8x8():
    # 1920x1080 BGR-ish image, all the same color -> identical output
    img = _solid(1080, 1920)
    out = fit_and_downsample(img, "crop")
    assert out.shape == (8, 8, 3)


def test_letterbox_zero_pads_short_axis():
    # Wide image: 100 high, 800 wide -> scale 8/800 = 0.01 -> 1 row x 8 cols
    img = _solid(100, 800)
    out = fit_and_downsample(img, "letterbox")
    assert out.shape == (8, 8, 3)
    # Top and bottom rows should be black; middle row(s) should hold the color
    # The image is squashed to ~1 px height in the 8x8 grid.
    nonzero_rows = [r for r in range(8) if np.any(out[r] != 0)]
    assert len(nonzero_rows) >= 1
    assert nonzero_rows[0] != 0  # padded on top
    assert nonzero_rows[-1] != 7  # padded on bottom


def test_bgr_to_rgb_swaps_channels():
    bgr = np.array([[[10, 20, 30]]], dtype=np.uint8)  # (1,1,3) BGR
    rgb = bgr_to_rgb(bgr)
    assert rgb.shape == (1, 1, 3)
    assert rgb[0, 0].tolist() == [30, 20, 10]


def test_crop_center_picks_middle_square():
    # Build an image where the center 1080x1080 square is red and the
    # left/right strips are blue. Crop should yield all red.
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    img[:, :] = (0, 0, 255)  # blue everywhere
    img[:, (1920 - 1080) // 2 : (1920 - 1080) // 2 + 1080] = (255, 0, 0)  # red center
    out = fit_and_downsample(img, "crop")
    # All red, no blue
    assert np.all(out[..., 0] == 255)
    assert np.all(out[..., 2] == 0)
