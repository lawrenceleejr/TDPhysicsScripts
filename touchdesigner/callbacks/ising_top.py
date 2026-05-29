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

    rgb = palette.colorize(sim.field01(), pal)            # (H, W, 3) float32
    if wall_glow > 0.0:
        rgb = rgb + wall_glow * sim.domain_walls()[..., None]  # glowing edges
    rgb = np.ascontiguousarray(np.clip(rgb, 0.0, 1.0), dtype=np.float32)
    scriptOp.copyNumpyArray(rgb)


# Backwards-compat aliases for older TouchDesigner callback names.
setupParameters = onSetupParameters
cook = onCook
