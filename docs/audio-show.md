# Audio show — pipeline, scenes, effects, control panel

`--source audio` turns the Launchpad into a microphone-driven, beat-aware
visualizer. `--source webcam-show` reuses the same effect stack on a
webcam feed instead of generating scenes from audio. Both expose a
WebSocket-driven control panel when `--control-port` is set.

## Audio analysis pipeline

Per render tick (30 fps default):

1. **Capture.** A `sounddevice` callback fills a 1024-sample float32
   buffer at 44.1 kHz on a background thread.
2. **FFT band magnitudes.** Hanning-windowed `rfft`. Eight log-spaced
   bands between **40 Hz and 16 kHz** — band magnitude is the **max**
   of the bins inside the band (peak-per-band, not mean — preserves
   transients in the wider high bands).
3. **Focus weighting.** If `focus = bass / melody / harmony`, multiply
   the bands by a fixed weight profile before normalisation.
4. **Auto-gain.** A slow-decaying running max (`MAX_DECAY = 0.9985`)
   normalises the peak band to 1.0. **Stuck-rescue**: if the peak stays
   below 25 % of the running max for > 2 s (e.g. after a clap), an
   extra `× 0.99` decay accelerates recovery.
5. **Peak decay.** `held = max(new, held * bar_decay)` (`bar_decay`
   defaults to 0.85; tunable from the panel). Stops the bars from
   strobing.
6. **Beat detection.** EMA over the instantaneous bass band; a beat
   fires when `bass_now > 1.35 × ema` with a 250 ms cooldown (caps
   reported BPM at 240). Median of the last 8 inter-beat intervals is
   the BPM estimate, hard-capped at 200. Confidence comes from
   `1 - std/mean` of those intervals.
7. **Higher-level features**:
   - `beat_phase` — continuous 0..1 progress to the next expected beat.
   - `onset_strength` — log-scaled `bass_now / ema` for proportional
     reactions (weak hat vs. massive kick).
   - `spectral_flux` — sum of positive frame-to-frame band increases.
     Good signal for director scene-cuts and palette flips.
   - `beat_confidence` — interval-variance based, 0..1.

All of the above lands in a `MusicState` dataclass passed to every scene
and effect.

## Scenes (20 rotating)

Each scene maps `MusicState → sparse {Cell: RGB}`. Auto-rotate cycles
through these every 20–40 s, optionally with a secondary scene blended
and 0–2 random effects layered.

| name           | what it looks like                                          |
|----------------|-------------------------------------------------------------|
| `spectrum`     | bottom-up VU bars, green→yellow→red per row                |
| `inverted`     | bars hang from top, hue per column                          |
| `mirror`       | bars grow up *and* down from the middle row                 |
| `ring`         | square ring expanding from centre, size = RMS              |
| `blob`         | solid centred square pulses with bass                       |
| `rain`         | random pads spawn at top and fall; density tied to bass     |
| `sparkle`      | random pads pop on each beat and fade                       |
| `neg_sparkle`  | full grid lit; random pads go **dark** on beats             |
| `flood`        | whole grid one colour; brightness = RMS, hue jumps on beat |
| `diag`         | diagonal stripe scrolling; speed scales with BPM            |
| `rings`        | concentric rings expand from centre on beat                 |
| `col_sweep`    | bright column scans left→right, height = bass               |
| `row_sweep`    | bright row scrolls top→bottom                               |
| `checker`      | 2-colour checkerboard, phase flips on beat                  |
| `plasma`       | animated sinusoidal field, hue mapped                       |
| `border`       | pad with trail runs along perimeter, speed = BPM           |
| `cross`        | X across grid, brightness = RMS                             |
| `heart`        | heart shape that pulses with bass                           |
| `edge`         | top + bottom rows show band intensities                     |
| `quads`        | four quadrants, each filled by a band range                 |

## Special scenes (panel-only)

- `text` — scrolling banner, set from the panel's text card
  (5×7 / 3×5 fonts, left/right scroll, speed slider).
- `blackout` — empty grid (used internally by the `clear` button and
  scene = `none`).
- `shape` — single static bitmap pulse. The shape/emoji **picker UI was
  removed** from the dashboard, but the underlying scene and its
  `shape` WebSocket message still work for programmatic clients.

## Effect pipeline (fixed order)

After the scene + mutators have produced a `CellMap`:

```
trail_decay  → palette_remap → hue_rotate → invert → complementary
            → mirror → contrast → brightness → gamma
```

- **`trail_decay`** (0..0.95) — `max(current, prev * decay)` per pad.
  Adds "ghost" trails to any scene.
- **`palette_remap`** — if a palette is active, replace each lit pad
  with `palette.sample((r+g+b)/(3*63))` (brightness-keyed lookup).
- **`hue_rotate`** (-180°..+180°) — HSV rotation applied per pad.
- **`invert`** / **`complementary`** — toggles. Invert swaps lit/dark
  pads to a fixed bright fg; complementary is 180° hue, brightness
  preserved.
- **`mirror`** — `off / horizontal / vertical / quad / radial`. Radial
  is the kaleidoscope: take the top-left 4×4, mirror into all four
  quadrants.
- **`contrast`** (0.5..2.5, sigmoid) — `(x-0.5)*contrast + 0.5` clamped.
- **`brightness`** (0.1..1.5) — linear scalar.
- **`gamma`** (1.0..3.0) — perceptual correction LUT, regenerated when
  the value changes.

## Palettes

17 palettes total, all pre-rendered as 256-entry RGB LUTs at startup
(~9 kB total).

`neon`, `fire`, `inferno`, `ocean`, `forest`, `sunset`, `ice`, `candy`,
`lava`, `spring`, `mono`, `mono_red`, `mono_blu`, `violet`, `synthwave`,
`cyberpunk`, `toxic`.

Palettes are sampled by brightness — they don't pick at random. A scene
that already produces hue-varying output still produces hue-varying
output; the palette just remaps each output colour by its luminance.

## Web control panel

When `--control-port PORT` is set with `--source audio` or
`--source webcam-show`, the server listens on `127.0.0.1:PORT` and
serves three things:

- `GET /` — the control panel HTML (single-page, no build step)
- `GET /static/InterVariable.ttf` — bundled font
- `WS /ws` — live state push + command channel

### WebSocket protocol

**Server → client** at ~10 Hz:

```json
{
  "type": "state",
  "source": "audio",
  "scene": "spectrum",
  "rms": 0.42, "bass": 0.51, "treble": 0.12,
  "bpm": 124.0, "is_beat": false, "beat_count": 87,
  "beat_confidence": 0.86, "spectral_flux": 0.034,
  "grid": [r0,g0,b0, r1,g1,b1, ...],  // 192 ints, 8×8×3, channels 0..63
  "video": { "loaded": false, "path": null, "fit": "crop" },
  "page_version": "abc123def456"  // changes when the HTML changes
}
```

The client uses `page_version` for the stale-tab guard: if the value
differs from the `PAGE_VERSION` constant embedded in the HTML at
render time, the client calls `location.reload()`. The layout
`localStorage` key includes `page_version` so a server upgrade also
invalidates any saved layout from the previous build.

**Client → server** (any JSON message accepted; unknown fields
ignored):

| `type`              | `value`                                  | effect                                   |
|---------------------|------------------------------------------|------------------------------------------|
| `scene`             | scene name, `"none"`, or `null`          | lock primary; `null` = auto-rotate       |
| `mutator`           | list of scene names                      | blended over the primary via max         |
| `palette`           | palette name or `null`                   | remap output by brightness               |
| `hue`               | -180..180                                | global hue rotation                      |
| `contrast`          | 0.5..2.5                                 | sigmoid contrast                         |
| `gamma`             | 1.0..3.0                                 | output gamma correction                  |
| `brightness`        | 0.1..1.5                                 | final LED scaling                        |
| `trail`             | 0..0.95                                  | trail decay rate                         |
| `decay_rate`        | 0.1..0.99                                | bar-decay (`held` smoothing)             |
| `mirror`            | `off / horizontal / vertical / quad / radial` | global mirroring                    |
| `invert`            | bool                                     | invert toggle                            |
| `complementary`     | bool                                     | complementary-hue toggle                 |
| `blackout`          | bool                                     | full kill switch (skips all effects)     |
| `beat_multiplier`   | 0.5 / 1.0 / 2.0                          | multiplier on BPM seen by viz code       |
| `sensitivity`       | 0.1..3.0                                 | mic gain                                 |
| `intensity`         | 0.1..3.0                                 | post-gain visual scaling                 |
| `tempo`             | float or `null`                          | BPM override; `null` = auto-detect       |
| `focus`             | `bass / melody / harmony / null`         | band weighting profile                   |
| `text`              | string                                   | scrolling-text content                   |
| `text_font`         | `5x7 / 3x5`                              | font for scrolling text                  |
| `text_speed`        | 1..40 (pixels/sec)                       | scroll speed                             |
| `text_dir`          | `left / right`                           | scroll direction                         |
| `shape`             | shape/emoji name                         | bitmap to display in the `shape` scene (no panel UI; programmatic only) |
| `video`             | `{ action: "load", path: str }` or `{ action: "stop" }` | mount a server-side `FileSource` and route its frames through `paint_passthrough` |
| `video_fit`         | `crop / letterbox / stretch`             | fit mode applied to the loaded video before downsampling |

## Architecture notes

- `audio_source.AudioSource` owns mic capture + the analyzer + the
  `Show` director. Its `read_frame(elapsed)` is what the scheduler
  calls each tick.
- `audio_show.Show` is the director: picks a primary scene, blends
  optional secondaries + mutators, then runs the fixed effect pipeline
  above. Live setters (`set_palette`, `set_hue`, etc.) are atomic — no
  locks needed.
- `audio_show.Show.paint_passthrough(cells, state)` is used by
  `--source webcam-show`: it skips scene generation and runs an input
  `CellMap` through the same post-stack.
- `web.server.ControlServer` runs the HTTP+WS pair on a background
  thread with its own asyncio loop. The `_apply` dispatch routes WS
  commands into either `AudioSource` setters (analysis tunables) or
  `Show` setters (effect/scene tunables).
