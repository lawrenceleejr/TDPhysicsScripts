# Script CHOP callback -- Bohmian (pilot-wave) electrons in hydrogen orbitals.
#
# A cloud of electrons is sampled from |psi|^2 and advected by the de Broglie-
# Bohm guidance velocity v = Im(grad psi / psi). For an m != 0 orbital they
# circulate about the z-axis; for m = 0 / real orbitals they sit nearly still;
# superpositions of different energies slosh. Outputs the 7 instance channels
# (c0..c6 = pos, colour, scale) used by the instanced/POP renderer.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.hydrogen import HydrogenState, PRESET_NAMES
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
    if hasattr(scriptOp.par, "Orbital"):
        return
    page = scriptOp.appendCustomPage("Bohmian H")
    o = page.appendMenu("Orbital", label="Orbital / Superposition")[0]
    o.menuNames = PRESET_NAMES
    o.menuLabels = PRESET_NAMES
    o.val = PRESET_NAMES[0]
    c = page.appendInt("Count", label="Electrons")[0]
    c.val = 20000
    scriptOp.par.Count.normMin, scriptOp.par.Count.normMax = 2000, 120000
    scriptOp.par.Count.clampMin = True
    sp = page.appendFloat("Speed", label="Flow Speed (exaggerated)")[0]
    sp.val = 4.0
    scriptOp.par.Speed.normMin, scriptOp.par.Speed.normMax = 0.0, 12.0
    sub = page.appendInt("Substeps", label="Substeps / Frame")[0]
    sub.val = 2
    scriptOp.par.Substeps.normMin, scriptOp.par.Substeps.normMax = 1, 6
    scriptOp.par.Substeps.clampMin = True
    rf = page.appendFloat("Refresh", label="Respawn / sec")[0]
    rf.val = 0.6
    scriptOp.par.Refresh.normMin, scriptOp.par.Refresh.normMax = 0.0, 4.0
    page.appendFloat("Pointsize", label="Point Size")[0].val = 0.03
    scriptOp.par.Pointsize.normMin, scriptOp.par.Pointsize.normMax = 0.005, 0.15
    menu = page.appendMenu("Palette", label="Palette")[0]
    menu.menuNames = palette.PALETTE_NAMES
    menu.menuLabels = palette.PALETTE_NAMES
    menu.val = "ice"
    page.appendPulse("Reset", label="Reseed Cloud")


def onPulse(par):
    if par.name == "Reset":
        _state(par.owner).pop("pos", None)


_DT = 0.06  # internal integration step (atomic time units)


def _seed(st, n):
    pos = st["state"].sample_density(n, t=st["t"], rng=st["rng"], extent=st["extent"])
    return pos.astype(np.float64)


def onCook(scriptOp):
    st = _state(scriptOp)
    orbital = _p(scriptOp, "Orbital", PRESET_NAMES[0])
    n = int(_p(scriptOp, "Count", 20000))
    speed = float(_p(scriptOp, "Speed", 4.0))
    substeps = int(_p(scriptOp, "Substeps", 2))
    refresh = float(_p(scriptOp, "Refresh", 0.6))
    psize = float(_p(scriptOp, "Pointsize", 0.03))
    pal = _p(scriptOp, "Palette", "ice")

    if st.get("key") != (orbital, n) or "pos" not in st:
        state = HydrogenState.preset(orbital)
        st["state"] = state
        st["rng"] = np.random.default_rng()
        st["t"] = 0.0
        st["extent"] = 6.0 * state.nmax ** 2 * 0.5 + 6.0
        st["pos"] = _seed(st, n)
        st["key"] = (orbital, n)
        st["out_frame"] = -1

    state = st["state"]
    pos = st["pos"]

    # Integrate + assemble at most once per frame; reuse the last guidance
    # velocity from the integration loop for colouring (it's an O(N) complex
    # wavefunction eval over up to 120k points -- don't compute it twice).
    frame = absTime.frame  # noqa: F821 (TD global)
    if st.get("out_frame") != frame:
        v = None
        if st.get("last_frame") != frame:
            for _ in range(max(1, substeps)):
                v = state.bohm_velocity(pos, st["t"])
                pos = pos + v * (_DT * speed)
                st["t"] += _DT
            # Respawn a fraction (keeps the cloud crisp + alive under trails)
            # and recycle any electron that wandered well outside the orbital.
            rate = 1.0 / float(max(getattr(me.time, "rate", 60.0), 1.0))  # noqa: F821
            frac = min(max(refresh * rate, 0.0), 1.0)
            far = np.linalg.norm(pos, axis=1) > st["extent"] * 1.4
            pick = st["rng"].random(pos.shape[0]) < frac
            respawn = far | pick
            k = int(respawn.sum())
            if k:
                pos[respawn] = _seed(st, k)
            st["pos"] = pos
            st["last_frame"] = frame
        if v is None:
            v = state.bohm_velocity(pos, st["t"])
        speeds = np.linalg.norm(v, axis=1)
        vmax = float(np.percentile(speeds, 92)) if speeds.size else 1.0
        col = palette.colorize(palette.normalize(speeds, 0.0, max(vmax, 1e-6), gamma=0.6), pal)
        out = np.empty((7, pos.shape[0]), dtype=np.float32)
        out[0:3] = pos.T.astype(np.float32)
        out[3:6] = col.T
        out[6] = psize
        st["out"], st["out_frame"] = out, frame
    scriptOp.clear()
    scriptOp.copyNumpyArray(st["out"], baseName="c")


setupParameters = onSetupParameters
cook = onCook
