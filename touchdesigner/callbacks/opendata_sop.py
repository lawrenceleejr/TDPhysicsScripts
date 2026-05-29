# Script SOP callback - CMS dimuon open data as muon tracks.
#
# Cycles through real (or bundled synthetic) dimuon events, drawing the two
# muon tracks per event, colour-coded by the event's invariant mass. Run
# data/fetch_opendata.py to pull the real CMS dataset; otherwise the bundled
# sample (with J/psi, Upsilon and Z peaks) is used automatically.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.opendata import DimuonShow
from physics import palette

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
    if hasattr(scriptOp.par, "Eventperiod"):
        return  # already set up (idempotent)
    page = scriptOp.appendCustomPage("Open Data")
    page.appendFloat("Eventperiod", label="Seconds / Event")[0].val = 2.5
    scriptOp.par.Eventperiod.normMin, scriptOp.par.Eventperiod.normMax = 0.25, 10.0
    page.appendFloat("Growtime", label="Grow Time (s)")[0].val = 0.6
    scriptOp.par.Growtime.normMin, scriptOp.par.Growtime.normMax = 0.0, 3.0
    page.appendFloat("Scale", label="World Scale")[0].val = 0.6
    scriptOp.par.Scale.normMin, scriptOp.par.Scale.normMax = 0.1, 3.0
    o = page.appendMenu("Order", label="Event Order")[0]
    o.menuNames = ["mass", "random", "sequential"]
    o.menuLabels = ["By mass", "Random", "Sequential"]
    o.val = "random"
    menu = page.appendMenu("Palette", label="Palette")[0]
    menu.menuNames = palette.PALETTE_NAMES
    menu.menuLabels = palette.PALETTE_NAMES
    menu.val = "ice"
    page.appendPulse("Nextevent", label="Next Event")


def onPulse(par):
    st = _state(par.owner)
    if par.name == "Nextevent" and "show" in st:
        st["show"].next_event()
        st["t0"] = absTime.seconds
        st["last_built"] = None


def _build(scriptOp, tracks, scale):
    scriptOp.clear()
    scriptOp.pointAttribs.create("Cd", (0.0, 0.0, 0.0))
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
            b = 0.4 + 0.6 * (i / (m - 1))
            vtx.point.Cd = (float(base[0] * b), float(base[1] * b), float(base[2] * b))


def onCook(scriptOp):
    st = _state(scriptOp)
    period = float(_p(scriptOp, "Eventperiod", 2.5))
    grow_time = float(_p(scriptOp, "Growtime", 0.6))
    scale = float(_p(scriptOp, "Scale", 0.6))
    order = _p(scriptOp, "Order", "random")
    pal = _p(scriptOp, "Palette", "ice")

    show = st.get("show")
    if show is None or st.get("order") != order or st.get("pal") != pal:
        show = DimuonShow(palette=pal, order=order)
        st.update(show=show, t0=absTime.seconds, order=order, pal=pal, last_built=None)

    now = absTime.seconds
    if now - st["t0"] >= period:
        show.next_event()
        st["t0"] = now
        st["last_built"] = None

    elapsed = now - st["t0"]
    frac = 1.0 if grow_time <= 0 else min(elapsed / grow_time, 1.0)

    # Expose the current invariant mass so a HUD/Text can read it.
    try:
        scriptOp.store("invariant_mass", float(show.current_mass))
    except Exception:
        pass

    quant = round(frac, 2)
    if st.get("last_built") == quant:
        return
    st["last_built"] = quant
    _build(scriptOp, show.grow(frac), scale)


setupParameters = onSetupParameters
cook = onCook
