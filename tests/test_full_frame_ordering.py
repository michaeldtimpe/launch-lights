"""The 0Fh SysEx command dumps RGB triples bottom-up, left-to-right.

Our coordinate system uses row 0 = top everywhere else. This test enforces
that the vertical flip happens in exactly one place — the device serializer —
by checking byte positions of corner pixels in the emitted SysEx payload.
"""
from __future__ import annotations

import numpy as np

from launch_lights.device.launchpad_pro import HEADER, LaunchpadProOut
from launch_lights.engine.plan import RGBFullFramePlan
from tests.fixtures.fake_midi import FakeMidiOut


def _emit(grid: np.ndarray) -> tuple[int, ...]:
    fake = FakeMidiOut()
    d = LaunchpadProOut(port_name="<fake>", _port=fake)
    d.execute(RGBFullFramePlan(grid=grid))
    return fake.sysex[-1]


def test_top_left_lands_at_end_of_payload():
    # Top-left pixel (0,0) should land at the END of the payload because
    # the protocol sends bottom row first, and within each row left-to-right.
    grid = np.zeros((8, 8, 3), dtype=np.uint8)
    grid[0, 0] = (63, 0, 0)  # unique red marker at top-left
    payload = _emit(grid)

    # payload layout: HEADER(5) + 0x0F + 0x01 + 192 rgb bytes
    body = payload[7:]
    assert len(body) == 192

    # Bottom-up means top-left pixel is in the LAST row sent.
    # Within that last row, col 0 is the FIRST triple (left-to-right).
    # So top-left pixel occupies body[-24], body[-23], body[-22] (the first
    # triple of the last 8-cell row).
    assert body[-24:-21] == (63, 0, 0)


def test_bottom_right_lands_at_start_of_payload():
    grid = np.zeros((8, 8, 3), dtype=np.uint8)
    grid[7, 7] = (0, 0, 63)  # unique blue marker at bottom-right
    payload = _emit(grid)
    body = payload[7:]

    # Bottom row goes first; within bottom row, col 7 is the LAST triple
    # of that row -> body[21:24] is the 8th triple in the first row.
    assert body[21:24] == (0, 0, 63)


def test_top_right_lands_at_last_triple_of_payload():
    grid = np.zeros((8, 8, 3), dtype=np.uint8)
    grid[0, 7] = (0, 63, 0)  # unique green marker at top-right
    payload = _emit(grid)
    body = payload[7:]

    # Top row sent last; col 7 last within that row -> very last 3 bytes.
    assert body[-3:] == (0, 63, 0)


def test_bottom_left_lands_at_first_triple_of_payload():
    grid = np.zeros((8, 8, 3), dtype=np.uint8)
    grid[7, 0] = (63, 63, 63)  # unique white marker at bottom-left
    payload = _emit(grid)
    body = payload[7:]

    # First row sent is bottom row; first triple is col 0.
    assert body[0:3] == (63, 63, 63)


def test_payload_header_is_correct():
    grid = np.zeros((8, 8, 3), dtype=np.uint8)
    payload = _emit(grid)
    # F0 .. header(5) ..  0F 01 .. body .. F7
    # FakeMidiOut records inner bytes only (between F0/F7), so we see:
    assert payload[:5] == HEADER
    assert payload[5] == 0x0F
    assert payload[6] == 0x01  # grid type = 8x8


def test_six_bit_values_pass_through_unchanged():
    grid = np.zeros((8, 8, 3), dtype=np.uint8)
    grid[3, 4] = (12, 34, 56)
    payload = _emit(grid)
    body = payload[7:]
    # row 3 (from top) = row 4 from bottom in 0-indexed bottom-up order
    # = the 5th row sent (index 4). Within that row, col 4 is the 5th triple.
    # Byte offset: row_idx * 8 * 3 + col_idx * 3 = 4*24 + 4*3 = 108
    row_from_bottom = 7 - 3  # = 4
    col = 4
    off = row_from_bottom * 24 + col * 3
    assert body[off:off + 3] == (12, 34, 56)
