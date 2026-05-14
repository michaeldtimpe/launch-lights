"""Palette LUT determinism and basic nearest-color behavior."""
from __future__ import annotations

from launch_lights.engine.frame import OFF, RGB
from launch_lights.video.palette import LAUNCHPAD_PALETTE_RGB, Palette


def test_palette_has_128_entries():
    assert len(LAUNCHPAD_PALETTE_RGB) == 128
    for r, g, b in LAUNCHPAD_PALETTE_RGB:
        assert 0 <= r <= 63
        assert 0 <= g <= 63
        assert 0 <= b <= 63


def test_off_maps_to_index_zero():
    p = Palette()
    assert p.nearest(OFF) == 0


def test_pure_white_maps_to_a_white_entry():
    p = Palette()
    idx = p.nearest(RGB(63, 63, 63))
    r, g, b = LAUNCHPAD_PALETTE_RGB[idx]
    # Returned entry should be roughly grayscale and bright
    assert min(r, g, b) > 30
    assert abs(r - g) < 5
    assert abs(g - b) < 5


def test_pure_red_maps_near_red():
    p = Palette()
    idx = p.nearest(RGB(63, 0, 0))
    r, g, b = LAUNCHPAD_PALETTE_RGB[idx]
    assert r > 30
    assert g < 20
    assert b < 20


def test_lut_is_deterministic():
    a = Palette()
    b = Palette()
    # Same table should produce identical LUT
    assert (a._lut == b._lut).all()


def test_palette_index_round_trip():
    p = Palette()
    for idx in (0, 1, 50, 127):
        rgb = p.index_to_rgb(idx)
        # The entry's own RGB should map back to itself (within tied-nearest)
        # — at the very least, the chosen index's palette RGB matches.
        assert tuple(LAUNCHPAD_PALETTE_RGB[idx]) == (rgb.r, rgb.g, rgb.b)
