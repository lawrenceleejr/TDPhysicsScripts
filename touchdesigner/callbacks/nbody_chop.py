# Script CHOP callback - N-body gravity -> per-particle instance channels.
#
# Outputs 7 channels (c0..c6), one sample per body, for GPU instancing:
#   c0,c1,c2 = tx,ty,tz   c3,c4,c5 = r,g,b (colour by speed)   c6 = scale
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.nbody import NBodySim
from physics import palette

_STATE = {}
_INITS = {
    "two_galaxies": NBodySim.two_galaxies,
    "rotating_disk": NBodySim.rotating_disk,
    "plummer_sphere": NBodySim.plummer_sphere,
}


def _state(scriptOp):
    return _STATE.setdefault(scriptOp.path, {})


def _p(o, name, default):
    par = getattr(o.par, name, None)
    try:
        return par.eval() if par is not None else default
    except Exception:
        return default


def onSetupParameters(scriptOp):
    if hasattr(scriptOp.par, "Initial"):
        return  # already set up (idempotent)
    page = scriptOp.appendCustomPage("N-Body")
    m = page.appendMenu("Initial", label="Initial Condition")[0]
    m.menuNames = list(_INITS.keys())
    m.menuLabels = ["Colliding galaxies", "Rotating disk", "Star cluster"]
    m.val = "two_galaxies"
    c = page.appendInt("Count", label="Bodies")[0]
    c.val = 600
    scriptOp.par.Count.normMin, scriptOp.par.Count.normMax = 100, 1500
    scriptOp.par.Count.clampMin = True
    page.appendFloat("Gravity", label="G")[0].val = 1.0
    scriptOp.par.Gravity.normMin, scriptOp.par.Gravity.normMax = 0.0, 3.0
    page.appendFloat("Timestep", label="Time Step")[0].val = 0.005
    scriptOp.par.Timestep.normMin, scriptOp.par.Timestep.normMax = 0.0005, 0.02
    page.appendFloat("Softening", label="Softening")[0].val = 0.15
    scriptOp.par.Softening.normMin, scriptOp.par.Softening.normMax = 0.02, 0.6
    sub = page.appendInt("Substeps", label="Substeps / Frame")[0]
    sub.val = 1
    scriptOp.par.Substeps.normMin, scriptOp.par.Substeps.normMax = 1, 6
    scriptOp.par.Substeps.clampMin = True
    page.appendFloat("Pointsize", label="Point Size")[0].val = 0.045
    scriptOp.par.Pointsize.normMin, scriptOp.par.Pointsize.normMax = 0.005, 0.2
    menu = page.appendMenu("Palette", label="Palette")[0]
    menu.menuNames = palette.PALETTE_NAMES
    menu.menuLabels = palette.PALETTE_NAMES
    menu.val = "inferno"
    page.appendPulse("Reset", label="Reset / New System")


def onPulse(par):
    if par.name == "Reset":
        _state(par.owner).pop("sim", None)


def _make(initial, n, G, soft, dt):
    ctor = _INITS.get(initial, NBodySim.two_galaxies)
    return ctor(n=n, G=G, softening=soft, dt=dt)


def onCook(scriptOp):
    st = _state(scriptOp)
    initial = _p(scriptOp, "Initial", "two_galaxies")
    n = int(_p(scriptOp, "Count", 600))
    G = float(_p(scriptOp, "Gravity", 1.0))
    dt = float(_p(scriptOp, "Timestep", 0.005))
    soft = float(_p(scriptOp, "Softening", 0.08))
    substeps = int(_p(scriptOp, "Substeps", 1))
    psize = float(_p(scriptOp, "Pointsize", 0.045))
    pal = _p(scriptOp, "Palette", "inferno")

    sim = st.get("sim")
    rebuilt = False
    if sim is None or st.get("key") != (initial, n):
        sim = _make(initial, n, G, soft, dt)
        st["sim"] = sim
        st["key"] = (initial, n)
        rebuilt = True
    sim.G, sim.dt, sim.softening = G, dt, soft

    # Step + assemble the instance array at most ONCE per frame. A Script CHOP
    # can be cooked several times per frame (viewers, the instancing geo, ...);
    # the cached output is just re-emitted on those extra cooks.
    frame = absTime.frame
    if rebuilt or st.get("out_frame") != frame:
        if sim.last_frame != frame:
            for _ in range(substeps):
                sim.step()
            sim.last_frame = frame
        pos = sim.positions.astype(np.float32)
        speeds = sim.speeds()
        vmax = float(np.percentile(speeds, 92)) if speeds.size else 1.0
        col = palette.colorize(palette.normalize(speeds, 0.0, max(vmax, 1e-6), gamma=0.6), pal)
        mnorm = palette.normalize(np.cbrt(np.maximum(sim.masses, 0.0)))
        scale = (psize * (0.6 + 1.4 * mnorm)).astype(np.float32)
        out = np.empty((7, pos.shape[0]), dtype=np.float32)
        out[0:3] = pos.T
        out[3:6] = col.T
        out[6] = scale
        st["out"], st["out_frame"] = out, frame
    scriptOp.clear()
    scriptOp.copyNumpyArray(st["out"], baseName="c")


setupParameters = onSetupParameters
cook = onCook
