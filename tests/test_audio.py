"""Tests for the audio + tempo DSP cores (physics/audio.py)."""
import numpy as np

from physics.audio import AudioAnalyzer, BeatTracker, TempoClock


def _tone(freq, n=4096, sr=44100.0, amp=0.8):
    t = np.arange(n) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_band_separation():
    """A pure tone should light up the band that contains its frequency."""
    a = AudioAnalyzer()
    # Run a few blocks so the envelope follower settles.
    for _ in range(8):
        bass = a.analyze(_tone(60.0))
    a2 = AudioAnalyzer()
    for _ in range(8):
        high = a2.analyze(_tone(9000.0))
    assert bass["bass"] > bass["high"]
    assert high["high"] > high["bass"]


def test_level_tracks_amplitude():
    loud = AudioAnalyzer()
    quiet = AudioAnalyzer()
    for _ in range(12):
        lv = loud.analyze(_tone(300.0, amp=0.9))
        qv = quiet.analyze(_tone(300.0, amp=0.05))
    assert lv["level"] > qv["level"]
    assert lv["level"] >= 0.0


def test_silence_is_zero():
    a = AudioAnalyzer()
    f = a.analyze(np.zeros(2048, dtype=np.float32))
    assert all(v >= 0.0 for v in f.values())
    assert f["level"] < 1e-3


def test_spectrum_shape_and_range():
    a = AudioAnalyzer()
    bars = a.spectrum(_tone(1000.0), n_out=48)
    assert bars.shape == (48,)
    assert bars.min() >= 0.0 and bars.max() <= 1.0 + 1e-5
    assert bars.max() > 0.5  # the tone produces a clear peak


def test_beat_tracker_detects_pulses():
    bt = BeatTracker(sensitivity=1.3, refractory=0.1)
    dt = 1.0 / 60.0
    beats = 0
    # 2 Hz pulse train (120 bpm): a spike every 30 frames.
    for i in range(600):
        energy = 1.0 if (i % 30 == 0) else 0.02
        if bt.update(energy, dt):
            beats += 1
    assert beats >= 8
    assert 100.0 <= bt.bpm <= 140.0


def test_tempo_clock_follows_midi_clock():
    tc = TempoClock(default_bpm=90.0)
    bpm = 128.0
    pulse_dt = 60.0 / (bpm * TempoClock.PPQN)
    t = 0.0
    tc.on_start(t)
    for _ in range(TempoClock.PPQN * 4):  # four beats of clock
        t += pulse_dt
        tc.on_pulse(t)
    bp, bar, est, idx = tc.phase(t)
    assert abs(est - bpm) < 4.0
    assert 0.0 <= bp <= 1.0 and 0.0 <= bar <= 1.0


def test_tempo_clock_phase_is_monotonic_within_beat():
    tc = TempoClock(default_bpm=120.0)
    pulse_dt = 60.0 / (120.0 * TempoClock.PPQN)
    t = 0.0
    tc.on_start(t)
    last = -1.0
    rose = False
    for _ in range(TempoClock.PPQN - 1):
        t += pulse_dt
        tc.on_pulse(t)
        bp, _, _, _ = tc.phase(t)
        if bp > last:
            rose = True
        last = bp
    assert rose


def test_tempo_clock_free_runs_without_clock():
    tc = TempoClock(default_bpm=120.0)
    # No pulses ever -> should free-run on the manual bpm.
    bp0, _, bpm0, _ = tc.phase(0.0)
    bp1, _, bpm1, idx1 = tc.phase(0.5)  # half a beat at 120 bpm
    assert bpm0 == 120.0 and bpm1 == 120.0
    assert abs(bp1 - 1.0) < 0.05 or abs(bp1 - 0.0) < 0.05  # ~1 beat elapsed
