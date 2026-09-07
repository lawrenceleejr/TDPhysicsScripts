# Script CHOP callback -- a tempo engine that locks to an incoming MIDI beat
# clock (24 PPQN) or free-runs on a manual BPM. Outputs continuous phase so any
# visual can ride the DJ's tempo.
#
# Output channels (one sample each):
#   bpm   current tempo
#   beat  0..1 ramp, resets every quarter note
#   bar   0..1 ramp over 4 beats
#   sine  0.5 +/- a beat-locked sine (smooth LFO)
#   pulse 1.0 on the frame a beat starts, else 0.0
#
# The paired MIDI In DAT (built by td_build) calls on_realtime() for clock /
# start / stop messages. Manual BPM always works, so visuals lock even with no
# clock plugged in -- the MIDI path is uncertain across TD versions, this isn't.
import sys, os, math

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from physics.audio import TempoClock

_STATE = {}
_CHANNELS = ["bpm", "beat", "bar", "sine", "pulse"]


def _state(op_):
    return _STATE.setdefault(op_.path, {})


def _clock(op_):
    st = _state(op_)
    clk = st.get("clock")
    if clk is None:
        clk = TempoClock()
        st["clock"] = clk
        st["last_beat"] = -1
    return clk


def _p(o, name, default):
    par = getattr(o.par, name, None)
    try:
        return par.eval() if par is not None else default
    except Exception:
        return default


def onSetupParameters(scriptOp):
    if hasattr(scriptOp.par, "Bpm"):
        return
    page = scriptOp.appendCustomPage("Tempo")
    b = page.appendFloat("Bpm", label="Manual BPM")[0]
    b.val = 120.0
    scriptOp.par.Bpm.normMin, scriptOp.par.Bpm.normMax = 60.0, 200.0
    page.appendToggle("Usemidiclock", label="Follow MIDI Clock")[0].val = True
    sm = page.appendFloat("Smoothing", label="Clock Smoothing")[0]
    sm.val = 0.18
    scriptOp.par.Smoothing.normMin, scriptOp.par.Smoothing.normMax = 0.02, 1.0
    page.appendPulse("Resetphase", label="Reset Phase")


def onPulse(par):
    if par.name == "Resetphase":
        clk = _clock(par.owner)
        try:
            clk.on_start(absTime.seconds)  # noqa: F821
        except Exception:
            pass


def on_realtime(scriptOp, message):
    """Called by the MIDI In DAT for realtime/transport messages."""
    if scriptOp is None:
        return
    msg = (message or "").lower()
    try:
        t = absTime.seconds  # noqa: F821
    except Exception:
        t = 0.0
    clk = _clock(scriptOp)
    if "clock" in msg or "timing" in msg or "tick" in msg:
        clk.on_pulse(t)
    elif "start" in msg:
        clk.on_start(t)
    elif "continue" in msg:
        clk.locked = True
    elif "stop" in msg:
        clk.on_stop(t)


def onCook(scriptOp):
    clk = _clock(scriptOp)
    clk.smoothing = float(_p(scriptOp, "Smoothing", 0.18))
    clk.set_manual_bpm(float(_p(scriptOp, "Bpm", 120.0)))
    if not int(_p(scriptOp, "Usemidiclock", 1)):
        clk.locked = False  # ignore clock -> free-run on manual BPM

    try:
        t = absTime.seconds  # noqa: F821
    except Exception:
        t = 0.0
    beat_phase, bar_phase, bpm, beat_index = clk.phase(t)

    st = _state(scriptOp)
    pulse = 1.0 if beat_index != st.get("last_beat", -1) else 0.0
    st["last_beat"] = beat_index

    sine = 0.5 + 0.5 * math.sin(beat_phase * 2.0 * math.pi)
    vals = {"bpm": bpm, "beat": beat_phase, "bar": bar_phase, "sine": sine, "pulse": pulse}
    # Named single-sample channels (appendChan is the documented way to name
    # Script CHOP channels; everything downstream reads these by name). Leave
    # Time Slice mode first: numSamples cannot be edited while it is on.
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
    for name in _CHANNELS:
        ch = scriptOp.appendChan(name)
        ch[0] = float(vals[name])


setupParameters = onSetupParameters
cook = onCook
