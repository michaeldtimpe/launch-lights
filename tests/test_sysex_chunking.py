"""Boundary tests for RGB (0Bh, <=78) and palette (0Ah, <=97) batch splits.

The 8x8 grid only has 64 cells, so we test the splitter directly using a
mocked oversized input (the internal chunker shouldn't care that cells live
outside the grid for the purposes of splitting)."""
from __future__ import annotations

from launch_lights.device.launchpad_pro import _chunks


def test_chunks_under_limit():
    seq = list(range(10))
    out = list(_chunks(seq, 78))
    assert out == [seq]


def test_chunks_exact_limit():
    seq = list(range(78))
    out = list(_chunks(seq, 78))
    assert len(out) == 1
    assert out[0] == seq


def test_chunks_just_over_limit():
    seq = list(range(79))
    out = list(_chunks(seq, 78))
    assert len(out) == 2
    assert out[0] == list(range(78))
    assert out[1] == [78]


def test_chunks_two_full_messages():
    seq = list(range(156))
    out = list(_chunks(seq, 78))
    assert len(out) == 2
    assert out[0] == list(range(78))
    assert out[1] == list(range(78, 156))


def test_chunks_palette_boundary_97():
    seq = list(range(98))
    out = list(_chunks(seq, 97))
    assert len(out) == 2
    assert len(out[0]) == 97
    assert len(out[1]) == 1
