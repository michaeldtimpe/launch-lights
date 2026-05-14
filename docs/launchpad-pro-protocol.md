# Launchpad Pro (2015 / MK1) SysEx protocol

This is the subset of the Programmer's Reference Guide that `launch-lights`
implements. Bytes verified against:

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
