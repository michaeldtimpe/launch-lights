"""Exact byte streams emitted by every LaunchpadProMK3Out operation.

Pins the MK3 wire format. Cross-reference: Launchpad Pro [MK3] Programmer's
Reference Manual — model byte 0Eh, mode-select cmd 0Eh, lighting cmd 03h
with repeated <type><pad>[<data>...] records, 7-bit RGB.
"""
from __future__ import annotations

import numpy as np
import pytest

from launch_lights.device.launchpad_pro import pad_for
from launch_lights.device.launchpad_pro_mk3 import (
    HEADER,
    LaunchpadProMK3Out,
)
from launch_lights.engine.frame import RGB
from launch_lights.engine.plan import (
    NoOpPlan,
    PaletteDiffPlan,
    RGBDiffPlan,
    RGBFullFramePlan,
)
from tests.fixtures.fake_midi import FakeMidiOut


@pytest.fixture
def dev() -> tuple[LaunchpadProMK3Out, FakeMidiOut]:
    fake = FakeMidiOut()
    return LaunchpadProMK3Out(port_name="<fake>", _port=fake), fake


def _sysex_inner(*payload: int) -> tuple[int, ...]:
    return tuple((*HEADER, *payload))


def test_header_uses_mk3_model_byte():
    # Sanity: the MK3 model byte is 0Eh (MK1 uses 0x10).
    assert HEADER == (0x00, 0x20, 0x29, 0x02, 0x0E)


def test_enter_programmer_mode(dev):
    d, fake = dev
    d.enter_programmer_mode()
    # 0Eh = mode select, 01 = Programmer mode
    assert fake.sysex == [_sysex_inner(0x0E, 0x01)]


def test_exit_programmer_mode(dev):
    d, fake = dev
    d.exit_programmer_mode()
    # 0Eh = mode select, 00 = Live mode
    assert fake.sysex == [_sysex_inner(0x0E, 0x00)]


def test_single_rgb_cell_upscales_to_7bit(dev):
    d, fake = dev
    d.execute(RGBDiffPlan(cells={(0, 0): RGB(63, 0, 0)}))
    # 03h <type=03h RGB> <pad=81> <r=126> <g=0> <b=0>
    # 63 (6-bit) << 1 = 126 (7-bit)
    assert fake.sysex == [_sysex_inner(0x03, 0x03, pad_for(0, 0), 126, 0, 0)]


def test_rgb_diff_all_64_cells_in_one_message(dev):
    d, fake = dev
    cells = {(r, c): RGB(1, 2, 3) for r in range(8) for c in range(8)}
    d.execute(RGBDiffPlan(cells=cells))
    assert len(fake.sysex) == 1
    payload = fake.sysex[0]
    # header(5) + cmd(1) + 64*(type,pad,r,g,b) = 6 + 320 = 326 inner bytes
    assert len(payload) == 5 + 1 + 64 * 5
    assert payload[:6] == (*HEADER, 0x03)
    # First record is type=03, then the pad number, then upscaled RGB
    assert payload[6] == 0x03  # RGB type
    # r=1 << 1 = 2, g=2 << 1 = 4, b=3 << 1 = 6
    # The first cell could be any (r,c); verify all records have those scaled values
    for i in range(64):
        base = 6 + i * 5
        assert payload[base] == 0x03         # type
        # payload[base+1] is the pad number — we don't assert order
        assert payload[base + 2] == 2         # r upscaled
        assert payload[base + 3] == 4         # g upscaled
        assert payload[base + 4] == 6         # b upscaled


def test_palette_single_cell(dev):
    d, fake = dev
    d.execute(PaletteDiffPlan(cells={(0, 0): 5}))
    # 03h <type=00 palette> <pad=81> <color=5>
    assert fake.sysex == [_sysex_inner(0x03, 0x00, pad_for(0, 0), 5)]


def test_palette_batch_all_64_in_one_message(dev):
    d, fake = dev
    cells = {(r, c): (r * 8 + c) for r in range(8) for c in range(8)}
    d.execute(PaletteDiffPlan(cells=cells))
    assert len(fake.sysex) == 1
    payload = fake.sysex[0]
    # header(5) + cmd(1) + 64*(type, pad, color) = 6 + 192 = 198 inner bytes
    assert len(payload) == 5 + 1 + 64 * 3
    assert payload[:6] == (*HEADER, 0x03)
    # Every record's type byte is palette (0x00)
    for i in range(64):
        base = 6 + i * 3
        assert payload[base] == 0x00


def test_full_frame_emits_03h_rgb_records(dev):
    d, fake = dev
    grid = np.zeros((8, 8, 3), dtype=np.uint8)
    d.execute(RGBFullFramePlan(grid=grid))
    assert len(fake.sysex) == 1
    payload = fake.sysex[0]
    # Same shape as a 64-cell RGB diff: 6 + 64*5 = 326 inner bytes
    assert len(payload) == 5 + 1 + 64 * 5
    assert payload[:6] == (*HEADER, 0x03)
    # All-zero frame -> every RGB triple is (0, 0, 0)
    for i in range(64):
        base = 6 + i * 5
        assert payload[base] == 0x03            # RGB type
        assert payload[base + 2 : base + 5] == (0, 0, 0)


def test_full_frame_does_not_vertical_flip(dev):
    """Unlike MK1's 0Fh, MK3 lighting uses Programmer pad numbers directly,
    so top-origin -> pad_for is the only mapping. Verify row 0 ends up on
    the top row pads (81..88)."""
    d, fake = dev
    grid = np.zeros((8, 8, 3), dtype=np.uint8)
    grid[0, 0] = (50, 0, 0)  # top-left should map to pad 81
    d.execute(RGBFullFramePlan(grid=grid))
    payload = fake.sysex[0]
    # Find the record whose pad number is 81 (top-left in Programmer layout)
    found = False
    for i in range(64):
        base = 6 + i * 5
        if payload[base + 1] == pad_for(0, 0):
            assert payload[base + 2] == 100  # 50 << 1
            assert payload[base + 3] == 0
            assert payload[base + 4] == 0
            found = True
            break
    assert found, "expected a record for pad_for(0,0)"


def test_blackout_sends_palette_zero_to_all_pads(dev):
    d, fake = dev
    d.blackout()
    assert len(fake.sysex) == 1
    payload = fake.sysex[0]
    # 64 palette-0 records: 6 header/cmd + 64*3 = 198 bytes
    assert len(payload) == 5 + 1 + 64 * 3
    assert payload[:6] == (*HEADER, 0x03)
    for i in range(64):
        base = 6 + i * 3
        assert payload[base] == 0x00       # palette type
        assert payload[base + 2] == 0x00   # color index 0 = off


def test_noop_plan_sends_nothing(dev):
    d, fake = dev
    d.execute(NoOpPlan())
    assert fake.sysex == []


def test_close_is_idempotent(dev):
    d, fake = dev
    d.close()
    d.close()  # must not raise
    assert fake.closed is True


def test_close_blacks_out_and_returns_to_live(dev):
    d, fake = dev
    d.close()
    # First message: blackout (lighting cmd 03h, 64 palette-0 records)
    assert fake.sysex[0][:6] == (*HEADER, 0x03)
    # Second message: mode select to Live
    assert fake.sysex[1] == _sysex_inner(0x0E, 0x00)
    assert fake.closed is True


def test_close_with_restore_mode_false_skips_exit_programmer(dev):
    d, fake = dev
    d.close(restore_mode=False)
    # Should have sent ONLY the blackout (cmd 03h, 64 palette-0 records).
    # The mode-select-to-Live message must NOT be emitted, or the device
    # would repaint over our blackout.
    assert len(fake.sysex) == 1
    assert fake.sysex[0][:6] == (*HEADER, 0x03)
    assert fake.closed is True
