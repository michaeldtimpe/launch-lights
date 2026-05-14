"""SysEx-based LED I/O for the Novation Launchpad Pro 2015 (MK1).

Bytes verified against:
  - Launchpad Pro Programmers Reference Guide 1.01 (Focusrite/Novation)
  - github.com/FMMT666/launchpad.py (cross-reference for 0Bh format)

Use the "Launchpad Pro Standalone Port" — layout selection (2Ch) is
documented as Standalone-only.

Coordinate convention used here matches the rest of the codebase:
    Cell = (row, col); row 0 = top, col 0 = left, both 0..7.
The 0Fh full-frame command requires bottom-up dump order; that vertical
flip happens in EXACTLY ONE place: ``_execute_full_frame``.
"""
from __future__ import annotations

import logging
from typing import Iterable

import mido
import numpy as np

from launch_lights.engine.frame import Cell, RGB
from launch_lights.engine.plan import (
    NoOpPlan,
    PaletteDiffPlan,
    RenderPlan,
    RGBDiffPlan,
    RGBFullFramePlan,
)

log = logging.getLogger(__name__)


SYSEX_START = 0xF0
SYSEX_END = 0xF7

# Manufacturer Focusrite/Novation + LP Pro MK1 device prefix
HEADER: tuple[int, ...] = (0x00, 0x20, 0x29, 0x02, 0x10)

CMD_LIGHT_PALETTE = 0x0A       # <pad><color>, repeat <= 97
CMD_LIGHT_RGB = 0x0B           # <pad><r><g><b>, repeat <= 78
CMD_LIGHT_COLUMN = 0x0C
CMD_LIGHT_ROW = 0x0D
CMD_LIGHT_ALL = 0x0E           # <color> (palette), no repeat
CMD_LIGHT_GRID_RGB = 0x0F      # <grid_type><r><g><b>..., grid 0=10x10, 1=8x8
CMD_FLASH = 0x23
CMD_PULSE = 0x28
CMD_SELECT_LAYOUT = 0x2C       # <00..03>, Standalone-only

LAYOUT_NOTE = 0x00
LAYOUT_DRUM = 0x01
LAYOUT_FADER = 0x02
LAYOUT_PROGRAMMER = 0x03

GRID_TYPE_10x10 = 0x00
GRID_TYPE_8x8 = 0x01

# Maximum repeat counts (data groups per SysEx message)
MAX_REPEAT_RGB = 78
MAX_REPEAT_PALETTE = 97


def pad_for(row: int, col: int) -> int:
    """Return the Programmer-layout note number for an 8x8 grid cell.

    Row 0 = top, col 0 = left. Top-left = 81, bottom-right = 18.
    "+1 = right, +10 = up" in the device's coordinate space.
    """
    if not (0 <= row <= 7 and 0 <= col <= 7):
        raise ValueError(f"cell out of grid: ({row},{col})")
    return (8 - row) * 10 + (col + 1)


NOTE_TO_CELL: dict[int, Cell] = {
    pad_for(r, c): (r, c) for r in range(8) for c in range(8)
}


def _chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class LaunchpadProOut:
    """SysEx-based LED writer for the LP Pro MK1 Standalone port.

    Owns the wire format. ``execute(plan)`` is the single dispatch surface;
    everything else is internal byte-shuffling.
    """

    # Preallocated buffer for the 0Fh full-frame message (mutated in place
    # every tick to avoid per-frame allocation churn).
    _FULL_FRAME_FIXED_HEAD = bytes((*HEADER, CMD_LIGHT_GRID_RGB, GRID_TYPE_8x8))
    _FULL_FRAME_DATA_OFFSET = len(_FULL_FRAME_FIXED_HEAD)
    _FULL_FRAME_DATA_LEN = 64 * 3
    _FULL_FRAME_TOTAL = _FULL_FRAME_DATA_OFFSET + _FULL_FRAME_DATA_LEN

    def __init__(self, port_name: str, *, _port=None) -> None:
        self.port_name = port_name
        self._port = _port if _port is not None else mido.open_output(port_name)
        self._closed = False

        self._full_frame_buf = bytearray(self._FULL_FRAME_TOTAL)
        self._full_frame_buf[: self._FULL_FRAME_DATA_OFFSET] = self._FULL_FRAME_FIXED_HEAD

    # --- mode setup ---------------------------------------------------------

    def enter_programmer_mode(self) -> None:
        self._send_sysex_inner((*HEADER, CMD_SELECT_LAYOUT, LAYOUT_PROGRAMMER))

    def exit_programmer_mode(self) -> None:
        self._send_sysex_inner((*HEADER, CMD_SELECT_LAYOUT, LAYOUT_NOTE))

    # --- frame painting -----------------------------------------------------

    def execute(self, plan: RenderPlan) -> int:
        """Dispatch on plan type. Returns the number of pads written
        (0 for NoOp). All MIDI I/O for the engine flows through here."""
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
                    (pad_for(row, col), rgb.r & 0x3F, rgb.g & 0x3F, rgb.b & 0x3F)
                )
            self._send_sysex_inner((*HEADER, CMD_LIGHT_RGB, *payload))
            sent += len(chunk)
        return sent

    def _execute_full_frame(self, plan: RGBFullFramePlan) -> int:
        """Emit the fixed-size 0Fh message after flipping rows bottom-up.

        This is THE ONE PLACE where top-origin -> bottom-up conversion happens.
        Mutates the preallocated buffer in place.
        """
        grid = plan.grid
        if grid.shape != (8, 8, 3) or grid.dtype != np.uint8:
            raise ValueError(
                f"RGBFullFramePlan.grid must be (8,8,3) uint8, got {grid.shape} {grid.dtype}"
            )
        # Mask to 6 bits and flip vertically (bottom row first per protocol)
        bottom_up = np.flipud(grid & 0x3F)
        self._full_frame_buf[self._FULL_FRAME_DATA_OFFSET :] = bottom_up.tobytes()
        self._send_sysex_inner(bytes(self._full_frame_buf))
        return 64

    def _execute_palette_diff(self, plan: PaletteDiffPlan) -> int:
        items = list(plan.cells.items())
        sent = 0
        for chunk in _chunks(items, MAX_REPEAT_PALETTE):
            payload: list[int] = []
            for (row, col), idx in chunk:
                payload.extend((pad_for(row, col), idx & 0x7F))
            self._send_sysex_inner((*HEADER, CMD_LIGHT_PALETTE, *payload))
            sent += len(chunk)
        return sent

    # --- maintenance --------------------------------------------------------

    def blackout(self) -> None:
        """Turn every LED off in one SysEx message (0Eh with palette index 0)."""
        self._send_sysex_inner((*HEADER, CMD_LIGHT_ALL, 0x00))

    def close(self) -> None:
        """Idempotent shutdown: blackout, exit programmer mode, close port.

        Tolerates repeated calls and partially-initialized state — SIGINT,
        signal handler, and atexit can all fire on the same instance.
        """
        if self._closed:
            return
        self._closed = True
        for step in (self._safe_blackout, self._safe_exit_programmer, self._safe_close_port):
            try:
                step()
            except Exception:  # noqa: BLE001
                log.debug("shutdown step failed", exc_info=True)

    def _safe_blackout(self) -> None:
        self._send_sysex_inner((*HEADER, CMD_LIGHT_ALL, 0x00))

    def _safe_exit_programmer(self) -> None:
        self._send_sysex_inner((*HEADER, CMD_SELECT_LAYOUT, LAYOUT_NOTE))

    def _safe_close_port(self) -> None:
        if self._port is not None:
            self._port.close()

    # --- internals ----------------------------------------------------------

    def _send_sysex_inner(self, data) -> None:
        """``data`` is the inner SysEx bytes (between F0 and F7, exclusive).

        We pass it to mido as a tuple — mido adds the F0/F7 framing on the wire.
        """
        if self._closed and self._port is None:
            return
        self._port.send(mido.Message("sysex", data=tuple(data)))
