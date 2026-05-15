# Launchpad Pro SysEx protocol

`launch-lights` supports two device generations:

- **MK1 (2015)** — `src/launch_lights/device/launchpad_pro.py`
- **MK3** — `src/launch_lights/device/launchpad_pro_mk3.py`

Both expose the same `enter_programmer_mode / exit_programmer_mode /
execute(plan) / blackout / close` surface to the engine. Detection +
selection happens in `device/midi_io.py::detect_launchpad()` — the CLI's
`_resolve_device` returns `(port_name, model)` and `_open_device`
constructs the right class.

---

## MK1 (2015)

This is the subset of the Programmer's Reference Guide that the MK1
backend implements. Bytes verified against:

- [Launchpad Pro Programmers Reference Guide 1.01](https://fael-downloads-prod.focusrite.com/customer/prod/s3fs-public/downloads/Launchpad%20Pro%20Programmers%20Reference%20Guide%201.01.pdf) (Focusrite/Novation, official PDF)
- Cross-referenced against [FMMT666/launchpad.py](https://github.com/FMMT666/launchpad.py)

## SysEx framing

Every message has the form:

```
F0  00 20 29 02 10  <cmd>  <payload...>  F7
```

- `00 20 29` = Focusrite/Novation manufacturer ID
- `02 10`    = Launchpad Pro MK1 product prefix

## Ports

The device exposes two USB MIDI ports:

- **Launchpad Pro Standalone Port** — used for Programmer-mode SysEx and pad input. This is what we open.
- **Launchpad Pro Live Port** — used by Ableton Live's "Launchpad Pro" control surface. We ignore it.

Layout selection (2Ch) is documented as "Standalone only" — sending it to
the Live Port is a no-op.

## Commands implemented

| cmd | hex  | name                       | payload                                  | max repeats |
|-----|------|----------------------------|------------------------------------------|-------------|
| `0A`| 0Ah  | Light LED (palette)        | `<pad> <color>`                          | 97          |
| `0B`| 0Bh  | Light LED (RGB)            | `<pad> <r> <g> <b>` (r,g,b ∈ 0..63)      | 78          |
| `0E`| 0Eh  | Light all LEDs (palette)   | `<color>`                                | —           |
| `0F`| 0Fh  | Light grid RGB             | `<grid_type> <r> <g> <b> [<r><g><b>...]` | 100         |
| `2C`| 2Ch  | Select layout              | `<layout>` (Programmer = 03h)            | —           |

`0Fh` `grid_type`: `00` = 10×10 (including side LEDs), `01` = 8×8 grid only.

## Pad numbering (Programmer layout, 8×8 grid)

```
top-left  81 .. 88  top-right
          71 .. 78
          61 .. 68
          51 .. 58
          41 .. 48
          31 .. 38
          21 .. 28
bot-left  11 .. 18  bot-right
```

"+1 = right, +10 = up." With top-origin coordinates (row 0 = top, col 0 = left):

```
pad = (8 - row) * 10 + (col + 1)
```

## The 0Fh bottom-up order

The 0Fh command takes **no pad indices** — it's a sequential dump. The
device walks the grid **bottom row first, left-to-right, then up**. Since
our coordinate system uses row 0 = top, the device serializer must flip the
grid vertically (`np.flipud`) before serializing.

This flip happens in exactly one place:
`src/launch_lights/device/launchpad_pro.py::LaunchpadProOut._execute_full_frame`.

`tests/test_full_frame_ordering.py` enforces this: a top-left red pixel must
land near the **end** of the SysEx payload because it's in the last row sent.

## Pad input (Programmer layout)

Square pad presses emit Note On on channel 1 (status `90h`). Velocity 1..127
on press, 0 (or Note Off) on release. Same note numbers as the LED commands.

We don't currently consume input — pads are display-only — but the wiring
is straightforward when needed.

## Color range

- RGB: each channel is 6-bit (0..63). The remaining 2 bits per channel are
  the cost of fitting all three channels into a 7-bit SysEx data byte.
- Palette: 128 entries (0..127). We approximate the firmware's palette in
  `src/launch_lights/video/palette.py` — for pixel-perfect color, measure
  the device and replace the table.

---

## MK3

Reference: Launchpad Pro [MK3] Programmer's Reference Manual (Novation).

### SysEx framing

```
F0  00 20 29 02 0E  <cmd>  <payload...>  F7
```

- `00 20 29` = Focusrite/Novation manufacturer ID
- `02 0E`    = Launchpad Pro MK3 product prefix (the model byte changes
  from `0x10` on the MK1)

### Ports

The MK3 exposes three USB MIDI ports:

- **`LPProMK3 MIDI`** — used for Programmer-mode SysEx and pad input.
  This is the one we open.
- **`LPProMK3 DIN`** — bridge to the 5-pin DIN connector. We skip it.
- **`LPProMK3 DAW`** — Ableton Live-style control surface. We skip it.

`device/midi_io.py::find_launchpad_pro_mk3_output()` explicitly excludes
`DAW` and `DIN` matches.

### Commands implemented

| cmd  | hex | name           | payload                                                       |
|------|-----|----------------|---------------------------------------------------------------|
| `03` | 03h | Lighting       | `[<type> <pad> <data...>]+` — repeat per pad                  |
| `0E` | 0Eh | Mode select    | `<00 = Live | 01 = Programmer>`                               |
| `10` | 10h | DAW mode       | `<00 = off | 01 = on>` (we don't emit this, but it exists)    |

Lighting `type` values:

- `00` = palette — `data = <color>` (1 byte, 0..127)
- `01` = flash   — `data = <color_b> <color_a>`
- `02` = pulse   — `data = <color>`
- `03` = RGB     — `data = <r> <g> <b>` (each 0..127)

There is **no** 0Fh-style bulk grid dump on the MK3. A full-frame paint
emits one `03h` lighting message with 64 RGB records — fits comfortably
in a single SysEx (≈ 326 bytes).

There is **no** "all LEDs off" shortcut either. Blackout iterates
palette-0 over all 64 pads in one `03h` message.

### Pad numbering

Same Programmer-layout note numbers as the MK1 — `pad_for(row, col) =
(8 - row) * 10 + (col + 1)`, top-left = 81, bottom-right = 18. The MK3
backend reuses MK1's `pad_for`.

### Color range

The MK3 takes **7-bit** RGB channels (0..127). The engine still works in
6-bit; the MK3 wire layer upscales via `v << 1` inside
`launchpad_pro_mk3._to7`. Top of range becomes 126 instead of 127 —
perceptually identical, and avoids per-pixel multiplication in the hot
path.

### Custom firmware caveat

The MK3 SysEx commands above are silently ignored on non-stock firmware
(some community CFW projects rework or remove Programmer Mode). If
`launch-lights blackout` runs cleanly but nothing changes on the device,
check the device's Setup menu — if the **Programmer** mode pad is
missing, the firmware is custom. Reflash via Novation Components to
restore standard behaviour.
