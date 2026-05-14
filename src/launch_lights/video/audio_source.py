"""Microphone capture and per-frame audio analysis.

The audio thread fills a 1024-sample buffer (`_latest`); `read_frame` pulls
it, computes an 8-band FFT spectrum, peak-decayed band magnitudes, an RMS
loudness, and a transient flag (beat detection on bass). Those analyses go
into a `MusicState` which the `Show` director turns into a Frame.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from launch_lights.engine.frame import Cell, Frame, OFF, RGB
from launch_lights.video.audio_show import MusicState, Show, N_BANDS


SAMPLE_RATE = 44100
BLOCK_SIZE = 1024
BAND_EDGES_HZ = np.geomspace(40.0, 16000.0, N_BANDS + 1)

BAR_DECAY = 0.85
MAX_DECAY = 0.9985
# Stuck-auto-gain rescue: if the loudest band stays below this fraction of
# `_max_seen` for STUCK_RESCUE_S seconds, force the running max down faster.
STUCK_RESCUE_RATIO = 0.25
STUCK_RESCUE_S = 2.0
STUCK_RESCUE_DECAY = 0.99

# Beat-detection tuning. 250 ms cooldown caps reported BPM at 240 BPM
# (= no music outside of Extratone); BPM_HARD_CAP further clamps the reported
# value if the median says something silly.
BASS_EMA_ALPHA = 0.05
BEAT_THRESHOLD = 1.35
BEAT_COOLDOWN_S = 0.25
BEAT_BPM_HISTORY = 8
BPM_HARD_CAP = 200.0


class AudioSource:
    def __init__(self, gain: float = 1.0, device: Optional[int] = None, seed: int | None = None) -> None:
        try:
            import sounddevice as sd
        except ImportError as e:
            raise RuntimeError(
                "sounddevice is required for --source audio. "
                "Install with: .venv/bin/pip install sounddevice"
            ) from e

        self._gain = float(gain)
        self._lock = threading.Lock()
        self._latest = np.zeros(BLOCK_SIZE, dtype=np.float32)
        self._held = np.zeros(N_BANDS, dtype=np.float32)
        self._max_seen = 1e-6
        self._window = np.hanning(BLOCK_SIZE).astype(np.float32)

        freqs = np.fft.rfftfreq(BLOCK_SIZE, d=1.0 / SAMPLE_RATE)
        self._band_idx: list[np.ndarray] = []
        for i in range(N_BANDS):
            lo, hi = BAND_EDGES_HZ[i], BAND_EDGES_HZ[i + 1]
            self._band_idx.append(np.flatnonzero((freqs >= lo) & (freqs < hi)))

        # Beat state
        self._bass_ema = 0.0
        self._last_beat_t = -1.0
        self._beat_count = 0
        self._beat_times: list[float] = []
        self._frames_since_beat = 0
        self._prev_bands = np.zeros(N_BANDS, dtype=np.float32)
        self._stuck_since: Optional[float] = None

        # Tunable from Show via set_decay_rate.
        self._bar_decay = BAR_DECAY

        # Cache of the most recent rendered framebuffer for the panel preview.
        # 8x8 uint8 RGB (0..63 each channel).
        self._last_frame_rgb: np.ndarray = np.zeros((8, 8, 3), dtype=np.uint8)

        self._show = Show(seed=seed)
        self._intensity = 1.0
        self._focus: Optional[str] = None  # "bass" | "melody" | "harmony" | None
        self._tempo_override: Optional[float] = None  # BPM or None for auto

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK_SIZE,
            dtype="float32", device=device, callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata, frames, time_info, status) -> None:
        with self._lock:
            self._latest[:] = indata[:, 0]

    def _analyze(self, elapsed: float) -> MusicState:
        with self._lock:
            samples = self._latest.copy()

        spectrum = np.abs(np.fft.rfft(samples * self._window))
        bands_raw = np.empty(N_BANDS, dtype=np.float32)
        for i, idx in enumerate(self._band_idx):
            bands_raw[i] = spectrum[idx].max() if idx.size else 0.0

        # Apply focus weighting before normalisation.
        if self._focus == "bass":
            bands_raw = bands_raw * np.array([1.5, 1.5, 1.4, 0.8, 0.6, 0.5, 0.4, 0.4], dtype=np.float32)
        elif self._focus == "melody":
            bands_raw = bands_raw * np.array([0.5, 0.6, 0.9, 1.4, 1.5, 1.3, 0.8, 0.6], dtype=np.float32)
        elif self._focus == "harmony":
            bands_raw = bands_raw * np.array([0.4, 0.5, 0.6, 0.8, 1.0, 1.3, 1.5, 1.5], dtype=np.float32)

        # Auto-gain: slow-decaying running max, with a stuck-rescue path.
        peak = float(bands_raw.max())
        self._max_seen = max(self._max_seen * MAX_DECAY, peak)
        if peak < STUCK_RESCUE_RATIO * self._max_seen:
            if self._stuck_since is None:
                self._stuck_since = elapsed
            elif elapsed - self._stuck_since > STUCK_RESCUE_S:
                self._max_seen *= STUCK_RESCUE_DECAY
        else:
            self._stuck_since = None
        bands = np.clip(bands_raw / self._max_seen * self._gain * self._intensity, 0.0, 1.0)
        self._held = np.maximum(bands, self._held * self._bar_decay)

        rms = float(np.sqrt(np.mean(samples ** 2)))
        # Scale RMS to a useful 0..1 visual range — empirical for typical mic
        rms_n = min(1.0, rms * 30.0 * self._gain)

        bass = float(np.mean(self._held[0:3]))
        treble = float(np.mean(self._held[5:8]))

        # Beat detection on instantaneous bass energy
        bass_now = float(np.mean(bands[0:3]))
        self._bass_ema = (1 - BASS_EMA_ALPHA) * self._bass_ema + BASS_EMA_ALPHA * bass_now
        is_beat = False
        if (
            bass_now > BEAT_THRESHOLD * self._bass_ema
            and bass_now > 0.15
            and elapsed - self._last_beat_t > BEAT_COOLDOWN_S
        ):
            is_beat = True
            self._last_beat_t = elapsed
            self._beat_count += 1
            self._beat_times.append(elapsed)
            if len(self._beat_times) > BEAT_BPM_HISTORY:
                self._beat_times.pop(0)
            self._frames_since_beat = 0
        else:
            self._frames_since_beat += 1

        bpm = 0.0
        beat_confidence = 0.0
        if len(self._beat_times) >= 4:
            intervals = np.diff(self._beat_times)
            med = float(np.median(intervals))
            if med > 0:
                bpm = min(BPM_HARD_CAP, 60.0 / med)
            # Confidence from interval consistency: low std / mean = high confidence.
            mean = float(np.mean(intervals))
            std = float(np.std(intervals))
            if mean > 0:
                beat_confidence = max(0.0, min(1.0, 1.0 - std / mean))
        if self._tempo_override is not None:
            bpm = self._tempo_override
            beat_confidence = 1.0  # locked tempo == fully confident

        # Beat phase: continuous 0..1 progress to the *expected* next beat.
        if bpm > 0 and self._last_beat_t >= 0:
            expected_interval = 60.0 / bpm
            beat_phase = min(1.0, (elapsed - self._last_beat_t) / max(0.01, expected_interval))
        else:
            beat_phase = 0.0

        # Onset strength: log-scaled bass surge ratio, ~0..1.
        onset_strength = 0.0
        if self._bass_ema > 0.001:
            ratio = bass_now / self._bass_ema
            onset_strength = float(min(1.0, max(0.0, np.log1p(ratio) / np.log1p(5.0))))

        # Spectral flux: sum of positive frame-to-frame band increases.
        flux = float(np.sum(np.maximum(0.0, bands - self._prev_bands)))
        self._prev_bands = bands.copy()

        # Cache for the control panel snapshot reader.
        self._last_rms = rms_n
        self._last_bass = bass
        self._last_treble = treble
        self._last_bpm = bpm
        self._last_is_beat = is_beat
        self._last_beat_confidence = beat_confidence
        self._last_flux = flux

        return MusicState(
            bands=bands, held=self._held.copy(),
            rms=rms_n, bass=bass, treble=treble,
            is_beat=is_beat, beat_count=self._beat_count, bpm=bpm,
            beat_phase=beat_phase, onset_strength=onset_strength,
            spectral_flux=flux, beat_confidence=beat_confidence,
            frames_since_beat=self._frames_since_beat, elapsed=elapsed,
        )

    def analyze(self, elapsed: float) -> "MusicState":
        """Public entrypoint — webcam-show calls this to update audio state."""
        return self._analyze(elapsed)

    def write_grid_cache(self, cells) -> None:
        """For sources that bypass read_frame: copy a CellMap into the cache."""
        grid = self._last_frame_rgb
        grid[:] = 0
        for (r, c), rgb in cells.items():
            grid[r, c, 0] = rgb.r
            grid[r, c, 1] = rgb.g
            grid[r, c, 2] = rgb.b

    def read_frame(self, elapsed: float) -> Frame:
        state = self._analyze(elapsed)
        cells: dict[Cell, RGB] = {(r, c): OFF for r in range(8) for c in range(8)}
        cells.update(self._show.paint(state))
        # Cache the rendered grid for the panel preview.
        grid = self._last_frame_rgb
        for (r, c), rgb in cells.items():
            grid[r, c, 0] = rgb.r
            grid[r, c, 1] = rgb.g
            grid[r, c, 2] = rgb.b
        return Frame(cells=cells)

    def latest_frame_grid(self) -> np.ndarray:
        """Returns the most recent (8,8,3) framebuffer for preview."""
        return self._last_frame_rgb

    # --- live-control hooks --------------------------------------------------

    @property
    def show(self) -> Show:
        return self._show

    def set_gain(self, value: float) -> None:
        self._gain = max(0.1, min(5.0, float(value)))

    def set_intensity(self, value: float) -> None:
        self._intensity = max(0.1, min(3.0, float(value)))

    def set_focus(self, value: Optional[str]) -> None:
        self._focus = value if value in ("bass", "melody", "harmony") else None

    def set_tempo_override(self, value: Optional[float]) -> None:
        self._tempo_override = float(value) if value else None

    def set_decay_rate(self, value: float) -> None:
        self._bar_decay = max(0.1, min(0.99, float(value)))

    def snapshot_state(self) -> dict:
        return {
            "gain": self._gain,
            "intensity": self._intensity,
            "focus": self._focus,
            "tempo_override": self._tempo_override,
            "bass_ema": self._bass_ema,
            "beat_count": self._beat_count,
            "frames_since_beat": self._frames_since_beat,
        }

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
