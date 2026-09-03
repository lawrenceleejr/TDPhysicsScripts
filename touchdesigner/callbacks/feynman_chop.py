# Script CHOP callback - the flood over the Feynman field, as point colours.
#
# Four channels, one sample per point of feynman_sop.py's geometry: r, g, b and
# alpha. A CHOP to SOP lands them on the field's points, so the front travels,
# each line draws itself out of the vertex the front reached, and the wake
# fades -- all of it as one numpy expression per frame rather than a Python
# loop over the geometry.
#
# Alpha is how a line is trimmed to its growth: points past the growing tip go
# to zero, and the renderer's interpolation gives the tip its soft end over
# the three-pixel spacing of the sampled wave.
#
# The field and marks settings are read from the Script SOP rather than
# duplicated here, because the two have to agree point for point or the
# colours land on the wrong lines.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.feynman import FeynmanShow
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


def field_path(name):
    root = _REPO or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "feynman", "%s.json" % name)


def onSetupParameters(scriptOp):
    if hasattr(scriptOp.par, "Tail"):
        return  # already set up (idempotent)
    page = scriptOp.appendCustomPage("Flood")
    # Relative to this CHOP's parent, which is the scene: the Script SOP is
    # one level down inside the Geometry COMP.
    page.appendStr("Geosop", label="Geometry SOP")[0].val = "geo/lines"
    # The lifetime of a line, as a share of a whole traverse: tail * traverse
    # seconds. Long, and the field fills up and sits there; short, and the
    # pattern is always turning over. The website reads this off the scroll.
    page.appendFloat("Tail", label="Line Lifetime")[0].val = 0.3
    scriptOp.par.Tail.normMin, scriptOp.par.Tail.normMax = 0.05, 1.2
    page.appendFloat("Traverse", label="Seconds / Traverse")[0].val = 30.0
    scriptOp.par.Traverse.normMin, scriptOp.par.Traverse.normMax = 4.0, 90.0
    page.appendFloat("Fade", label="Fade Share")[0].val = 0.5
    scriptOp.par.Fade.normMin, scriptOp.par.Fade.normMax = 0.05, 1.0
    page.appendInt("Walkers", label="Fronts")[0].val = 3
    scriptOp.par.Walkers.normMin, scriptOp.par.Walkers.normMax = 1, 6
    menu = page.appendMenu("Palette", label="Palette")[0]
    menu.menuNames = palette.PALETTE_NAMES
    menu.menuLabels = palette.PALETTE_NAMES
    menu.val = "sigma"
    page.appendToggle("Hold", label="Hold Lit")[0].val = False
    page.appendPulse("Reseed", label="New Fronts")


def onPulse(par):
    st = _state(par.owner)
    if par.name == "Reseed" and "show" in st:
        st["show"].flood.reseed()


def _find_geo(scriptOp):
    """The Script SOP holding the geometry, however the path is written.

    A relative path from a CHOP resolves against its parent, so both
    "geo/lines" and "../geo/lines" get tried, and failing those anything named
    lines under the scene that carries a Field parameter.
    """
    path = str(_p(scriptOp, "Geosop", "geo/lines"))
    for base in (scriptOp, scriptOp.parent()):
        for pat in (path, path.lstrip("./"), "../" + path.lstrip("./")):
            try:
                o = base.op(pat)
            except Exception:
                o = None
            if o is not None and hasattr(o.par, "Field"):
                return o
    try:
        for o in scriptOp.parent().findChildren(name="lines", depth=3):
            if hasattr(o.par, "Field"):
                return o
    except Exception:
        pass
    return None


def _show(scriptOp, st):
    """The show, rebuilt only when something structural changes."""
    geo = _find_geo(scriptOp)
    field = str(_p(geo, "Field", "16x9")) if geo else "16x9"
    world = float(_p(geo, "World", 16.0)) if geo else 16.0
    marks = bool(_p(geo, "Marks", True)) if geo else True
    walkers = max(1, int(_p(scriptOp, "Walkers", 3)))
    tail = float(_p(scriptOp, "Tail", 0.3))

    key = (field, round(world, 3), marks)
    show = st.get("show")
    if show is None or st.get("key") != key:
        path = field_path(field)
        if not os.path.exists(path):
            return None
        show = FeynmanShow(path, world=world, marks=marks, walkers=walkers,
                           tail=tail, traverse=float(_p(scriptOp, "Traverse", 30.0)),
                           fade=float(_p(scriptOp, "Fade", 0.5)))
        st.update(show=show, key=key, walkers=walkers, t=None, frame=None)

    # The dials that need no rebuild.
    show.flood.tail = tail
    show.flood.traverse = max(0.5, float(_p(scriptOp, "Traverse", 30.0)))
    show.flood.fade = float(_p(scriptOp, "Fade", 0.5))
    show.set_palette(str(_p(scriptOp, "Palette", "sigma")))
    if st.get("walkers") != walkers:
        show.flood.reseed(walkers)
        st["walkers"] = walkers
    return show


def onCook(scriptOp):
    st = _state(scriptOp)
    show = _show(scriptOp, st)
    if show is None:
        scriptOp.clear()
        return

    # One step per frame, however many times TouchDesigner asks us to cook.
    if st.get("frame") != absTime.frame:
        st["frame"] = absTime.frame
        now = absTime.seconds
        last = st.get("t")
        st["t"] = now
        if bool(_p(scriptOp, "Hold", False)):
            show.flood.fill()
        else:
            dt = 1.0 / 60.0 if last is None else min(0.05, max(0.0, now - last))
            show.step(dt)

    out = np.ascontiguousarray(show.colours().T.astype(np.float32))
    scriptOp.copyNumpyArray(out, baseName="c")


setupParameters = onSetupParameters
cook = onCook
