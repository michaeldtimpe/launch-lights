# Manual verification checklist

Run these on real hardware after first install or any change that touches the
device layer. The byte-level pytest suite catches protocol regressions; this
list catches everything else — port detection, orientation, color fidelity,
timing.

## Setup

```bash
.venv/bin/pip install -e ".[test]"
```

Plug the Launchpad Pro into USB. MK1 enumerates as two MIDI ports
(`Standalone` and `Live`); MK3 enumerates as three (`LPProMK3 MIDI`,
`LPProMK3 DIN`, `LPProMK3 DAW`). The MK3 needs stock Novation firmware —
custom firmware that hides Programmer Mode from the Setup menu silently
ignores all the lighting SysEx.

## Checklist

### 1. Port detection

```bash
.venv/bin/launch-lights list-ports
```

Expect:

- **MK1**: two ports — one ends in `Standalone`, one in `Live`. The
  auto-detect line should pick the Standalone one and report `(mk1)`.
- **MK3**: three ports — `LPProMK3 MIDI`, `LPProMK3 DIN`, `LPProMK3 DAW`.
  Auto-detect picks the `MIDI` port and reports `(mk3)`. DAW and DIN
  must NOT be selected (the lighting protocol doesn't work there).

### 2. Orientation

```bash
.venv/bin/launch-lights test --pattern orientation
```

Expect, looking at the device with the Novation logo at the top:

- Top-left pad: bright red
- Top-right pad: bright green
- Bottom-left pad: bright blue
- Bottom-right pad: bright white
- A red→green gradient along the top edge
- A red→blue gradient along the left edge

If anything is rotated, flipped, or transposed, the bug is in
`device.launchpad_pro.pad_for` (for diff/palette modes) or
`device.launchpad_pro._execute_full_frame` (for full-frame mode). The
`test_full_frame_ordering.py` test should also be failing.

### 3. Color fidelity

```bash
.venv/bin/launch-lights test --pattern bars
```

Expect 8 vertical bars: red, orange, yellow, green, cyan, blue, magenta,
white. All bars should be roughly the same brightness.

### 4. Banding A/B

```bash
.venv/bin/launch-lights test --pattern gradient
```

Then:

```bash
.venv/bin/launch-lights run --source test --pattern gradient --no-dither --fps 30
```

vs.

```bash
.venv/bin/launch-lights run --source test --pattern gradient --dither --fps 30
```

The dithered version should look visibly smoother across the gradient. If
both look identical, dithering may not be wired in — check
`cli.py`'s `tick` callback.

### 5. Live video

```bash
.venv/bin/launch-lights run --source webcam --fit crop --fps 30 --stats
```

Wave a hand or hold up a bright object. Latency should feel sub-100 ms.

In the `--stats` panel:
- `render fps` should hover near 30
- `source fps` should be ≥ 25 (webcams vary)
- `plan: RGBFullFrame` should be the dominant counter
- `skips` should stay at 0 or grow slowly

### 6. File playback

```bash
.venv/bin/launch-lights run --source file --file path/to/video.mp4 --fit letterbox
```

Expect: video plays, loops cleanly on EOF, letterboxing leaves the top and
bottom rows dark for widescreen content.

### 7. Clean shutdown

In any `run` or `test` session, press Ctrl-C. Expect:

- Every LED goes off.
- The device returns to its default user-facing mode (MK1: Note layout;
  MK3: Live mode / Session view).
- Stats line prints with `ticks`, `skips`, `max_drift`, and plan counts.

Press Ctrl-C twice in quick succession to verify idempotent shutdown —
both `LaunchpadProOut.close()` and `LaunchpadProMK3Out.close()` should
tolerate it without raising.

### 8. Blackout recovery

If the device gets into a weird state (interrupted process, etc.):

```bash
.venv/bin/launch-lights blackout
```

Every pad should go off and the device should **stay dark** in
Programmer Mode (it does NOT return to Live mode). On MK3 this matters
because Live mode's session view would otherwise repaint the grid the
instant we exit Programmer Mode. Run any `test` or `run` command to
bring the device back; both restore the default user-facing mode on
exit.

### 9. Audio show

```bash
.venv/bin/launch-lights run --source audio --stats
```

Play music with a clear kick drum. Expect:

- bars/visuals respond to amplitude immediately
- `beat_count` increments roughly on each downbeat
- `bpm` settles within a few seconds at a value within ±5 of the track's tempo
- `plan: RGBFullFrame` is the dominant counter
- `skips` stays at 0 or grows slowly

Ctrl-C exits cleanly and blacks out the device.

### 10. Web control panel

```bash
.venv/bin/launch-lights run --source audio --control-port 8095
```

Open <http://127.0.0.1:8095/> in a browser. Expect:

- the status pill in the header switches from `offline` (red) to `live`
  (green) within a second
- the **preview** card mirrors what the device displays
- VU bars (rms / bass / treble / flux) shift from green to red as
  loudness increases
- clicking any **scene chip** in the scene+palette card locks the show
  to that scene (solid highlight); `auto` resumes rotation; `none`
  empties the primary
- click the **blend** toggle (right-aligned with reset) — it highlights
  filled. Now clicking scene chips toggles them as mutator overlays
  (outline highlight, not solid). Click blend again to return to
  primary-select mode. Primary and mutator selections are independent.
- below the divider, clicking any **palette chip** remaps colors live;
  the `none` button next to reset clears the palette
- the **`clear`** button blacks out instantly; clicking again resumes
- `reset` on any card returns its controls to defaults

### 11. Webcam-show pipeline

```bash
.venv/bin/launch-lights run --source webcam-show --control-port 8095
```

Camera frames go through the effect stack. Expect:

- 8×8 downsampled webcam visible on the device
- panel still works: changing palette / hue / mirror / contrast / etc.
  immediately recolours the camera feed
- toggling `kaleido` (mirror = radial) gives a quadrant-mirrored
  webcam effect
- scene and mutator controls have no effect in this mode (they are
  scene-generation, which is bypassed)

### 12. Video file from the dashboard (audio source)

In an `--source audio --control-port 8095` session:

- paste an absolute path to a local video file into the **video** card's
  text input (e.g. `/Users/.../clip.mp4`)
- pick a fit mode (`crop` / `letterbox` / `stretch`)
- click **load**. Status changes to `playing: clip.mp4` and the device
  shows the downsampled video, looping at EOF.
- palette / hue / contrast / mirror / etc. apply live to the video
- click **stop** to release the file; the audio show resumes.

Bad paths fail silently in the UI — check the server terminal for a
`video load: failed to open …` warning if nothing appears on the device.

### 13. Scrolling text

In the panel (any audio-source run), type into the **scrolling text**
card → click **`show text`** → text scrolls across the device. Text
picks up palette, hue rotation, mirror, brightness, etc., from the
effect stack.

### 14. Layout edit + export

- click **edit layout** in the header — each card gets a dashed accent
  outline and resize handles appear
- drag any card to a new grid position, or drag its bottom-right corner
  to resize on the 12-column grid
- click **export** → the layout JSON is copied to your clipboard
- click **reset** → confirm → page reloads with baked-in defaults
- click **done editing** → drag affordances vanish; layout is locked in

## Known constraints

- **30 fps recommended.** 60 fps can saturate the LP Pro's SysEx buffer and
  cause stutters.
- **Gamma 2.2 is the default.** Without it the display crushes shadows.
- **Palette mode uses a synthetic 128-entry table.** Colors won't perfectly
  match the firmware's palette until the table in
  `src/launch_lights/video/palette.py` is calibrated against the device.
- **Source switching is launch-time.** Selecting `webcam-show` vs `audio`
  requires relaunching with the desired `--source`. The panel's source
  indicator reflects what's running, not a live switch.
- **`python-rtmidi` on Python 3.14 + macOS 26 can crash** on rapid
  process start (`MidiInCore::getCoreMidiClientSingleton` throws through
  an exception spec → `terminate`). Symptom: process aborts at startup,
  no scene rendered. Fix: just relaunch — once the previous CoreMIDI
  client has fully torn down (a couple seconds), it works.
