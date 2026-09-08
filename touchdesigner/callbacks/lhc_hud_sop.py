# Script SOP callback - the LHC detector readout: dog-leg leaders + labels.
#
# Lives in a Geometry COMP that does NOT orbit, so the labels stay upright and
# square to the camera while the event turns underneath them. The anchors are
# the real track points, rotated by the scene's own Orbit angle, so each leader
# stays pinned to its track as it swings past.
#
# The tracks are published by lhc_sop.py into the scene COMP's storage under
# 'hud_tracks'; this callback only draws.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics import hud
from physics import palette

# Colour roles, as an instrument: the leader and its tick are cool and quiet,
# the readout is bright, the event line is the accent.
COL_LEAD = (0.30, 0.62, 0.78)
COL_TICK = (0.55, 0.85, 1.00)
COL_TEXT = (0.80, 0.93, 1.00)
COL_EVENT = (1.00, 0.62, 0.28)


def _p(o, name, default):
    par = getattr(o.par, name, None)
    try:
        return par.eval() if par is not None else default
    except Exception:
        return default


def _scene(scriptOp):
    """The scene COMP: walk up until something carries the Orbit control."""
    o = scriptOp
    for _ in range(4):
        o = o.parent()
        if o is None:
            return None
        if hasattr(o.par, "Orbit"):
            return o
    return None


def onSetupParameters(scriptOp):
    if hasattr(scriptOp.par, "Labels"):
        return
    page = scriptOp.appendCustomPage("Readout")
    n = page.appendInt("Labels", label="Labelled Tracks")[0]
    n.val = 4
    scriptOp.par.Labels.normMin, scriptOp.par.Labels.normMax = 0, 8
    scriptOp.par.Labels.clampMin = True
    w = page.appendFloat("Column", label="Label Column (world x)")[0]
    w.val = 4.6
    scriptOp.par.Column.normMin, scriptOp.par.Column.normMax = 2.0, 9.0
    h = page.appendFloat("Textsize", label="Readout Size")[0]
    h.val = 0.26
    scriptOp.par.Textsize.normMin, scriptOp.par.Textsize.normMax = 0.08, 0.6
    page.appendToggle("Showreadout", label="Show Readout")[0].val = True


def _build(scriptOp, polys, kinds):
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
    for pts, kind in zip(polys, kinds):
        m = len(pts)
        if m < 2:
            continue
        if kind == "tick":
            col = COL_TICK
        elif kind == "lead":
            col = COL_LEAD
        elif kind == "event":
            col = COL_EVENT
        else:
            col = COL_TEXT
        poly = scriptOp.appendPoly(m, closed=False, addPoints=True)
        for i in range(m):
            vtx = poly[i]
            vtx.point.x = float(pts[i, 0])
            vtx.point.y = float(pts[i, 1])
            vtx.point.z = float(pts[i, 2])
            vtx.point.Cd = (float(col[0]), float(col[1]), float(col[2]))


def onCook(scriptOp):
    scene = _scene(scriptOp)
    if scene is None or not bool(_p(scriptOp, "Showreadout", True)):
        scriptOp.clear()
        return
    published = []
    try:
        published = scene.fetch("hud_tracks", []) or []
    except Exception:
        published = []
    n_labels = max(0, int(_p(scriptOp, "Labels", 4)))
    if not published or n_labels == 0:
        scriptOp.clear()
        return

    # The event turns under the labels: rotate every anchor by the same angle
    # the scene's Orbit expression is applying to the geometry right now.
    try:
        orbit = float(scene.par.Orbit.eval())
        turn = (orbit * absTime.seconds) % 360.0   # noqa: F821 (TD global)
    except Exception:
        turn = 0.0

    rows = published[:n_labels]
    anchors = hud.rotate_y(np.asarray([r["anchor"] for r in rows], dtype=np.float32), turn)
    labels = [r["label"] for r in rows]

    polys, kinds = hud.leaders(
        anchors, labels,
        half_width=float(_p(scriptOp, "Column", 4.6)),
        text_h=float(_p(scriptOp, "Textsize", 0.26)),
    )

    # The event header sits above the column, in the accent colour.
    header = None
    try:
        header = scene.fetch("hud_header", None)
    except Exception:
        header = None
    if header:
        h = float(_p(scriptOp, "Textsize", 0.26)) * 1.25
        top = 3.2
        for poly in hud.strokes(str(header), height=h,
                                origin=(-float(_p(scriptOp, "Column", 4.6)), top)):
            arr = np.zeros((len(poly), 3), dtype=np.float32)
            arr[:, 0] = poly[:, 0]
            arr[:, 1] = poly[:, 1]
            polys.append(arr)
            kinds.append("event")
    _build(scriptOp, polys, kinds)


setupParameters = onSetupParameters
cook = onCook
