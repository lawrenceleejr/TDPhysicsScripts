# Script SOP callback - the Feynman field as polylines, built once.
#
# The geometry never moves: every line of the field and every vertex mark is
# appended here one time, and what animates is the colour and alpha of each
# point, which arrives from feynman_chop.py through a CHOP to SOP. Rebuilding
# twelve thousand points in Python every frame is what that arrangement is
# there to avoid.
#
# So this cooks again only when the field, the world size or the marks toggle
# actually changes. The point order it lays down -- every line in file order,
# then a mark for every vertex that carries one -- is the contract the CHOP
# matches sample for sample.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.feynman import FeynmanShow

# The fields that ship in data/feynman. Export more with
#   node data/feynman/export_field.mjs --w 2560 --h 1080 --seed 3
FIELDS = ["16x9", "16x9-b", "16x9-c", "21x9", "9x16", "1x1"]

_STATE = {}


def _state(scriptOp):
    return _STATE.setdefault(scriptOp.path, {})


def _p(o, name, default):
    par = getattr(o.par, name, None)
    try:
        return par.eval() if par is not None else default
    except Exception:
        return default


def field_path(name):
    root = _REPO or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "feynman", "%s.json" % name)


def onSetupParameters(scriptOp):
    if hasattr(scriptOp.par, "Field"):
        return  # already set up (idempotent)
    page = scriptOp.appendCustomPage("Field")
    menu = page.appendMenu("Field", label="Field")[0]
    menu.menuNames = FIELDS
    menu.menuLabels = FIELDS
    menu.val = "16x9"
    page.appendFloat("World", label="World Width")[0].val = 16.0
    scriptOp.par.World.normMin, scriptOp.par.World.normMax = 4.0, 40.0
    page.appendToggle("Marks", label="Vertex Marks")[0].val = True
    page.appendPulse("Rebuild", label="Rebuild")


def onPulse(par):
    if par.name == "Rebuild":
        st = _state(par.owner)
        st["key"] = None
        try:
            par.owner.cook(force=True)
        except Exception:
            pass


def _build(scriptOp, show):
    scriptOp.clear()
    scriptOp.pointAttribs.create("Cd", (0.0, 0.0, 0.0))
    for pts in show.polys:
        m = len(pts)
        if m < 2:
            continue
        poly = scriptOp.appendPoly(m, closed=False, addPoints=True)
        for i in range(m):
            vtx = poly[i]
            vtx.point.x = float(pts[i, 0])
            vtx.point.y = float(pts[i, 1])
            vtx.point.z = float(pts[i, 2])


def onCook(scriptOp):
    st = _state(scriptOp)
    field = str(_p(scriptOp, "Field", "16x9"))
    world = float(_p(scriptOp, "World", 16.0))
    marks = bool(_p(scriptOp, "Marks", True))

    key = (field, round(world, 3), marks)
    if st.get("key") == key:
        return
    path = field_path(field)
    if not os.path.exists(path):
        print("[feynman] no field at %s -- set TD_PHYSICS_REPO or the callback's "
              "_REPO to the repo root" % path)
        return
    # One walker: this instance is only ever asked for geometry, and each one
    # costs a flood.
    show = FeynmanShow(path, world=world, marks=marks, walkers=1)
    _build(scriptOp, show)
    st.update(key=key, points=show.n_points, polys=len(show.polys))


setupParameters = onSetupParameters
cook = onCook
