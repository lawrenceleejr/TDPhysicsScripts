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
    # Earlier events stay on screen, dimming with age, so the frame reads as
    # a detector event display rather than two lines at a time.
    page.appendInt("History", label="Events Kept")[0].val = 10
    scriptOp.par.History.normMin, scriptOp.par.History.normMax = 0, 40
    scriptOp.par.History.clampMin = True
    menu = page.appendMenu("Palette", label="Palette")[0]
    menu.menuNames = palette.PALETTE_NAMES
    menu.menuLabels = palette.PALETTE_NAMES
    menu.val = "ice"
    page.appendPulse("Nextevent", label="Next Event")


def _advance(st, now):
    """Move to the next event, keeping the finished one in the history."""
    show = st["show"]
    hist = st.setdefault("history", [])
    hist.insert(0, show.tracks)              # newest first, full length
    del hist[40:]
    show.next_event()
    st["t0"] = now
    st["last_built"] = None


def onPulse(par):
    st = _state(par.owner)
    if par.name == "Nextevent" and "show" in st:
        _advance(st, absTime.seconds)


def _build(scriptOp, layers, scale):
    """``layers`` is a list of (tracks, brightness): the live event at 1.0,
    then the kept history, each dimmer than the last."""
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
    for tracks, dim in layers:
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
                b = dim * (0.4 + 0.6 * (i / (m - 1)))
                vtx.point.Cd = (float(base[0] * b), float(base[1] * b), float(base[2] * b))


def onCook(scriptOp):
    st = _state(scriptOp)
    period = float(_p(scriptOp, "Eventperiod", 2.5))
    grow_time = float(_p(scriptOp, "Growtime", 0.6))
    scale = float(_p(scriptOp, "Scale", 0.6))
    order = _p(scriptOp, "Order", "random")
    pal = _p(scriptOp, "Palette", "ice")

    keep = max(0, int(_p(scriptOp, "History", 10)))

    show = st.get("show")
    if show is None or st.get("order") != order or st.get("pal") != pal:
        show = DimuonShow(palette=pal, order=order)
        st.update(show=show, t0=absTime.seconds, order=order, pal=pal,
                  last_built=None, history=[])

    now = absTime.seconds
    if now - st["t0"] >= period:
        _advance(st, now)

    elapsed = now - st["t0"]
    frac = 1.0 if grow_time <= 0 else min(elapsed / grow_time, 1.0)

    # Expose the current invariant mass so the HUD can read it. It goes on the
    # scene COMP (the SOP's parent), not on this SOP: storage is dependable,
    # and an op writing its own storage while it cooks is a cook-dependency
    # loop on itself. Written only when the event changes.
    mass = float(show.current_mass)
    if st.get("stored_mass") != mass:
        st["stored_mass"] = mass
        try:
            scriptOp.parent().store("invariant_mass", mass)
        except Exception:
            pass

    # Quantise the reveal to whole points: tracks have n_points samples, so
    # rebuilding more often than that re-appends identical geometry (the old
    # 1%-of-frac step did about a third more rebuilds than could show).
    quant = (int(frac * show.n_points), keep)
    if st.get("last_built") == quant:
        return
    st["last_built"] = quant
    hist = st.get("history", [])[:keep]
    layers = [(show.grow(frac), 1.0)]
    for i, tracks in enumerate(hist):
        layers.append((tracks, 0.55 * (1.0 - (i + 1) / (keep + 1))))
    _build(scriptOp, layers, scale)


setupParameters = onSetupParameters
cook = onCook
