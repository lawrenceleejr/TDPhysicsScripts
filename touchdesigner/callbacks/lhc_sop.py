# Script SOP callback - LHC-style collision tracks as glowing polylines.
#
# Builds one open polyline per charged-particle track (helix in the solenoid
# field), colour-coded by pT and brightened toward the tip. A new collision
# fires every few seconds and the tracks animate outward from the vertex.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.lhc_tracks import LHCEventGenerator

_STATE = {}


def _state(scriptOp):
    return _STATE.setdefault(scriptOp.path, {})


def _p(o, name, default):
    par = getattr(o.par, name, None)
    try:
        return par.eval() if par is not None else default
    except Exception:
        return default


def onSetupParameters(scriptOp):
    if hasattr(scriptOp.par, "Bfield"):
        return  # already set up (idempotent)
    page = scriptOp.appendCustomPage("LHC")
    page.appendFloat("Bfield", label="B Field (T)")[0].val = 3.8
    scriptOp.par.Bfield.normMin, scriptOp.par.Bfield.normMax = 0.5, 6.0
    page.appendFloat("Eventperiod", label="Seconds / Event")[0].val = 6.0
    scriptOp.par.Eventperiod.normMin, scriptOp.par.Eventperiod.normMax = 1.0, 20.0
    page.appendFloat("Growtime", label="Grow Time (s)")[0].val = 1.2
    scriptOp.par.Growtime.normMin, scriptOp.par.Growtime.normMax = 0.0, 5.0
    page.appendFloat("Scale", label="World Scale")[0].val = 0.6
    scriptOp.par.Scale.normMin, scriptOp.par.Scale.normMax = 0.1, 3.0
    menu = page.appendMenu("Palette", label="Palette")[0]
    from physics import palette
    menu.menuNames = palette.PALETTE_NAMES
    menu.menuLabels = palette.PALETTE_NAMES
    menu.val = "inferno"
    page.appendPulse("Newevent", label="New Collision")


def onPulse(par):
    st = _state(par.owner)
    if par.name == "Newevent" and "gen" in st:
        st["gen"].new_event()
        st["t0"] = absTime.seconds
        st["last_built"] = None


def _build(scriptOp, tracks, scale):
    scriptOp.clear()
    # Cd is a standard attribute in TD 2025+ (no default allowed); older builds
    # require a default. Try both, then fall back to it already existing.
    try:
        scriptOp.pointAttribs.create("Cd", (0.0, 0.0, 0.0))
    except Exception:
        try:
            scriptOp.pointAttribs.create("Cd")
        except Exception:
            pass
    for tr in tracks:
        pts = tr.points
        m = len(pts)
        if m < 2:
            continue
        poly = scriptOp.appendPoly(m, closed=False, addPoints=True)
        base = tr.color
        for i in range(m):
            vtx = poly[i]
            vtx.point.x = float(pts[i, 0] * scale)
            vtx.point.y = float(pts[i, 1] * scale)
            vtx.point.z = float(pts[i, 2] * scale)
            # Brighten toward the leading tip for a comet-like streak.
            b = 0.35 + 0.65 * (i / (m - 1))
            vtx.point.Cd = (float(base[0] * b), float(base[1] * b), float(base[2] * b))


def onCook(scriptOp):
    st = _state(scriptOp)
    B = float(_p(scriptOp, "Bfield", 3.8))
    period = float(_p(scriptOp, "Eventperiod", 6.0))
    grow_time = float(_p(scriptOp, "Growtime", 1.2))
    scale = float(_p(scriptOp, "Scale", 0.6))
    pal = _p(scriptOp, "Palette", "inferno")

    gen = st.get("gen")
    if gen is None or st.get("B") != B or st.get("pal") != pal:
        gen = LHCEventGenerator(B=B, palette=pal)
        st.update(gen=gen, t0=absTime.seconds, B=B, pal=pal, last_built=None)

    now = absTime.seconds
    if now - st["t0"] >= period:
        gen.new_event()
        st["t0"] = now
        st["last_built"] = None

    elapsed = now - st["t0"]
    frac = 1.0 if grow_time <= 0 else min(elapsed / grow_time, 1.0)

    # Only rebuild geometry while the tracks are still growing (or first time).
    quant = round(frac, 2)
    if st.get("last_built") == quant:
        return
    st["last_built"] = quant
    _build(scriptOp, gen.grow(frac), scale)


setupParameters = onSetupParameters
cook = onCook
