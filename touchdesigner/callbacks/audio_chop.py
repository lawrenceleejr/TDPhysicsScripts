# Script CHOP callback -- turn the DJ's audio into reactive control channels.
#
# Input 0 = an audio CHOP (mono or stereo; the live feed). Outputs one sample
# per channel:  bass, mid, high, level, beat, bpm.  Scenes, shaders and the
# post-FX bind their uniforms/params to these (e.g. op('Reactor/analyze')['bass']).
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
from physics.audio import AudioAnalyzer, BeatTracker

_STATE = {}
_CHANNELS = ["bass", "mid", "high", "level", "beat", "bpm"]


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
    s.val = 1.5
    scriptOp.par.Beatsens.normMin, scriptOp.par.Beatsens.normMax = 1.05, 3.0
    page.appendFloat("Beathold", label="Beat Hold (s)")[0].val = 0.18


def onPulse(par):
    pass


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
        st["beat"] = BeatTracker()
        st["flash"] = 0.0
    analyzer.sr = sr
    analyzer.attack = float(_p(scriptOp, "Attack", 0.7))
    analyzer.release = float(_p(scriptOp, "Release", 0.12))
    analyzer.gain = float(_p(scriptOp, "Gain", 6.0))
    bt = st["beat"]
    bt.sensitivity = float(_p(scriptOp, "Beatsens", 1.5))
    bt.refractory = float(_p(scriptOp, "Beathold", 0.18))

    x = _samples(scriptOp)
    feats = analyzer.analyze(x)

    frame = absTime.frame  # noqa: F821 (TD global)
    dt = 1.0 / _fps(scriptOp)
    if st.get("last_frame") != frame:
        is_beat = bt.update(feats["bass"], dt)
        st["flash"] = 1.0 if is_beat else max(0.0, st.get("flash", 0.0) - dt * 6.0)
        st["last_frame"] = frame

    vals = {
        "bass": feats["bass"], "mid": feats["mid"], "high": feats["high"],
        "level": feats["level"], "beat": st.get("flash", 0.0),
        "bpm": bt.bpm if bt.bpm > 0 else 0.0,
    }
    _emit(scriptOp, vals)


setupParameters = onSetupParameters
cook = onCook
