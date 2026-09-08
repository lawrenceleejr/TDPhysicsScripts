"""Tests for the audio + tempo DSP cores (physics/audio.py)."""
import numpy as np

from physics.audio import AudioAnalyzer, AutoLevel, BeatTracker, KickTracker, TempoClock


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



def _edm(bpm=128.0, seconds=16.0, sr=44100, amp=0.8, seed=0):
    """A synthetic four-on-the-floor: decaying 55 Hz kick + click on every
    beat, hats on the off-beats, a bass note, and noise. Returns (signal,
    kick_times)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = 0.02 * rng.standard_normal(n)
    x += 0.15 * np.sin(2 * np.pi * 110.0 * t)                  # sustained bass note
    period = 60.0 / bpm
    kicks = np.arange(0.5, seconds - 0.3, period)
    for k in kicks:
        i0 = int(k * sr)
        seg = t[i0:i0 + int(0.16 * sr)] - k
        env = np.exp(-seg * 22.0)
        x[i0:i0 + seg.size] += env * np.sin(2 * np.pi * (55.0 + 60.0 * np.exp(-seg * 40)) * seg)
        x[i0:i0 + 300] += 0.4 * np.exp(-np.arange(300) / 60.0)  # click
    for h in kicks + period / 2:                                 # off-beat hats
        i0 = int(h * sr)
        seg = min(int(0.04 * sr), n - i0)
        x[i0:i0 + seg] += 0.25 * rng.standard_normal(seg) * np.exp(-np.arange(seg) / (0.01 * sr))
    return (amp * x / np.abs(x).max()).astype(np.float32), kicks


def _run_kicks(signal, sr=44100, fps=60.0):
    a = AudioAnalyzer(sample_rate=sr)
    agc = AutoLevel(["bass"])
    kt = KickTracker()
    block = int(sr / fps)
    dt = 1.0 / fps
    detected = []
    for i in range(0, signal.size - block, block):
        f = a.analyze(signal[i:i + block])
        low = agc.normalize("bass", f["low_raw"], dt)
        if kt.update(low, dt):
            detected.append((i + block) / sr)
    return np.array(detected), kt


def test_kick_tracker_finds_edm_kicks_and_tempo():
    sig, kicks = _edm(bpm=128.0)
    det, kt = _run_kicks(sig)
    # after a 2 s warm-up, most kicks are found within 60 ms
    real = kicks[kicks > 2.0]
    hits = sum(1 for k in real if det.size and np.abs(det - k).min() < 0.06)
    assert hits / real.size > 0.85, (hits, real.size)
    # ... and there are few phantom beats
    phantom = sum(1 for d in det if d > 2.0 and np.abs(kicks - d).min() > 0.08)
    assert phantom <= 0.15 * real.size, phantom
    assert abs(kt.bpm - 128.0) < 4.0, kt.bpm
    assert 0.0 <= kt.phase() < 1.0 and 0.0 <= kt.pulse() <= 1.0


def test_auto_level_makes_quiet_and_loud_feeds_alike():
    quiet, _ = _edm(amp=0.05, seed=1)
    loud, _ = _edm(amp=0.95, seed=1)

    def peak_bass(sig):
        a = AudioAnalyzer()
        agc = AutoLevel(["bass"])
        vals = []
        block = 735
        for i in range(0, sig.size - block, block):
            f = a.analyze(sig[i:i + block])
            vals.append(agc.normalize("bass", f["low_raw"], 1 / 60))
        return np.percentile(vals[120:], 95)

    q, l = peak_bass(quiet), peak_bass(loud)
    assert 0.6 < q < 1.3 and 0.6 < l < 1.3, (q, l)
    assert abs(q - l) < 0.25


def test_kick_tracker_stays_quiet_in_silence_and_flashes_on_hit():
    kt = KickTracker()
    beats = sum(kt.update(0.0, 1 / 60) for _ in range(300))
    assert beats == 0 and kt.bpm == 0.0
    kt.hit()
    assert kt.envelope == 1.0
    for _ in range(30):
        kt.update(0.0, 1 / 60)
    assert kt.envelope < 0.2                      # ~0.5 s later it has decayed


def test_band_envelopes_are_smoother_than_before():
    """The second smoothing pole must take the frame-to-frame jitter out of a
    noisy but steady input while still following a step within a few frames."""
    rng = np.random.default_rng(2)
    a = AudioAnalyzer()
    vals = []
    for i in range(120):
        amp = 0.6 if i > 20 else 0.0
        x = (amp * np.sin(2 * np.pi * 300 * np.arange(735) / 44100)
             + 0.15 * amp * rng.standard_normal(735)).astype(np.float32)
        vals.append(a.analyze(x)["mid"])
    vals = np.array(vals)
    assert vals[30] > 0.5 * vals[-1]              # rises within ~10 frames
    assert np.abs(np.diff(vals[60:])).max() < 0.08 * vals[60:].mean()
