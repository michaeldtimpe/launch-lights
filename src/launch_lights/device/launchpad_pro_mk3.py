"""SysEx-based LED I/O for the Novation Launchpad Pro MK3.

Bytes follow the Launchpad Pro [MK3] Programmer's Reference Manual.

Differences from the MK1 backend (``launchpad_pro.py``):
  * Manufacturer/product prefix uses model byte ``0x0E`` (MK1 uses ``0x10``).
  * Mode select is the ``0Eh`` command with ``00``=Live / ``01``=Programmer.
    (The MK1's ``2Ch`` layout-select command does not exist on MK3.)
  * Lighting is a single ``03h`` command with repeated
    ``<type> <pad> <data...>`` records. Types: ``00``=palette,
    ``01``=flash, ``02``=pulse, ``03``=RGB.
  * RGB channels are 7-bit (0..127). This backend upscales the engine's
    6-bit values by left-shift (``v << 1``). Top end becomes 126 instead of
    127 — perceptually identical and avoids per-pixel multiplication.
  * No 0Fh-equivalent 8x8 grid-dump. ``RGBFullFramePlan`` is rendered as
    one ``03h`` message with 64 RGB records — fits comfortably in a single
    SysEx.
  * No "all LEDs off" shortcut. Blackout sends palette-index 0 to all 64
    pads in one ``03h`` message.

Pad numbering matches the MK1 Programmer layout (``pad_for`` is reused).
"""
from __future__ import annotations

import logging
from typing import Iterable

import mido
import numpy as np

from launch_lights.device.launchpad_pro import pad_for
from launch_lights.engine.frame import Cell, RGB
from launch_lights.engine.plan import (
    NoOpPlan,
    PaletteDiffPlan,
    RenderPlan,
    RGBDiffPlan,
    RGBFullFramePlan,
)

log = logging.getLogger(__name__)


# Manufacturer Focusrite/Novation + LP Pro MK3 device prefix
HEADER: tuple[int, ...] = (0x00, 0x20, 0x29, 0x02, 0x0E)

CMD_MODE_SELECT = 0x0E         # <00=Live | 01=Programmer>
CMD_LIGHTING = 0x03            # [<type> <pad> <data...>]+

LIGHT_TYPE_PALETTE = 0x00      # data: 1 byte (color index 0..127)
LIGHT_TYPE_FLASH = 0x01        # data: 2 bytes
LIGHT_TYPE_PULSE = 0x02        # data: 1 byte
LIGHT_TYPE_RGB = 0x03          # data: 3 bytes (r, g, b), each 0..127

MODE_LIVE = 0x00
MODE_PROGRAMMER = 0x01

# One full 8x8 grid (64 records) fits in a single SysEx message for every
# lighting type — caps below match that, leaving headroom on the device's
# SysEx buffer rather than maximising throughput.
MAX_REPEAT_RGB = 64
MAX_REPEAT_PALETTE = 64


def _chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _to7(v: int) -> int:
    """Upscale a 6-bit (0..63) channel to 7-bit (0..126). Top bit is the
    engine's saturation bit, so a simple left-shift preserves relative
    intensity without rounding artifacts."""
    return (v & 0x3F) << 1


class LaunchpadProMK3Out:
    """SysEx-based LED writer for the LP Pro MK3.

    Public surface mirrors ``LaunchpadProOut`` so the CLI can hold either
    one without caring about the wire format."""

    def __init__(self, port_name: str, *, _port=None) -> None:
        self.port_name = port_name
        self._port = _port if _port is not None else mido.open_output(port_name)
        self._closed = False

    # --- mode setup ---------------------------------------------------------

    def enter_programmer_mode(self) -> None:
        self._send_sysex_inner((*HEADER, CMD_MODE_SELECT, MODE_PROGRAMMER))

    def exit_programmer_mode(self) -> None:
        self._send_sysex_inner((*HEADER, CMD_MODE_SELECT, MODE_LIVE))

    # --- frame painting -----------------------------------------------------

    def execute(self, plan: RenderPlan) -> int:
        if isinstance(plan, NoOpPlan):
            return 0
        if isinstance(plan, RGBDiffPlan):
            return self._execute_rgb_diff(plan)
        if isinstance(plan, RGBFullFramePlan):
            return self._execute_full_frame(plan)
        if isinstance(plan, PaletteDiffPlan):
            return self._execute_palette_diff(plan)
        raise TypeError(f"unknown plan type: {type(plan).__name__}")

    def _execute_rgb_diff(self, plan: RGBDiffPlan) -> int:
        items = list(plan.cells.items())
        sent = 0
        for chunk in _chunks(items, MAX_REPEAT_RGB):
            payload: list[int] = []
            for (row, col), rgb in chunk:
                payload.extend(
                    (
                        LIGHT_TYPE_RGB,
                        pad_for(row, col),
                        _to7(rgb.r),
                        _to7(rgb.g),
                        _to7(rgb.b),
                    )
                )
            self._send_sysex_inner((*HEADER, CMD_LIGHTING, *payload))
            sent += len(chunk)
        return sent

    def _execute_full_frame(self, plan: RGBFullFramePlan) -> int:
        grid = plan.grid
        if grid.shape != (8, 8, 3) or grid.dtype != np.uint8:
            raise ValueError(
                f"RGBFullFramePlan.grid must be (8,8,3) uint8, got {grid.shape} {grid.dtype}"
            )
        # No grid-dump shortcut on MK3: emit all 64 cells as RGB records.
        # Top-origin order maps directly via pad_for — no vertical flip is
        # needed here (unlike MK1's 0Fh).
        payload: list[int] = []
        for r in range(8):
            for c in range(8):
                rr, gg, bb = grid[r, c]
                payload.extend(
                    (
                        LIGHT_TYPE_RGB,
                        pad_for(r, c),
                        _to7(int(rr)),
                        _to7(int(gg)),
                        _to7(int(bb)),
                    )
                )
        self._send_sysex_inner((*HEADER, CMD_LIGHTING, *payload))
        return 64

    def _execute_palette_diff(self, plan: PaletteDiffPlan) -> int:
        items = list(plan.cells.items())
        sent = 0
        for chunk in _chunks(items, MAX_REPEAT_PALETTE):
            payload: list[int] = []
            for (row, col), idx in chunk:
                payload.extend(
                    (LIGHT_TYPE_PALETTE, pad_for(row, col), idx & 0x7F)
                )
            self._send_sysex_inner((*HEADER, CMD_LIGHTING, *payload))
            sent += len(chunk)
        return sent

    # --- maintenance --------------------------------------------------------

    def blackout(self) -> None:
        """Turn every LED off in one SysEx message (palette-0 over all 64
        pads)."""
        payload: list[int] = []
        for r in range(8):
            for c in range(8):
                payload.extend((LIGHT_TYPE_PALETTE, pad_for(r, c), 0x00))
        self._send_sysex_inner((*HEADER, CMD_LIGHTING, *payload))

    def close(self, *, restore_mode: bool = True) -> None:
        """Idempotent shutdown: blackout, optionally return to Live mode,
        close port. ``restore_mode=False`` leaves the device dark in
        Programmer Mode."""
        if self._closed:
            return
        self._closed = True
        steps = [self._safe_blackout]
        if restore_mode:
            steps.append(self._safe_exit_programmer)
        steps.append(self._safe_close_port)
        for step in steps:
            try:
                step()
            except Exception:  # noqa: BLE001
                log.debug("shutdown step failed", exc_info=True)

    def _safe_blackout(self) -> None:
        self.blackout()

    def _safe_exit_programmer(self) -> None:
        self._send_sysex_inner((*HEADER, CMD_MODE_SELECT, MODE_LIVE))

    def _safe_close_port(self) -> None:
        if self._port is not None:
            self._port.close()

    # --- internals ----------------------------------------------------------

    def _send_sysex_inner(self, data) -> None:
        if self._closed and self._port is None:
            return
        self._port.send(mido.Message("sysex", data=tuple(data)))
