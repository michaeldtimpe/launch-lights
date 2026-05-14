"""Round-trip the (row, col) -> Launchpad Pro Programmer-layout pad number."""
from __future__ import annotations

import pytest

from launch_lights.device.launchpad_pro import NOTE_TO_CELL, pad_for


def test_known_corners():
    assert pad_for(0, 0) == 81  # top-left
    assert pad_for(0, 7) == 88  # top-right
    assert pad_for(7, 0) == 11  # bottom-left
    assert pad_for(7, 7) == 18  # bottom-right


def test_neighbors():
    # "+1 = right, +10 = up"
    assert pad_for(0, 1) - pad_for(0, 0) == 1
    assert pad_for(1, 0) - pad_for(0, 0) == -10  # one row down = -10


def test_round_trip_all_cells():
    for r in range(8):
        for c in range(8):
            note = pad_for(r, c)
            assert NOTE_TO_CELL[note] == (r, c)


def test_all_64_unique():
    notes = {pad_for(r, c) for r in range(8) for c in range(8)}
    assert len(notes) == 64


def test_out_of_range_raises():
    for bad in ((-1, 0), (8, 0), (0, -1), (0, 8)):
        with pytest.raises(ValueError):
            pad_for(*bad)
