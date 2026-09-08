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
    mt = page.appendFloat("Morphtime", label="Orbital Morph (s)")[0]
    mt.val = 8.0
    scriptOp.par.Morphtime.normMin, scriptOp.par.Morphtime.normMax = 0.0, 30.0
    dp = page.appendFloat("Depthcue", label="Depth Cue (3D read)")[0]
    dp.val = 0.75
    scriptOp.par.Depthcue.normMin, scriptOp.par.Depthcue.normMax = 0.0, 1.0
    sp = page.appendFloat("Speed", label="Flow Speed (exaggerated)")[0]
    sp.val = 1.1
    scriptOp.par.Speed.normMin, scriptOp.par.Speed.normMax = 0.0, 12.0
    sub = page.appendInt("Substeps", label="Substeps / Frame")[0]
    sub.val = 2
    scriptOp.par.Substeps.normMin, scriptOp.par.Substeps.normMax = 1, 6
    scriptOp.par.Substeps.clampMin = True
    rf = page.appendFloat("Refresh", label="Respawn / sec")[0]
    rf.val = 0.22
    scriptOp.par.Refresh.normMin, scriptOp.par.Refresh.normMax = 0.0, 4.0
    page.appendFloat("Pointsize", label="Point Size")[0].val = 0.03
    scriptOp.par.Pointsize.normMin, scriptOp.par.Pointsize.normMax = 0.005, 0.15
    menu = page.appendMenu("Palette", label="Palette")[0]
    menu.menuNames = palette.PALETTE_NAMES
    menu.menuLabels = palette.PALETTE_NAMES
    menu.val = "ice"
    page.appendPulse("Reset", label="Reseed Cloud")


def morph_progress(scriptOp):
    """How far through an orbital morph this sim is (1 = settled). Handy on a
    UI and used by the tests."""
    return float(_state(scriptOp).get("morph", 1.0))


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
    speed = float(_p(scriptOp, "Speed", 1.1))
    substeps = int(_p(scriptOp, "Substeps", 2))
    refresh = float(_p(scriptOp, "Refresh", 0.22))
    morph_time = float(_p(scriptOp, "Morphtime", 8.0))
    depthcue = float(_p(scriptOp, "Depthcue", 0.75))
    psize = float(_p(scriptOp, "Pointsize", 0.03))
    pal = _p(scriptOp, "Palette", "ice")

    if "pos" not in st or st.get("count") != n:
        state = HydrogenState.preset(orbital)
        st["state"] = st["target"] = state
        st["rng"] = np.random.default_rng()
        st["t"] = 0.0
        st["extent"] = 6.0 * state.nmax ** 2 * 0.5 + 6.0
        st["pos"] = _seed(st, n)
        st["orbital"] = orbital
        st["count"] = n
        st["morph"] = 1.0
        st["out_frame"] = -1
    elif st.get("orbital") != orbital:
        # A new orbital does not cut: the cloud is handed a coherent
        # superposition of where it was and where it is going, and the weight
        # crosses over during Morphtime, so it reshapes itself continuously.
        st["from"] = st["state"]
        st["target"] = HydrogenState.preset(orbital)
        st["orbital"] = orbital
        st["morph"] = 0.0 if morph_time > 0 else 1.0
        st["extent"] = max(st.get("extent", 6.0),
                           6.0 * st["target"].nmax ** 2 * 0.5 + 6.0)

    if st.get("morph", 1.0) < 1.0:
        try:
            rate = 1.0 / max(float(scriptOp.time.rate), 1.0)
        except Exception:
            rate = 1.0 / 60.0
        st["morph"] = min(1.0, st["morph"] + rate / max(morph_time, 1e-3))
        w = st["morph"]
        w = w * w * (3.0 - 2.0 * w)                 # ease in and out
        st["state"] = HydrogenState.blend(st["from"], st["target"], w)
        st["out_frame"] = -1                        # the field changed this frame
        if st["morph"] >= 1.0:
            st["state"] = st["target"]

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
            try:
                rate = 1.0 / max(float(scriptOp.time.rate), 1.0)
            except Exception:
                rate = 1.0 / 60.0
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
        # Depth cue: the camera looks down +z, so brightness and point size
        # follow z. Near electrons read bright and large, far ones sink away,
        # which is what gives a flat additive cloud its volume.
        if depthcue > 0.0:
            ext = max(float(st.get("extent", 6.0)), 1e-6)
            z = np.clip(pos[:, 2] / ext, -1.0, 1.0).astype(np.float32)
            near = 0.5 + 0.5 * z                     # 0 far, 1 near
            col = col * (1.0 - depthcue + depthcue * (0.25 + 1.15 * near))[:, None]
            np.clip(col, 0.0, 1.0, out=col)
            size = psize * (1.0 - depthcue * 0.55 + depthcue * 1.1 * near)
        else:
            size = np.full(pos.shape[0], psize, dtype=np.float32)
        out = np.empty((7, pos.shape[0]), dtype=np.float32)
        out[0:3] = pos.T.astype(np.float32)
        out[3:6] = col.T
        out[6] = size
        st["out"], st["out_frame"] = out, frame
    scriptOp.clear()
    scriptOp.copyNumpyArray(st["out"], baseName="c")


setupParameters = onSetupParameters
cook = onCook
