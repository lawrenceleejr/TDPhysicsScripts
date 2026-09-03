"""Script CHOP: the flood, one frame at a time.

Drop this in a Script CHOP's callbacks DAT. Every frame it moves the walkers
on and writes two channels — `grow` and `tone` — with one sample per line
followed by one per vertex, which is the order feynman/ribbons.py indexed the
geometry with. Convert this to a TOP and the shader can look either up.

This is the only thing that runs per frame, and it is two numpy arrays of about
a thousand samples, so it is cheap. The geometry it drives was built once.

Custom parameters to put on the Script CHOP (Component Editor, page 'Flood'):

    Geo         OP      ../field_geo    the Script SOP holding the field
    Speed       Float   1       multiplies the traverse — higher is faster
    Traverse    Float   34      seconds for a front to cross the whole field
    Tail        Float   0.85    how much stays lit behind the head. Past 1.0
                                nothing fades and the field fills and holds
    Fade        Float   0.34    how much of the tail is spent fading
    Walkers     Int     2       fronts travelling at once
    Seed        Int     3
    Freeze      Toggle  0       hold the picture where it is
    Fill        Pulse           light the whole field at once
    Reseed      Pulse           throw the fronts somewhere new

VJ notes. Speed is the obvious one to put on a fader. Tail is the other: at
0.2 it is a bright snake, at 0.85 a wide travelling wash, past 1.0 the whole
field lights and stays. Walkers 1 reads as a single sweep, 4 as weather.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from feynman.field import Flood          # noqa: E402
from feynman.ribbons import state_rows   # noqa: E402

_state = {"flood": None, "key": None, "field": None}


def _key(scriptOp):
    geo = scriptOp.par.Geo.eval()
    return (geo.path if geo else None,
            int(scriptOp.par.Walkers.eval()),
            int(scriptOp.par.Seed.eval()),
            geo.storage.get("field_w") if geo else None)


def _ensure(scriptOp):
    geo = scriptOp.par.Geo.eval()
    if geo is None:
        return None
    # feynman_geo caches the loaded Field; ask it rather than loading twice.
    mod = geo.par.callbacks.eval() if hasattr(geo.par, "callbacks") else None
    field = None
    if mod is not None and hasattr(mod.module, "field_of"):
        field = mod.module.field_of(geo)
    if field is None:
        return None

    key = _key(scriptOp)
    if _state["key"] != key or _state["flood"] is None:
        _state["flood"] = Flood(
            field,
            walkers=max(1, int(scriptOp.par.Walkers.eval())),
            traverse=max(0.5, float(scriptOp.par.Traverse.eval())),
            tail=max(0.01, float(scriptOp.par.Tail.eval())),
            fade=min(0.99, max(0.01, float(scriptOp.par.Fade.eval()))),
            seed=int(scriptOp.par.Seed.eval()),
        )
        _state["key"] = key
        _state["field"] = field
    return field


def onPulse(par):
    if par.name == "Reseed":
        _state["key"] = None
    elif par.name == "Fill" and _state["flood"] is not None:
        _state["flood"].fill()
    par.owner.cook(force=True)


def cook(scriptOp):
    field = _ensure(scriptOp)
    scriptOp.clear()
    if field is None:
        # Nothing wired up yet: two empty channels, so the shader still reads.
        scriptOp.numSamples = 1
        scriptOp.appendChan("grow")[0] = 0.0
        scriptOp.appendChan("tone")[0] = 0.0
        return

    flood = _state["flood"]
    # Live dials that do not need the flood rebuilt.
    flood.traverse = max(0.5, float(scriptOp.par.Traverse.eval())
                         / max(0.01, float(scriptOp.par.Speed.eval())))
    flood.tail = max(0.01, float(scriptOp.par.Tail.eval()))
    flood.fade = min(0.99, max(0.01, float(scriptOp.par.Fade.eval())))

    if not scriptOp.par.Freeze.eval():
        dt = 1.0 / max(1.0, float(me.time.rate))
        flood.advance(dt)

    grow, tone = state_rows(field, flood)
    scriptOp.numSamples = len(grow)
    scriptOp.appendChan("grow").vals = grow.tolist()
    scriptOp.appendChan("tone").vals = tone.tolist()
