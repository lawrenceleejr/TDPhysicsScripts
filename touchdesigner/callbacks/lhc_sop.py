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


def _scene(scriptOp):
    """The scene COMP (the one carrying Orbit), walking up from this SOP."""
    o = scriptOp
    for _ in range(4):
        o = o.parent()
        if o is None:
            return None
        if hasattr(o.par, "Orbit"):
            return o
    return None


def _publish(scriptOp, tracks, scale, gen):
    """Hand the readout the tracks worth labelling: the highest-momentum ones,
    each with a point on its own body to anchor a leader to.

    Storage on the scene COMP, not on this SOP: an op writing its own storage
    while it cooks is a cook dependency on itself.
    """
    scene = _scene(scriptOp)
    if scene is None:
        return
    try:
        from physics.hud import track_labels
    except Exception:
        return
    ranked = sorted(tracks, key=lambda t: -float(getattr(t, "pt", 0.0)))[:8]
    labels = track_labels(ranked)
    rows = []
    for tr, label in zip(ranked, labels):
        pts = tr.points
        if len(pts) < 2:
            continue
        # 72% along what is drawn so far: on the track, clear of its tip.
        k = max(1, int(len(pts) * 0.72) - 1)
        rows.append({"anchor": (float(pts[k, 0] * scale), float(pts[k, 1] * scale),
                                float(pts[k, 2] * scale)),
                     "label": label,
                     "pt": float(getattr(tr, "pt", 0.0))})
    try:
        scene.store("hud_tracks", rows)
        scene.store("hud_header", "EVENT %04d  B %.1f T  %d TRACKS"
                    % (getattr(gen, "event_index", 0) % 10000, float(getattr(gen, "B", 0.0)),
                       len(tracks)))
    except Exception:
        pass


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
    # Quantise the reveal to whole points: tracks have n_points samples, so
    # rebuilding more often than that re-appends identical geometry (the old
    # 1%-of-frac step did about a third more rebuilds than could show).
    quant = int(frac * gen.n_points)
    if st.get("last_built") == quant:
        return
    st["last_built"] = quant
    tracks = gen.grow(frac)
    _build(scriptOp, tracks, scale)
    _publish(scriptOp, tracks, scale, gen)


setupParameters = onSetupParameters
cook = onCook
