# Script TOP callback - 2D Ising model -> neon texture.
#
# Paste into a Script TOP's callbacks DAT, or (recommended) let
# touchdesigner/td_build.py build the whole scene for you. The build script
# bakes the repo path into the `_REPO = r""` line below so `import physics`
# works at cook time.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.ising import IsingModel, CRITICAL_TEMPERATURE
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
    if hasattr(scriptOp.par, "Size"):
        return  # already set up (idempotent)
    page = scriptOp.appendCustomPage("Ising")
    page.appendInt("Size", label="Lattice Size")[0].val = 256
    scriptOp.par.Size.normMin, scriptOp.par.Size.normMax = 64, 1024
    scriptOp.par.Size.clampMin = True
    t = page.appendFloat("Temperature", label="Temperature")[0]
    t.val = round(float(CRITICAL_TEMPERATURE), 4)
    scriptOp.par.Temperature.normMin, scriptOp.par.Temperature.normMax = 0.2, 5.0
    s = page.appendInt("Sweeps", label="Sweeps / Frame")[0]
    s.val = 1
    scriptOp.par.Sweeps.normMin, scriptOp.par.Sweeps.normMax = 1, 8
    scriptOp.par.Sweeps.clampMin = True
    page.appendFloat("Wallglow", label="Domain Wall Glow")[0].val = 0.6
    menu = page.appendMenu("Palette", label="Palette")[0]
    menu.menuNames = palette.PALETTE_NAMES
    menu.menuLabels = palette.PALETTE_NAMES
    menu.val = "inferno"
    page.appendToggle("Invert", label="Invert Domains")[0].val = True
    page.appendPulse("Reset", label="Reset")


def onPulse(par):
    if par.name == "Reset":
        _state(par.owner).pop("sim", None)


def onCook(scriptOp):
    st = _state(scriptOp)
    size = int(_p(scriptOp, "Size", 256))
    temperature = float(_p(scriptOp, "Temperature", float(CRITICAL_TEMPERATURE)))
    sweeps = int(_p(scriptOp, "Sweeps", 1))
    wall_glow = float(_p(scriptOp, "Wallglow", 0.6))
    pal = _p(scriptOp, "Palette", "inferno")

    sim = st.get("sim")
    if sim is None or sim.size != size:
        sim = IsingModel(size=size, temperature=temperature)
        st["sim"] = sim
    sim.temperature = temperature

    # Advance at most once per rendered frame (cheap if pulled multiple times).
    frame = absTime.frame
    if sim.last_frame != frame:
        sim.step(sweeps)
        sim.last_frame = frame

    # Spins are binary, so the colour map is a two-entry table, not an interp
    # over the whole lattice; the image is filled one channel at a time with
    # broadcast scalars, which is the cheapest way numpy writes an (H, W, 3).
    # Sample the ramp *inside* its ends, not at them. Taking the extremes made
    # the lit domain the palette's brightest colour, and since a domain covers
    # roughly half the lattice the frame came out at mean 0.95 -- a white
    # screen, which is the opposite of what this show wants. A mid tone for the
    # domain leaves the domain-wall glow as the only real highlight, which is
    # also the part worth looking at.
    lo, hi = palette.colorize(np.array([0.06, 0.62], dtype=np.float32), pal)
    if bool(_p(scriptOp, "Invert", True)):
        lo, hi = hi, lo               # the other domain is the lit one
    up = (sim.spins > 0).astype(np.float32)               # 1 where spin is +1
    rgb = np.empty(sim.spins.shape + (3,), dtype=np.float32)
    walls = sim.domain_walls() * wall_glow if wall_glow > 0.0 else None
    for c in range(3):
        ch = rgb[..., c]
        np.multiply(up, float(hi[c] - lo[c]), out=ch)
        ch += float(lo[c])
        if walls is not None:
            ch += walls                                    # glowing edges
    np.clip(rgb, 0.0, 1.0, out=rgb)
    scriptOp.copyNumpyArray(rgb)

# Backwards-compat aliases for older TouchDesigner callback names.
setupParameters = onSetupParameters
cook = onCook
