# Script CHOP callback - particle systems -> per-particle instance channels.
#
# Mode "flow"     : divergence-free curl-noise turbulence (smoke-like).
# Mode "softbody" : a rotating, wobbling shape-matched blob.
# Outputs 7 channels (c0..c6): c0..c2 tx/ty/tz, c3..c5 r/g/b, c6 scale.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from physics.particles import CurlNoiseFlow, ShapeMatchedSoftBody
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
    if hasattr(scriptOp.par, "Mode"):
        return  # already set up (idempotent)
    page = scriptOp.appendCustomPage("Particles")
    m = page.appendMenu("Mode", label="Mode")[0]
    m.menuNames = ["flow", "softbody"]
    m.menuLabels = ["Turbulent flow", "Rotating soft body"]
    m.val = "flow"
    c = page.appendInt("Count", label="Particles")[0]
    c.val = 20000
    scriptOp.par.Count.normMin, scriptOp.par.Count.normMax = 1000, 60000
    scriptOp.par.Count.clampMin = True
    page.appendFloat("Speed", label="Flow Speed")[0].val = 1.6
    scriptOp.par.Speed.normMin, scriptOp.par.Speed.normMax = 0.0, 5.0
    page.appendFloat("Scale", label="Noise Scale")[0].val = 0.22
    scriptOp.par.Scale.normMin, scriptOp.par.Scale.normMax = 0.05, 0.8
    page.appendFloat("Evolve", label="Evolve Rate")[0].val = 0.12
    scriptOp.par.Evolve.normMin, scriptOp.par.Evolve.normMax = 0.0, 1.0
    page.appendFloat("Spin", label="Soft Body Spin")[0].val = 1.1
    scriptOp.par.Spin.normMin, scriptOp.par.Spin.normMax = 0.0, 4.0
    page.appendFloat("Pointsize", label="Point Size")[0].val = 0.02
    scriptOp.par.Pointsize.normMin, scriptOp.par.Pointsize.normMax = 0.002, 0.1
    menu = page.appendMenu("Palette", label="Palette")[0]
    menu.menuNames = palette.PALETTE_NAMES
    menu.menuLabels = palette.PALETTE_NAMES
    menu.val = "cyber"
    page.appendPulse("Reset", label="Reset")


def onPulse(par):
    if par.name == "Reset":
        _state(par.owner).pop("sim", None)


def onCook(scriptOp):
    st = _state(scriptOp)
    mode = _p(scriptOp, "Mode", "flow")
    n = int(_p(scriptOp, "Count", 20000))
    speed = float(_p(scriptOp, "Speed", 1.6))
    scale = float(_p(scriptOp, "Scale", 0.22))
    evolve = float(_p(scriptOp, "Evolve", 0.12))
    spin = float(_p(scriptOp, "Spin", 1.1))
    psize = float(_p(scriptOp, "Pointsize", 0.02))
    pal = _p(scriptOp, "Palette", "cyber")

    sim = st.get("sim")
    rebuilt = False
    if sim is None or st.get("key") != (mode, n):
        if mode == "softbody":
            sim = ShapeMatchedSoftBody(n=n, spin=spin)
        else:
            sim = CurlNoiseFlow(n=n, freq=scale, speed=speed, evolve=evolve)
        st["sim"] = sim
        st["key"] = (mode, n)
        rebuilt = True
    # Live-tune the running sim.
    if isinstance(sim, CurlNoiseFlow):
        sim.freq, sim.speed, sim.evolve = scale, speed, evolve
    else:
        sim.spin = spin

    # Step + assemble at most once per frame (the CHOP can cook many times/frame).
    frame = absTime.frame
    if rebuilt or st.get("out_frame") != frame:
        if sim.last_frame != frame:
            # One frame of the op's own timeline, so 30 fps and 60 fps projects
            # see the same motion per second.
            try:
                dt = 1.0 / max(float(scriptOp.time.rate), 1.0)
            except Exception:
                dt = 1.0 / 60.0
            sim.step(dt)
            sim.last_frame = frame
        pos = sim.positions.astype(np.float32)
        field = sim.stress() if isinstance(sim, ShapeMatchedSoftBody) else sim.speeds()
        hi = float(np.percentile(field, 95)) if field.size else 1.0
        col = palette.colorize(palette.normalize(field, 0.0, max(hi, 1e-6), gamma=0.7), pal)
        out = np.empty((7, pos.shape[0]), dtype=np.float32)
        out[0:3] = pos.T
        out[3:6] = col.T
        out[6] = psize
        st["out"], st["out_frame"] = out, frame
    scriptOp.clear()
    scriptOp.copyNumpyArray(st["out"], baseName="c")


setupParameters = onSetupParameters
cook = onCook
