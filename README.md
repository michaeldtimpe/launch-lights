# launch-lights

Turn a **Novation Launchpad Pro** into a reactive 8×8 RGB display.
Supports the **2015 / MK1** and the **MK3** — both auto-detected at
launch. Drives the device at 30 fps over SysEx with sources that range
from a static test pattern up to a microphone-driven, beat-aware visual
show with a browser-based control panel.

## What it can do

| source         | what it shows                                                    |
|----------------|------------------------------------------------------------------|
| `test`         | built-in patterns (orientation, bars, gradient, sweep, etc.)     |
| `webcam`       | webcam frames, downsampled to 8×8                                |
| `file`         | video file (loops on EOF)                                        |
| `audio`        | microphone-driven audio show — 22 scenes, beat detection, effect stack |
| `webcam-show`  | webcam routed through the audio-mode effect stack                |

The audio and webcam-show sources expose a **web control panel** (HTTP +
WebSocket) so you can tune the show live from a phone or laptop while the
device faces away.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
```

Dependencies: `mido`, `python-rtmidi`, `numpy`, `opencv-python`, `click`,
`rich`, `sounddevice`, `websockets`. The `[test]` extra adds `pytest`.

## Quickstart

```bash
# Find the device
.venv/bin/launch-lights list-ports

# Test patterns
.venv/bin/launch-lights test --pattern orientation
.venv/bin/launch-lights test --pattern bars

# Webcam → 8×8
.venv/bin/launch-lights run --source webcam

# Video file → 8×8 (letterbox or crop)
.venv/bin/launch-lights run --source file --file ~/Movies/clip.mp4 --fit crop

# Audio show with web control
.venv/bin/launch-lights run --source audio --control-port 8095
# then open http://127.0.0.1:8095/

# Webcam through the audio-show effect stack
.venv/bin/launch-lights run --source webcam-show --control-port 8095

# Blackout + return device to Note layout
.venv/bin/launch-lights blackout
```

## Commands

`list-ports` — show MIDI in/out ports and auto-detect the Launchpad Pro
(MK1 Standalone Port, or MK3 `LPProMK3 MIDI`). The auto-detect line
prints which model was matched.

`test --pattern <name>` — display a built-in pattern. Patterns:
`bars`, `gradient`, `checker`, `flood` (with `--flood-color "#ff8000"`),
`sweep`, `orientation`.

`blackout` — every LED off; the device stays dark in Programmer Mode
(so Live Mode's session view doesn't repaint over it).

`run --source <webcam|file|test|audio|webcam-show>` — drive the display
from a source. Notable flags:

- `--fit crop|letterbox|stretch` (default `crop`) — how non-square video
  maps onto the 8×8 grid. `crop` keeps all 64 pads lit.
- `--fps 30` — display refresh rate. 60 fps risks saturating the
  LP Pro's internal SysEx buffer.
- `--gamma 2.2 --brightness 1.0` — perceptual correction and LED scaling.
- `--color-mode rgb|palette` — full RGB SysEx (default) or the LP Pro's
  built-in 128-color palette.
- `--dither/--no-dither` — Floyd-Steinberg error diffusion on webcam/file
  sources. Off by default.
- `--stats` — live FPS / plan-type panel in the terminal.
- `--mic-gain 1.0` — sensitivity for `--source audio` (also live in panel).
- `--control-port 8095` — serve the web control panel on this port.
  Audio and webcam-show only.

## Web control panel

Available when `--control-port` is set with `--source audio` or
`--source webcam-show`. The panel exposes:

- a **live 8×8 preview** mirror of what the device shows (useful when
  the device faces away during a performance)
- **audio analysis stats**: scene name, beat counter, BPM, beat
  confidence, and intensity-coloured VU meters for RMS, bass, treble,
  and spectral flux
- **scene + palette** card — `auto` (rotates every 20-40 s), `none`, or
  any of the 20 visualisations. A **blend** toggle next to those buttons
  changes click behaviour: with blend off a click sets the primary scene
  (solid highlight); with blend on a click toggles a scene as a mutator
  overlay (outline highlight). Below the chips, a divider, then the 16
  palettes — click any to remap output by brightness, `none` to clear.
- **color & tone** — hue rotate, contrast (sigmoid), gamma, brightness,
  invert/complementary toggles
- **motion** — trail decay, bar decay, mirror modes (off / h / v / quad /
  kaleidoscope)
- **audio levels** — sensitivity (mic gain), intensity (visual scaling)
- **tempo** — manual BPM override + 0.5× / 1× / 2× beat multiplier
- **focus** — bias the analysis toward bass, melody, or harmony bands
- **scrolling text** — type a string, pick 5×7 or 3×5 font, scroll
  direction, speed
- **video file** — paste an absolute path to a video, pick a fit mode
  (crop / letterbox / stretch), and click **load**; the video is routed
  through the same effect stack as `--source webcam-show`, so palette /
  hue / mirror / contrast all apply live during playback. **stop** clears
  it and the audio show resumes.
- **edit layout** (top-right header) toggles a drag-and-resize mode
  (Gridstack-driven). Cards become draggable on a 12-column grid; the
  layout auto-saves to `localStorage` per page version. **export**
  copies the current layout JSON to your clipboard; **reset** wipes the
  saved layout and reloads to baked-in defaults.
- a top-bar **`clear`** kill switch and a **`reset`** on every adjustable
  card

See [docs/audio-show.md](docs/audio-show.md) for the full reference —
audio pipeline, scene list, effect stack order, and WebSocket protocol.

## How it works

```
camera/file/webcam (BGR) ─┐
                          ├─ effect stack ─┐
mic (PCM) ─ analyzer ─┐   │                │
                      ├─ Show.paint ───────┤
                      │                    ▼
                     scenes              Frame
                                          │
                                          ▼
                                  Renderer.plan(frame)
                                          │
                          NoOp | RGBDiff | RGBFullFrame | PaletteDiff
                                          │
                                          ▼
                                LaunchpadProOut.execute()
                                          │
                                          ▼
                                 one SysEx per frame
```

The big design choices:

- **Top-origin everywhere.** `Cell = (row, col)` with row 0 = top,
  col 0 = left. The Launchpad's 0Fh bulk-RGB command needs bottom-up
  data, so the vertical flip happens in *exactly one place* —
  `device.launchpad_pro.LaunchpadProOut._execute_full_frame`. Don't
  scatter flips elsewhere.
- **Planning is pure compute.** `engine.renderer.Renderer.plan(frame)`
  returns a `RenderPlan` (one of `RGBDiff`, `RGBFullFrame`,
  `PaletteDiff`, `NoOp`). All MIDI I/O lives in the device layer's
  `execute(plan)`.
- **Preallocated SysEx buffer.** The 0Fh message is fixed-size. One
  `bytearray` per device instance, mutated in place every frame — no
  Python list churn in the hot path.
- **Effect pipeline is post-paint.** Whatever a scene emits is
  transformed through a fixed-order chain
  (`trail → palette_remap → hue_rotate → invert → complementary →
  mirror → contrast → brightness → gamma`) before the renderer sees it.
  Same chain is reused for `--source webcam-show`.
- **30 fps default.** 60 fps risks saturating the LP Pro's internal
  SysEx buffer. Override with `--fps`.

## Docs

- [docs/launchpad-pro-protocol.md](docs/launchpad-pro-protocol.md) —
  the verified SysEx spec with sources.
- [docs/manual-verification.md](docs/manual-verification.md) — hardware
  smoke-test checklist for first power-on and each release.
- [docs/audio-show.md](docs/audio-show.md) — audio analysis pipeline,
  scenes, effects, and the WebSocket control protocol.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The byte-level protocol tests run without hardware. They lock the wire
format against the reference guide.
