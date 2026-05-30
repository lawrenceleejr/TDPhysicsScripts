# Script TOP callback - Dimuon invariant-mass HUD overlay.
#
# Histograms the CMS dimuon dataset's invariant mass on a log axis and draws a
# translucent spectrum panel as an RGBA overlay, with the J/psi, Upsilon and Z
# resonances marked (left -> right). A bright marker tracks the mass of the
# event the Open Data scene is currently drawing, tying the HUD to the live
# tracks.
#
# Built/wired by touchdesigner/td_build.py (build_opendata). The repo path is
# baked into the _REPO line below so `import physics` works at cook time.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.opendata import load_dimuon, RESONANCES

# Script TOP arrays are uploaded with row 0 at the BOTTOM of the image (TD's
# OpenGL convention), so the panel below is drawn bottom-up. If your build
# shows it flipped, set this True.
_FLIP_Y = False

_STATE = {}

# (mass, RGB) for the resonance marker lines, ordered low -> high mass.
_PEAKS = [
    (RESONANCES["J/psi"], (0.20, 0.95, 1.00)),
    (RESONANCES["Upsilon"], (1.00, 0.35, 0.95)),
    (RESONANCES["Z"], (1.00, 0.92, 0.30)),
]


def _state(scriptOp):
    return _STATE.setdefault(scriptOp.path, {})


def _p(o, name, default):
    par = getattr(o.par, name, None)
    try:
        return par.eval() if par is not None else default
    except Exception:
        return default


def onSetupParameters(scriptOp):
    if hasattr(scriptOp.par, "Massmax"):
        return  # idempotent
    page = scriptOp.appendCustomPage("Mass HUD")
    page.appendFloat("Massmin", label="Mass Min (GeV)")[0].val = 0.5
    scriptOp.par.Massmin.normMin, scriptOp.par.Massmin.normMax = 0.2, 5.0
    page.appendFloat("Massmax", label="Mass Max (GeV)")[0].val = 120.0
    scriptOp.par.Massmax.normMin, scriptOp.par.Massmax.normMax = 10.0, 200.0


def _xpix(m, lo, hi, x0, x1):
    """Log-scale mass -> x pixel."""
    m = np.clip(m, lo, hi)
    f = (np.log10(m) - np.log10(lo)) / (np.log10(hi) - np.log10(lo))
    return x0 + f * (x1 - x0)


def _base_image(W, H, lo, hi):
    """The static part of the HUD: panel, gridlines, histogram, peak markers.

    Returns (rgba float32 [H,W,4], geometry tuple) with row 0 = bottom.
    """
    img = np.zeros((H, W, 4), dtype=np.float32)

    # Panel occupies a band near the bottom of the frame.
    px0, px1 = 70, W - 50
    py0, py1 = 50, min(H - 40, 360)
    bx0, bx1 = px0 + 40, px1 - 20      # plot (bars) area
    by0, by1 = py0 + 26, py1 - 30

    # Translucent dark panel + a bright top accent line.
    img[py0:py1, px0:px1, :3] = (0.015, 0.025, 0.045)
    img[py0:py1, px0:px1, 3] = 0.42
    img[py1 - 2:py1, px0:px1, :3] = (0.25, 0.55, 0.75)
    img[py1 - 2:py1, px0:px1, 3] = 0.85

    # Decade gridlines (behind the bars).
    for mark in (1, 3, 10, 30, 100):
        if lo <= mark <= hi:
            gx = int(round(_xpix(mark, lo, hi, bx0, bx1)))
            img[by0:by1, gx:gx + 1, :3] = (0.30, 0.35, 0.42)
            img[by0:by1, gx:gx + 1, 3] = 0.5

    # Histogram of the real (or synthetic) invariant masses, one bin per column.
    M = np.asarray(load_dimuon()["M"], dtype=np.float64)
    M = M[np.isfinite(M)]
    ncols = max(8, bx1 - bx0)
    edges = np.logspace(np.log10(lo), np.log10(hi), ncols + 1)
    counts, _ = np.histogram(np.clip(M, lo, hi), bins=edges)
    norm = np.log10(counts + 1.0)                     # log so peaks + continuum show
    norm = norm / max(float(norm.max()), 1e-9)
    for j in range(ncols):
        h = int(round(norm[j] * (by1 - by0)))
        if h <= 0:
            continue
        t = float(norm[j])
        col = (0.15 + 0.85 * t, 0.45 + 0.50 * t, 0.85 + 0.15 * t)  # blue->white
        img[by0:by0 + h, bx0 + j, :3] = col
        img[by0:by0 + h, bx0 + j, 3] = 0.92

    # Resonance marker lines, on top of the bars.
    for mass, col in _PEAKS:
        if lo <= mass <= hi:
            mx = int(round(_xpix(mass, lo, hi, bx0, bx1)))
            img[by0:by1, mx:mx + 2, :3] = col
            img[by0:by1, mx:mx + 2, 3] = 0.6

    return img, (lo, hi, bx0, bx1, by0, by1)


def onCook(scriptOp):
    st = _state(scriptOp)
    W, H = int(scriptOp.width), int(scriptOp.height)
    lo = float(_p(scriptOp, "Massmin", 0.5))
    hi = float(_p(scriptOp, "Massmax", 120.0))
    if hi <= lo * 1.001:
        hi = lo * 10.0

    key = (W, H, round(lo, 3), round(hi, 3))
    if st.get("key") != key:
        st["base"], st["geom"] = _base_image(W, H, lo, hi)
        st["key"], st["lastmass"] = key, None

    # The Open Data sim stores the mass of the event it's currently drawing.
    mass = 0.0
    try:
        sp = scriptOp.parent().fetch("simpath", "")
        if sp:
            mass = float(op(sp).fetch("invariant_mass", 0.0))  # noqa: F821
    except Exception:
        pass

    # Only redraw/upload when the marked event actually changes; otherwise the
    # TOP keeps its last image (mirrors the Open Data SOP's frame guard).
    mkey = round(mass, 2)
    if st.get("lastmass") == mkey:
        return
    st["lastmass"] = mkey

    lo, hi, bx0, bx1, by0, by1 = st["geom"]
    img = st["base"].copy()
    if mass > 0:
        cx = min(max(int(round(_xpix(mass, lo, hi, bx0, bx1))), bx0), bx1 - 2)
        img[by0:by1, cx:cx + 2, :3] = (1.0, 1.0, 1.0)
        img[by0:by1, cx:cx + 2, 3] = 0.95

    if _FLIP_Y:
        img = np.flipud(img)
    scriptOp.copyNumpyArray(np.ascontiguousarray(img, dtype=np.float32))


# Backwards-compat aliases for older TouchDesigner callback names.
setupParameters = onSetupParameters
cook = onCook
