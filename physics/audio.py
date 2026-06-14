"""Pure-numpy DSP cores for audio-reactive and tempo-synced visuals.

No TouchDesigner dependency -- fast, testable, and runs unchanged inside TD's
bundled Python. The TD layer wraps these:

* ``touchdesigner/callbacks/audio_chop.py`` turns a live audio CHOP (the DJ
  feed) into ``bass``/``mid``/``high``/``level``/``beat`` channels + a log
  spectrum, all smoothed for animation.
* ``touchdesigner/callbacks/tempo_chop.py`` follows an incoming MIDI beat clock
  (24 PPQN) -- or free-runs on a manual BPM -- and emits ``beat``/``bar`` phase
  ramps, ``bpm`` and a ``pulse`` trigger so visuals lock to the DJ's tempo.

Everything here is deliberately allocation-light and vectorised so it can run
per audio block at 60 fps.
"""
from collections import deque

import numpy as np

# Perceptual band edges (Hz). Bass = kick/sub, mid = body/vocals, high = hats.
BANDS = (("bass", 20.0, 250.0), ("mid", 250.0, 4000.0), ("high", 4000.0, 16000.0))
BAND_NAMES = [b[0] for b in BANDS]


def _window(n):
    return np.hanning(n).astype(np.float32) if n > 1 else np.ones(max(n, 1), np.float32)


class AudioAnalyzer:
    """Block-based spectral analysis with asymmetric (attack/release) smoothing.

    Call :meth:`analyze` once per frame with the latest audio samples; it
    returns a dict of smoothed, roughly-normalised features in ~[0, 1]. Fast
    attack + slow release gives lively but un-jittery motion -- the standard
    envelope-follower trick for VJ reactivity.
    """

    def __init__(self, sample_rate=44100.0, attack=0.7, release=0.12, gain=6.0):
        self.sr = float(sample_rate)
        self.attack = float(attack)
        self.release = float(release)
        self.gain = float(gain)
        self._env = {k: 0.0 for k in BAND_NAMES + ["level"]}

    def _smooth(self, key, target):
        prev = self._env[key]
        coeff = self.attack if target > prev else self.release
        v = prev + coeff * (target - prev)
        self._env[key] = v
        return v

    def analyze(self, samples):
        x = np.ascontiguousarray(samples, dtype=np.float32).ravel()
        n = x.size
        if n == 0:
            return dict(self._env)
        level_raw = float(np.sqrt(np.mean(x * x)) * self.gain)
        mag = np.abs(np.fft.rfft(x * _window(n))).astype(np.float32)
        # Normalise FFT magnitude by block size so features are scale-stable.
        mag *= (2.0 / n)
        freqs = np.fft.rfftfreq(n, 1.0 / self.sr)
        out = {}
        for name, lo, hi in BANDS:
            m = (freqs >= lo) & (freqs < hi)
            energy = float(np.sqrt(np.mean(mag[m] ** 2))) if m.any() else 0.0
            out[name] = self._smooth(name, min(energy * self.gain, 4.0))
        out["level"] = self._smooth("level", min(level_raw, 4.0))
        return out

    def spectrum(self, samples, n_out=64, fmin=30.0, fmax=16000.0):
        """Log-spaced, log-scaled, peak-normalised spectrum for visualisation."""
        x = np.ascontiguousarray(samples, dtype=np.float32).ravel()
        n = x.size
        if n < 2:
            return np.zeros(n_out, dtype=np.float32)
        mag = np.abs(np.fft.rfft(x * _window(n))).astype(np.float32)
        freqs = np.fft.rfftfreq(n, 1.0 / self.sr)
        edges = np.logspace(np.log10(fmin), np.log10(max(fmax, fmin * 2.0)), n_out + 1)
        idx = np.clip(np.searchsorted(edges, freqs) - 1, 0, n_out - 1)
        bars = np.zeros(n_out, dtype=np.float32)
        np.maximum.at(bars, idx, mag)
        bars = np.log1p(bars * 4.0)
        peak = float(bars.max())
        if peak > 1e-6:
            bars /= peak
        return bars.astype(np.float32)


class BeatTracker:
    """Adaptive energy-based onset/beat detector with a refractory gap.

    Feed it a per-frame energy (e.g. the bass envelope). It flags a beat when
    energy spikes above a running local average by ``sensitivity`` and enough
    time has passed since the last beat, and estimates BPM from recent beat
    spacing.
    """

    def __init__(self, sensitivity=1.5, refractory=0.18, memory=43):
        self.sensitivity = float(sensitivity)
        self.refractory = float(refractory)
        self._hist = deque(maxlen=int(memory))
        self._cool = 0.0
        self._t = 0.0
        self._beats = deque(maxlen=8)
        self.bpm = 0.0

    def update(self, energy, dt):
        energy = float(energy)
        self._t += float(dt)
        self._cool = max(0.0, self._cool - float(dt))
        avg = (sum(self._hist) / len(self._hist)) if self._hist else 0.0
        beat = False
        if energy > avg * self.sensitivity and energy > 1e-4 and self._cool <= 0.0:
            beat = True
            self._cool = self.refractory
            self._beats.append(self._t)
            if len(self._beats) >= 2:
                intervals = np.diff(np.asarray(self._beats))
                med = float(np.median(intervals))
                if med > 0:
                    bpm = 60.0 / med
                    if 60.0 <= bpm <= 200.0:
                        self.bpm = bpm
        self._hist.append(energy)
        return beat


class TempoClock:
    """MIDI beat-clock follower (24 PPQN) with a free-running fallback.

    Feed realtime messages as they arrive (``on_pulse`` per 0xF8 clock tick,
    ``on_start``/``on_stop`` for transport). Query :meth:`phase` each frame for
    a continuous beat/bar position. If no clock has arrived recently it
    free-runs on ``manual_bpm`` so visuals always have a tempo to lock to.
    """

    PPQN = 24
    BEATS_PER_BAR = 4

    def __init__(self, default_bpm=120.0, smoothing=0.18, timeout=1.5):
        self.bpm = float(default_bpm)
        self.manual_bpm = float(default_bpm)
        self.smoothing = float(smoothing)
        self.timeout = float(timeout)
        self.locked = False
        self._pulse = 0
        self._beat_index = 0
        self._last_t = None
        self._beat0_t = 0.0

    def on_start(self, t):
        self._pulse = 0
        self._beat_index = 0
        self._beat0_t = float(t)
        self._last_t = float(t)
        self.locked = True

    def on_stop(self, t):
        self.locked = False

    def set_manual_bpm(self, bpm):
        self.manual_bpm = max(20.0, float(bpm))

    def on_pulse(self, t):
        t = float(t)
        if self._last_t is not None:
            dt = t - self._last_t
            if dt > 0:
                inst = 60.0 / (dt * self.PPQN)
                if 40.0 <= inst <= 240.0:
                    self.bpm += self.smoothing * (inst - self.bpm)
        self._last_t = t
        self.locked = True
        self._pulse += 1
        if self._pulse >= self.PPQN:
            self._pulse = 0
            self._beat_index += 1
            self._beat0_t = t

    def _free(self, t):
        bpm = self.manual_bpm
        beats = t * bpm / 60.0
        return beats, bpm

    def phase(self, t):
        """Return (beat_phase, bar_phase, bpm, beat_index) at absolute time ``t``."""
        t = float(t)
        if not self.locked or (self._last_t is not None and t - self._last_t > self.timeout):
            beats, bpm = self._free(t)
        else:
            bpm = self.bpm if self.bpm > 0 else self.manual_bpm
            pulse_len = 60.0 / (bpm * self.PPQN)
            frac = min((t - self._last_t) / pulse_len, 1.0) if pulse_len > 0 else 0.0
            beats = self._beat_index + (self._pulse + frac) / self.PPQN
        beat_phase = beats % 1.0
        bar_phase = (beats / self.BEATS_PER_BAR) % 1.0
        return float(beat_phase), float(bar_phase), float(bpm), int(beats)
