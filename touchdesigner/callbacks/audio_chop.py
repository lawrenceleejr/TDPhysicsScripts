# Script CHOP callback -- turn the DJ's audio into reactive control channels.
#
# Input 0 = an audio CHOP (mono or stereo; the live feed). Outputs one sample
# per channel:
#   bass mid high level  smoothed bands, auto-levelled to 0..1 (Auto on)
#   beat                 a flash on each kick, exponential decay (~0.25 s)
#   kick                 onset strength of the last kick (0 between kicks)
#   pulse                smooth 0..1 pulsation peaking on each predicted beat
#   bpm                  the tracked tempo
# Scenes, shaders and the post-FX bind to these (op('Reactor/analyze')['bass']).
#
# Auto levels: with 'Auto' on (default) every band is normalised by its own
# slow-decaying peak, so a quiet feed and a hot one look the same and nothing
# has to be trimmed before a set. 'Reset Levels' forgets the history; 'Hit' is a
# manual beat; 'Mute' zeroes everything (the show stops reacting).
#
# The heavy lifting lives in physics/audio.py (numpy, unit-tested); this file is
# the thin TD adapter, advanced once per frame like every other sim here.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.audio import AudioAnalyzer, AutoLevel, KickTracker

_STATE = {}
_CHANNELS = ["bass", "mid", "high", "level", "beat", "kick", "pulse", "bpm"]
_BANDS = ["bass", "mid", "high", "level"]


def _state(scriptOp):
    return _STATE.setdefault(scriptOp.path, {})


def _p(o, name, default):
    par = getattr(o.par, name, None)
    try:
        return par.eval() if par is not None else default
    except Exception:
        return default


def _fps(scriptOp):
    """The frame rate this op runs at (its Time COMP), never below 1."""
    try:
        return max(float(scriptOp.time.rate), 1.0)
    except Exception:
        return 60.0


def _emit(scriptOp, vals):
    """Write one named, single-sample channel per entry of ``vals``.

    appendChan is the documented Script CHOP way to get *named* channels;
    copyNumpyArray only ever names them <base>0, <base>1, ... and every
    expression downstream reads these by name (op('Reactor/analyze')['bass']).

    A Script CHOP fed by an audio CHOP inherits Time Slice mode, in which the
    sample count is the frame's audio block and cannot be edited ("Editing
    numSamples is not supported in Time Slice mode"). These are control
    values, one per frame, so Time Slice is switched off first; if a build
    refuses even that, every sample of the slice is filled so a reader of the
    current sample still sees the value.
    """
    try:
        if scriptOp.isTimeSlice:
            scriptOp.isTimeSlice = False
    except Exception:
        pass
    scriptOp.clear()
    try:
        scriptOp.numSamples = 1
    except Exception:
        pass
    n = 1
    try:
        n = max(1, int(scriptOp.numSamples))
    except Exception:
        pass
    for name in _CHANNELS:
        ch = scriptOp.appendChan(name)
        v = float(vals.get(name, 0.0))
        if n == 1:
            ch[0] = v
        else:
            try:
                ch.vals = [v] * n
            except Exception:
                ch[0] = v


def onSetupParameters(scriptOp):
    if hasattr(scriptOp.par, "Attack"):
        return  # idempotent
    page = scriptOp.appendCustomPage("Audio")
    a = page.appendFloat("Attack", label="Attack (rise)")[0]
    a.val = 0.7
    scriptOp.par.Attack.normMin, scriptOp.par.Attack.normMax = 0.05, 1.0
    r = page.appendFloat("Release", label="Release (fall)")[0]
    r.val = 0.12
    scriptOp.par.Release.normMin, scriptOp.par.Release.normMax = 0.02, 1.0
    g = page.appendFloat("Gain", label="Input Gain")[0]
    g.val = 6.0
    scriptOp.par.Gain.normMin, scriptOp.par.Gain.normMax = 0.5, 20.0
    s = page.appendFloat("Beatsens", label="Beat Sensitivity")[0]
    s.val = 1.6
    scriptOp.par.Beatsens.normMin, scriptOp.par.Beatsens.normMax = 1.05, 3.0
    page.appendFloat("Beathold", label="Beat Hold (s)")[0].val = 0.22
    d = page.appendFloat("Depth", label="Reactive Depth")[0]
    d.val = 0.6
    scriptOp.par.Depth.normMin, scriptOp.par.Depth.normMax = 0.0, 1.0
    page.appendToggle("Auto", label="Auto Levels")[0].val = True
    page.appendToggle("Mute", label="Mute (stop reacting)")[0].val = False
    page.appendPulse("Resetlevels", label="Reset Levels")
    page.appendPulse("Hit", label="Hit (manual beat)")


def onPulse(par):
    st = _state(par.owner)
    if par.name == "Resetlevels":
        for key in ("agc", "kick"):
            if key in st:
                st[key].reset()
    elif par.name == "Hit" and "kick" in st:
        st["kick"].hit()


def _samples(scriptOp):
    """Mono float32 samples from input 0, or empty if nothing connected."""
    if not scriptOp.inputs:
        return np.zeros(0, dtype=np.float32)
    src = scriptOp.inputs[0]
    try:
        arr = src.numpyArray()  # (channels, samples)
        if arr is None or arr.size == 0:
            return np.zeros(0, dtype=np.float32)
        return np.ascontiguousarray(arr.mean(axis=0), dtype=np.float32)
    except Exception:
        try:
            return np.ascontiguousarray(src[0].vals, dtype=np.float32)
        except Exception:
            return np.zeros(0, dtype=np.float32)


def onCook(scriptOp):
    st = _state(scriptOp)
    sr = 44100.0
    try:
        sr = float(scriptOp.inputs[0].rate) if scriptOp.inputs else 44100.0
    except Exception:
        sr = 44100.0

    analyzer = st.get("analyzer")
    if analyzer is None:
        analyzer = AudioAnalyzer(sample_rate=sr)
        st["analyzer"] = analyzer
        st["agc"] = AutoLevel(_BANDS + ["low"])
        st["kick"] = KickTracker(decay=0.3)
    analyzer.sr = sr
    analyzer.attack = float(_p(scriptOp, "Attack", 0.7))
    analyzer.release = float(_p(scriptOp, "Release", 0.12))
    analyzer.gain = float(_p(scriptOp, "Gain", 6.0))
    kt = st["kick"]
    kt.sensitivity = float(_p(scriptOp, "Beatsens", 1.6))
    kt.refractory = float(_p(scriptOp, "Beathold", 0.22))
    auto = bool(_p(scriptOp, "Auto", True))
    mute = bool(_p(scriptOp, "Mute", False))
    depth = max(0.0, min(1.0, float(_p(scriptOp, "Depth", 0.6))))

    x = _samples(scriptOp)
    feats = analyzer.analyze(x)

    frame = absTime.frame  # noqa: F821 (TD global)
    dt = 1.0 / _fps(scriptOp)
    agc = st["agc"]
    if st.get("last_frame") != frame:
        # Auto-levelled bands; the kick detector runs on the auto-levelled raw
        # low band so it sees the same-sized kicks whatever the input gain.
        bands = {}
        for name in _BANDS:
            v = float(feats[name])
            bands[name] = min(agc.normalize(name, v, dt), 1.0) if auto else min(v, 1.0)
        low = agc.normalize("low", float(feats.get("low_raw", 0.0)), dt)
        kt.update(low if auto else min(float(feats.get("low_raw", 0.0)) * analyzer.gain, 1.5), dt)
        st["bands"] = bands
        st["last_frame"] = frame
    bands = st.get("bands", {n: 0.0 for n in _BANDS})

    # The beat flash is eased in over ~40 ms (a one-pole on the rising edge)
    # so it swells rather than clicks; its fall is the tracker's own decay.
    beat_s = st.get("beat_s", 0.0)
    if kt.envelope > beat_s:
        beat_s += (kt.envelope - beat_s) * (1.0 - np.exp(-dt / 0.04))
    else:
        beat_s = kt.envelope
    st["beat_s"] = beat_s

    # Depth scales how hard the show reacts (0 = still, 1 = full); the pulse
    # stays centred on 0.5 so bound parameters breathe about their rest value.
    vals = {
        "bass": bands["bass"] * depth, "mid": bands["mid"] * depth,
        "high": bands["high"] * depth, "level": bands["level"] * depth,
        "beat": beat_s * depth, "kick": kt.strength * depth,
        "pulse": 0.5 + (kt.pulse() - 0.5) * depth, "bpm": kt.bpm,
    }
    if mute or depth <= 0.0:
        vals = {k: (kt.bpm if k == "bpm" else (0.5 if k == "pulse" else 0.0)) for k in vals}
    _emit(scriptOp, vals)


setupParameters = onSetupParameters
cook = onCook
