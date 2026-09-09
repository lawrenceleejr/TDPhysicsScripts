"""Self-assembling TouchDesigner networks for the physics visuals.

Run this *inside* TouchDesigner to build fully wired, good-looking scenes in
one shot -- no manual node wrangling. The recommended entry point is
``build_all()``, which creates a ``PhysicsVJ`` component containing every
scene side-by-side plus a switcher, so you can flip between them instantly
during a set.

Typical use (in a Text DAT, then right-click > Run):

    import sys
    sys.path.insert(0, r"/path/to/TDPhysicsScripts")
    from touchdesigner import td_build
    td_build.build_all(op('/'))

Design for live performance:
  * Every scene is pre-built, so switching is just a parameter change.
  * Only the *visible* scene cooks: each scene has an ``Active`` flag and a
    frame-start cook driver, so idle scenes cost ~0 CPU. ``build_all`` wires
    ``Active`` to the scene selector. Flip ``Freerun All`` on to evolve every
    scene at once.
  * All sim->GPU handoff uses ``copyNumpyArray`` (the fast path).

Robustness: every parameter assignment is wrapped, so if a parameter name
differs on your TouchDesigner version the build still completes (it just
prints a note) and you can finish that one tweak by hand.
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

_CALLBACK_DIR = os.path.join(REPO, "touchdesigner", "callbacks")

# ``ParMode`` is a TouchDesigner enum. It is injected as a global in DAT scripts
# but NOT into imported modules, and which module exposes it has moved between
# builds (``from td import ParMode`` came back None on a 2025 build, which left
# every expression in the show unset). So it is resolved lazily, and first of
# all from the parameter itself: ``type(par.mode)`` *is* the enum class.
try:
    from td import ParMode  # noqa: F401
except Exception:
    ParMode = None


def _td_global(name):
    """A TouchDesigner global (op, app, project, ParMode ...) from an imported
    module, wherever this build exposes it. None if nowhere."""
    g = globals().get(name)
    if g is not None:
        return g
    for modname in ("td", "builtins"):
        try:
            mod = __import__(modname)
            val = getattr(mod, name, None)
            if val is not None:
                return val
        except Exception:
            pass
    return None


def _par_mode_enum(p):
    """The ParMode enum class, taken from the parameter's own mode value."""
    try:
        cls = type(p.mode)
        if hasattr(cls, "EXPRESSION"):
            return cls
    except Exception:
        pass
    return _td_global("ParMode")

# Stable scene table used by build_all (label, builder, kwargs).
SCENES = [
    ("Ising", "build_ising", {}),
    ("N-Body", "build_nbody", {}),
    ("Flow", "build_particles", {"mode": "flow", "name": "flow", "palette": "cyber"}),
    ("Soft Body", "build_particles", {"mode": "softbody", "name": "softbody", "palette": "synth"}),
    ("LHC Tracks", "build_lhc", {}),
    ("Open Data", "build_opendata", {}),
    ("React-Diff", "build_reaction_diffusion", {"name": "rd"}),
    ("Raymarch SDF", "build_raymarch", {"name": "sdf"}),
    ("POP Storm", "build_pops", {"name": "pops"}),
    ("Bohmian H", "build_bohmian", {"name": "hydrogen"}),
    ("Feynman", "build_feynman", {}),
]


# ---------------------------------------------------------------------------
# Low-level helpers (all defensive)
# ---------------------------------------------------------------------------
# Whole-screen FX toggles on PhysicsVJ, four per row of the APC's upper-right
# quadrant; the post shader reads them as uFxA..uFxD. Must match
# callbacks/apc_mini.FX_NAMES (a contract test checks).
FX_NAMES = ["Invert", "Edges", "Posterize", "Pixelate",
            "Mirror", "Mono", "Solarize", "Fisheye",
            "Tiles", "Shake", "Ascii", "Blur",
            "Kaleidofx", "Rgbboost", "Strobe", "Negflash"]

# A word or two per scene, shown in big caps by the TITLE button.
SCENE_TITLES = {
    "ising": "PHASE TRANSITION", "nbody": "GRAVITY", "flow": "CURL FLOW",
    "softbody": "SOFT MATTER", "lhc": "PROTON COLLISION", "opendata": "ATLAS DATA",
    "rd": "REACTION DIFFUSION", "sdf": "DISTANCE FIELDS", "pops": "PARTICLE STORM",
    "hydrogen": "QUANTUM HYDROGEN", "feynman": "FEYNMAN PATHS",
}


def _setpar(o, name, value):
    """Set a parameter if it exists; never raise."""
    try:
        p = getattr(o.par, name)
    except Exception:
        print(f"[td_build] no par {o.path}.{name}")
        return False
    try:
        p.val = value
        return True
    except Exception as e:
        print(f"[td_build] could not set {o.path}.{name} = {value!r}: {e}")
        return False


def _setpar_any(o, names, value, quiet=False):
    """Set the first of several candidate parameter names that exists.

    Parameter names have moved between TouchDesigner builds (Light colour is
    cr/cg/cb, GLSL MAT shaders are vdat/pdat ...); trying each spelling with
    _setpar would log a line per miss. This logs once, only if none matched.
    """
    for name in names:
        try:
            p = getattr(o.par, name)
        except Exception:
            continue
        try:
            p.val = value
            return name
        except Exception as e:
            print(f"[td_build] could not set {o.path}.{name} = {value!r}: {e}")
            return None
    if not quiet:
        print(f"[td_build] no par {o.path}.{'|'.join(names)}")
    return None


def _expr(o, name, expression, what="expr"):
    """Set a parameter to an expression; never raise, always report."""
    try:
        p = getattr(o.par, name)
    except Exception as e:
        print(f"[td_build] no par {o.path}.{name} for {what}: {e}")
        return False
    try:
        p.expr = expression
    except Exception as e:
        print(f"[td_build] could not set {what} {o.path}.{name}: {e}")
        return False
    # Setting .expr switches most builds to expression mode by itself; make
    # sure of it, using the enum class of the parameter's own mode value.
    try:
        pm = _par_mode_enum(p)
        if pm is not None and p.mode != pm.EXPRESSION:
            p.mode = pm.EXPRESSION
        return True
    except Exception as e:
        print(f"[td_build] set {what} on {o.path}.{name} but could not switch its mode: {e}")
        return False


def _create(parent, optype, name, x=0, y=0):
    """Create (replacing any existing) a child operator and position it."""
    existing = parent.op(name)
    if existing:
        try:
            existing.destroy()
        except Exception:
            pass
    o = parent.create(optype, name)
    try:
        o.nodeX, o.nodeY = x, y
    except Exception:
        pass
    return o


def _connect(src, dst, index=0):
    """Wire ``src``'s output into ``dst``'s input ``index`` -- and check it.

    TouchDesigner only wires operators that sit in the *same* network; a
    connect() across a COMP boundary does nothing and raises nothing, which is
    how every scene 'out' -> deck switch wire (and the Reactor textures ->
    overlay shader) went missing without a word in the report. So the wire is
    verified after the call and a miss is printed.
    """
    try:
        src.outputConnectors[0].connect(dst.inputConnectors[index])
    except Exception as e:
        print(f"[td_build] could not connect {src.path} -> {dst.path}[{index}]: {e}")
        return False
    try:
        wired = any(i is not None and i.path == src.path for i in dst.inputs)
    except Exception:
        return True                      # cannot check on this build; assume ok
    if not wired:
        print(f"[td_build] wire {src.path} -> {dst.path}[{index}] did not take"
              f" (different networks? {src.parent().path} vs {dst.parent().path})")
    return wired


def _install_callbacks(script_op, callback_filename):
    """Embed a callback source file into the Script OP, baking in the repo path."""
    path = os.path.join(_CALLBACK_DIR, callback_filename)
    with open(path, "r") as fh:
        text = fh.read()
    text = text.replace('_REPO = r""', f'_REPO = r"{REPO}"', 1)

    cbk = None
    try:
        cbk = script_op.par.callbacks.eval()
    except Exception:
        cbk = None
    if not cbk:
        cbk = _create(script_op.parent(), "textDAT", script_op.name + "_callbacks",
                      script_op.nodeX, script_op.nodeY - 130)
        _setpar(script_op, "callbacks", cbk)
    cbk.text = text
    # Create the custom parameters synchronously (a setuppars pulse may be
    # deferred, which would race the scene-specific overrides below). The
    # callbacks' onSetupParameters is idempotent, so this is safe even if TD
    # also runs it on its own.
    try:
        mod = cbk.module
        if hasattr(mod, "onSetupParameters"):
            mod.onSetupParameters(script_op)
    except Exception as e:
        print(f"[td_build] onSetupParameters failed for {script_op.path}: {e}")
        try:
            script_op.par.setuppars.pulse()
        except Exception:
            pass


def _ensure_active(container):
    """Every scene carries an Active toggle (build_all binds it to the decks),
    whether or not it has a cook driver -- the POP scene has none."""
    page = _custom_page(container, "VJ")
    if not hasattr(container.par, "Active"):
        page.appendToggle("Active")
        _setpar(container, "Active", True)
    return page


def _cook_driver(container, sim_op):
    """Make ``sim_op`` cook every frame while the scene's Active flag is on."""
    _ensure_active(container)
    try:
        container.store("simpath", sim_op.path)
    except Exception:
        pass
    drv = _create(container, "executeDAT", "cook_driver", -200, -200)
    drv.text = (
        "# Advances this scene's sim once per frame, only while Active.\n"
        "def onFrameStart(frame):\n"
        "    c = me.parent()\n"
        "    try:\n"
        "        if int(c.par.Active):\n"
        "            target = c.fetch('simpath', '')\n"
        "            if target:\n"
        "                op(target).cook(force=True)\n"
        "    except Exception:\n"
        "        pass\n"
    )
    # The Execute DAT's Frame Start toggle is 'framestart'; 'fs' is kept as a
    # fallback spelling for older builds. Without one of these the driver never
    # fires and hidden scenes (Freerun All) would not advance.
    if not _setpar(drv, "framestart", True):
        _setpar(drv, "fs", True)
    _setpar(drv, "active", True)


def _custom_page(comp, name):
    for pg in comp.customPages:
        if pg.name == name:
            return pg
    return comp.appendCustomPage(name)


MASTER_RES = (1920, 1080)  # the show's output size; every scene's 'out' is this


def _set_res(top, w=MASTER_RES[0], h=MASTER_RES[1], scale_expr=None):
    """Give a TOP an explicit size. resolutionw/h only apply once the TOP's
    Output Resolution mode is 'custom' -- without that the Render TOP sat at
    its 256x256 default and the whole show came out square and blocky.

    ``scale_expr`` makes the size an expression instead: the given fraction of
    (w, h), for a stage that renders below master size and is upscaled after.
    Every write of these two parameters goes through here, so none can happen
    without the mode that makes it count.
    """
    _setpar(top, "outputresolution", "custom")
    for pn, val in (("resolutionw", w), ("resolutionh", h)):
        if scale_expr:
            _bindexpr(top, pn, "int(%d * (%s))" % (val, scale_expr))
        else:
            _setpar(top, pn, val)


def _chan_names(chop, n, base="c"):
    """The names of ``chop``'s first ``n`` channels, read off the cooked op.

    copyNumpyArray numbers channels from 0 on older builds and from 1 on 2025
    (c1..c7), so any hard-coded 'c0' mapping is off by one on one of them --
    which is how positions became (0, x, y) and inferno came out green. Ask
    the CHOP instead; fall back to base0.. only if it has not cooked."""
    try:
        chop.cook(force=True)
    except Exception as e:
        print(f"[td_build] cook of {chop.path} raised: {e}")
    try:
        names = [ch.name for ch in chop.chans()]
    except Exception:
        names = []
    if len(names) >= n:
        return names[:n]
    print(f"[td_build] {chop.path} has {len(names)} channels, expected {n}; "
          f"assuming {base}0..{base}{n - 1}")
    return [f"{base}{i}" for i in range(n)]


def _instance_channels(geo, chop):
    """Point a Geometry COMP's instancing at the 7 channels the sim callbacks
    emit, whatever this build calls them: 0-2 position, 3-5 colour, 6 scale."""
    ch = _chan_names(chop, 7)
    _setpar(geo, "instancing", True)
    _setpar(geo, "instanceop", chop)
    for par, name in (("instancetx", ch[0]), ("instancety", ch[1]), ("instancetz", ch[2]),
                      ("instancesx", ch[6]), ("instancesy", ch[6]), ("instancesz", ch[6]),
                      ("instancer", ch[3]), ("instanceg", ch[4]), ("instanceb", ch[5])):
        _setpar(geo, par, name)
    # Per-instance colour multiplies the MAT colour. Falls back to the MAT
    # colour if the mode name differs on this version -- still looks good.
    _setpar(geo, "instancecolormode", "mult")
    return ch


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
def _soft_mat(container, name="soft_mat", x=-200, y=-180, alpha=0.65):
    """Additive, depth-free Constant MAT: the look for clouds of small points.

    Overlapping instances add up instead of occluding, so a dense cloud reads
    as a soft glow (and the bloom pass finishes the job). The per-instance
    colour multiplies the MAT's white.
    """
    mat = _create(container, "constantMAT", name, x, y)
    for pn, v in (("colorr", 1.0), ("colorg", 1.0), ("colorb", 1.0), ("alpha", alpha)):
        _setpar(mat, pn, v)
    _setpar(mat, "applypointcolor", False)
    _setpar_any(mat, ("blending",), True)
    _setpar_any(mat, ("srcblend", "srcblendmode"), "srcalpha")
    _setpar_any(mat, ("destblend", "dstblend", "destblendmode"), "one")
    _setpar_any(mat, ("depthwrite", "writedepth"), False, quiet=True)
    return mat


def _lit_mat(container, name="lit_mat", x=-200, y=-180, shininess=48.0):
    """A Phong MAT for solid instanced bodies, lit by the 3-point rig: soft
    diffuse, a glossy highlight, a little cool ambient so shadowed sides are
    not pure black. Instance colours multiply the diffuse."""
    mat = _try_create(container, "phongMAT", name, x, y)
    if mat is None:
        return _soft_mat(container, name, x, y, alpha=1.0)
    for pn, v in (("diffr", 0.95), ("diffg", 0.95), ("diffb", 0.95),
                  ("specr", 0.7), ("specg", 0.7), ("specb", 0.7),
                  ("ambr", 0.05), ("ambg", 0.06), ("ambb", 0.10)):
        _setpar(mat, pn, v)
    _setpar_any(mat, ("shininess",), shininess)
    return mat


USE_PBR = True   # PBR MAT under image-based light; False = the Phong rig only


def _pbr_mat(container, name="pbr_mat", x=-200, y=-180, metallic=0.15, roughness=0.32,
             base=(1.0, 1.0, 1.0)):
    """A physically based material: the studio environment reflects in it as
    a real softbox highlight and a cold rim, which is most of what 'ray
    traced' means to the eye. Instance colours multiply the base colour.
    Falls back to the Phong MAT where this build has no PBR MAT."""
    mat = _try_create(container, "pbrMAT", name, x, y) if USE_PBR else None
    if mat is None:
        return _lit_mat(container, name, x, y)
    for pn, v in (("basecolorr", base[0]), ("basecolorg", base[1]), ("basecolorb", base[2])):
        _setpar_any(mat, (pn,), v)
    _setpar_any(mat, ("metallic",), metallic)
    _setpar_any(mat, ("roughness",), roughness)
    _setpar_any(mat, ("specularlevel",), 0.6, quiet=True)
    # A path tracer's giveaway is that a surface keeps a little light where
    # nothing points at it: the environment does that here, and a shade of
    # ambient occlusion keeps the crevices honest.
    _setpar_any(mat, ("ambientocclusionmap", "occlusionmap"), None, quiet=True)
    _setpar_any(mat, ("envlightmapstrength", "environmentstrength"), 1.0, quiet=True)
    _setpar_any(mat, ("shadowstrength",), 0.9, quiet=True)
    return mat


def _studio_env(container, name="env", x=-200, y=480, intensity=1.0):
    """Image-based lighting: a procedural HDR studio (studio_env.frag) on an
    Environment Light COMP. Returns the light, or None if the COMP type is
    missing on this build (the point lights still work without it)."""
    tex = _create(container, "glslTOP", name + "_map", x - 200, y)
    _setpar(tex, "pixeldat", _shader_dat(container, name + "_src", "studio_env.frag", x - 200, y - 130))
    _set_res(tex, 512, 256)
    _setpar_any(tex, ("format",), "rgba16float", quiet=True)
    _glsl_vec4(tex, 0, "uEnv", ("absTime.seconds", intensity, 0.0, 0.0))
    env = _try_create(container, "environmentlightCOMP", name, x, y)
    if env is None:
        return None
    _setpar_any(env, ("envlightmap", "map"), tex)
    _setpar_any(env, ("dimmer",), 1.0, quiet=True)
    return env


def _floor(container, name="floor", x=-260, y=-260, size=60.0, height=-3.2):
    """A dark, slightly glossy stage under a scene: the key's soft shadow lands
    on it and the environment reflects in it -- the contact cue that sells a
    body sitting in real light. Returns the Geometry COMP."""
    geo = _create(container, "geometryCOMP", name, x, y)
    for child in list(geo.children):
        try:
            child.destroy()
        except Exception:
            pass
    grid = geo.create("gridSOP", "plane")
    _setpar_any(grid, ("sizex",), size)
    _setpar_any(grid, ("sizey",), size)
    _setpar_any(grid, ("rows",), 2, quiet=True)
    _setpar_any(grid, ("cols",), 2, quiet=True)
    if _setpar_any(grid, ("orient",), "zx", quiet=True) is None:
        _setpar(geo, "rx", -90.0)          # lay the XY grid flat instead
    try:
        grid.render = grid.display = True
    except Exception:
        pass
    _setpar(geo, "ty", height)
    mat = _pbr_mat(container, name + "_mat", x, y - 130, metallic=0.0, roughness=0.28,
                   base=(0.05, 0.05, 0.06))
    _setpar(geo, "material", mat)
    return geo


def _mean_of(top, frames=3):
    """The mean brightness of a TOP now, or None if it cannot be read."""
    try:
        import numpy as _np
        for _ in range(frames):
            top.cook(force=True)
        arr = top.numpyArray()
        return float(_np.nanmean(arr[..., :3]))
    except Exception:
        return None


def _ensure_visible(container, geo, out, what, x=-260, y=-260):
    """Cook the finished scene once; if it comes out black, swap ``geo``'s
    material for the lit PBR one and say so. The additive soft MAT rendered
    black on one build (both the storm and the hydrogen cloud), and a blank
    scene in a set is worse than a lit one."""
    try:
        import numpy as _np
    except Exception:
        return
    def mean_of(top):
        for _ in range(3):
            top.cook(force=True)
        arr = top.numpyArray()
        return float(_np.nanmean(arr[..., :3]))
    try:
        before = mean_of(out)
    except Exception as e:
        print(f"[td_build] {what}: visibility check unreadable ({e})")
        return
    if before >= 0.002:
        return
    mat = _pbr_mat(container, "lit_fallback_mat", x, y, metallic=0.0, roughness=0.55)
    _setpar(geo, "material", mat)
    try:
        after = mean_of(out)
    except Exception:
        after = float("nan")
    print(f"[td_build] {what}: soft material rendered black (mean {before:.4f}); "
          f"switched to the lit material (mean {after:.4f})")


def _cinematic(container, render, cam, focus, name="cine", x=380, y=200,
               dof=0.55, ao=0.9, haze=0.35, haze_color=(0.02, 0.03, 0.06)):
    """The depth-aware finishing pass (cinema.frag): screen-space ambient
    occlusion, depth of field about a Focus plane and a cold atmospheric haze.
    Switch-gated by a 'Cinema' toggle on the scene, so a shader that will not
    compile on some build leaves the raw render on screen. Returns the TOP to
    continue the chain from (the raw render if there is no Depth TOP)."""
    page = _custom_page(container, "VJ")
    if not hasattr(container.par, "Cinema"):
        page.appendToggle("Cinema", label="Cinema (AO / depth of field / haze)")
        _setpar(container, "Cinema", True)
        page.appendFloat("Focus", label="Focus Distance")
        _setpar(container, "Focus", focus)
        page.appendFloat("Dof", label="Depth of Field")
        _setpar(container, "Dof", dof)
        page.appendFloat("Haze", label="Haze")
        _setpar(container, "Haze", haze)
        try:
            container.par.Focus.normMin, container.par.Focus.normMax = 1.0, 60.0
            container.par.Dof.normMin, container.par.Dof.normMax = 0.0, 1.0
            container.par.Haze.normMin, container.par.Haze.normMax = 0.0, 1.5
        except Exception:
            pass
    depth = _try_create(container, "depthTOP", name + "_depth", x, y - 150)
    if depth is None or _setpar_any(depth, ("renderop", "rendertop", "top"), render) is None:
        print(f"[td_build] {container.path}: no Depth TOP; cinema pass skipped")
        return render
    # Camera space = linear distance; if that menu value is unknown the shader
    # linearises the normalized depth itself from the planes set in _camera.
    linear = _setpar_any(depth, ("depthspace",), "camera", quiet=True) is not None

    cine = _create(container, "glslTOP", name, x, y)
    _setpar(cine, "pixeldat", _shader_dat(container, name + "_src", "cinema.frag", x, y - 300))
    _connect(render, cine, 0)
    _connect(depth, cine, 1)
    _set_res(cine)
    _glsl_vec4(cine, 0, "uCam", (CAM_NEAR, CAM_FAR, "parent().par.Focus", "parent().par.Dof"))
    _glsl_vec4(cine, 1, "uCine", (ao, "parent().par.Haze", "absTime.seconds", 1.0 if linear else 0.0))
    _glsl_vec4(cine, 2, "uFog", (haze_color[0], haze_color[1], haze_color[2], 1.0))

    gate = _create(container, "switchTOP", name + "_gate", x + 160, y)
    _connect(render, gate, 0)     # Cinema off: the raw render, pass not cooked
    _connect(cine, gate, 1)
    _expr(gate, "index", "1 if parent().par.Cinema.eval() else 0")
    _set_res(gate)

    # Prove the pass earns its place: blurring and occluding a sparse scene can
    # cost it most of its light, and a dim scene in a set is a dead slot. If the
    # pass loses more than a third of the frame's brightness (or blacks it out),
    # the toggle starts off -- the controls stay for a manual look.
    raw, done = _mean_of(render), _mean_of(cine)
    if raw is not None and done is not None:
        if done < 0.002 or (raw > 0.002 and done < raw * 0.65):
            _setpar(container, "Cinema", False)
            print(f"[td_build] {container.name}: cinema pass dimmed the frame "
                  f"({raw:.3f} -> {done:.3f}); Cinema defaults off")
        else:
            print(f"[td_build] {container.name}: cinema pass on ({raw:.3f} -> {done:.3f})")
    return gate


# ---------------------------------------------------------------------------
# Reusable visual building blocks
# ---------------------------------------------------------------------------
def _instanced_geo(container, chop, name, base_color, x, y, look="lit", rows=8, cols=12):
    """Geometry COMP that instances a small sphere at each CHOP sample.

    Expects the CHOP channels produced by the nbody/particles callbacks:
    position, colour, scale (whatever this build names them). ``look`` is
    "lit" (Phong under the light rig; solid bodies) or "soft" (additive
    constant; clouds).
    """
    geo = _create(container, "geometryCOMP", name, x, y)
    for child in list(geo.children):
        try:
            child.destroy()
        except Exception:
            pass
    sph = geo.create("sphereSOP", "shape")
    _setpar(sph, "type", "poly")
    # A faceted sphere is the single loudest "real-time" tell, and at 1080p the
    # facets are plain. These counts are cheap next to the instancing.
    _setpar(sph, "rows", max(int(rows), 12))
    _setpar(sph, "cols", max(int(cols), 18))
    try:
        sph.render = True
        sph.display = True
    except Exception:
        pass

    if look == "soft":
        mat = _soft_mat(container, name + "_mat", x, y - 130)
    else:
        mat = _pbr_mat(container, name + "_mat", x, y - 130)
    _setpar(geo, "material", mat)          # assign the OP (TD stores the path)
    _instance_channels(geo, chop)
    return geo, mat


def _line_geo(container, sop, name, x, y):
    """Geometry COMP that renders a Script SOP's coloured polylines."""
    mat = _create(container, "constantMAT", name + "_mat", x, y - 130)
    _setpar(mat, "colorr", 1.0)
    _setpar(mat, "colorg", 1.0)
    _setpar(mat, "colorb", 1.0)
    _setpar(mat, "applypointcolor", True)  # show per-point Cd
    _setpar(sop.parent(), "material", mat)
    return mat


# The show's type: two families and a mono, each with one job. The files are
# bundled (assets/get_fonts.py fetches them from google/fonts), so the look
# does not depend on what happens to be installed on the machine.
FONT_DIR = os.path.join(REPO, "assets", "fonts")
FONTS = {
    "display": ("ArchivoBlack-Regular.ttf", "Archivo Black"),
    "ui": ("Archivo-Variable.ttf", "Archivo"),
    "mono": ("JetBrainsMono-Variable.ttf", "JetBrains Mono"),
}


def _font(top, role="ui", quiet=False):
    """Set a Text TOP's typeface to one of the show's fonts.

    A Text TOP takes either a font *file* or the name of an installed family,
    and which parameter carries which varies by build, so try the file first
    (nothing to install) and fall back to the family name. Returns what took,
    or None -- in which case the report says so and the TOP keeps its default.
    """
    fname, family = FONTS.get(role, FONTS["ui"])
    path = os.path.join(FONT_DIR, fname)
    if os.path.isfile(path):
        for pn in ("fontfile", "file"):
            if _setpar_any(top, (pn,), path, quiet=True) is not None:
                return path
        # Some builds accept a path in the font menu itself.
        if _setpar_any(top, ("font",), path, quiet=True) is not None:
            return path
    elif not quiet:
        print(f"[td_build] font file missing ({path}); run assets/get_fonts.py")
    got = _setpar_any(top, ("font",), family, quiet=True)
    if got is None and not quiet:
        print(f"[td_build] {top.path}: could not set the {role} font "
              f"(tried the file and the family '{family}')")
    return family if got else None


CAM_NEAR, CAM_FAR = 0.5, 200.0    # tight planes: usable depth for the cinema pass


def _camera(container, dist, name="cam", tilt=-12.0, x=-200, y=200):
    cam = _create(container, "cameraCOMP", name, x, y)
    _setpar(cam, "tz", dist)
    _setpar(cam, "ty", dist * 0.12)
    _setpar(cam, "rx", tilt)
    _setpar(cam, "near", CAM_NEAR)
    _setpar(cam, "far", CAM_FAR)
    return cam


def _light(container, name="light", x=-200, y=120):
    light = _create(container, "lightCOMP", name, x, y)
    _setpar(light, "tx", 5.0)
    _setpar(light, "ty", 8.0)
    _setpar(light, "tz", 6.0)
    return light


def _render(container, geo, cam, light, name="render", x=200, y=200,
            w=MASTER_RES[0], h=MASTER_RES[1]):
    """Render TOP. ``light`` may be one Light COMP, a list of them, or None."""
    r = _create(container, "renderTOP", name, x, y)
    _setpar(r, "camera", cam)
    if isinstance(geo, (list, tuple)):
        _setpar(r, "geometry", " ".join(g.name for g in geo))
    else:
        _setpar(r, "geometry", geo)
    if isinstance(light, (list, tuple)):
        _setpar(r, "lights", " ".join(l.name for l in light if l is not None))
    elif light is not None:
        _setpar(r, "lights", light)
    _setpar_any(r, ("antialias",), "aa8", quiet=True)   # smooth silhouettes
    _set_res(r, w, h)
    return r


def _glow(container, src, name="out", size=14.0, x=460, y=200, threshold=0.5):
    """Add a soft, *selective* bloom over solid black and output 'out'.

    A Level TOP raises the black point (``threshold``) so only bright/HDR
    sources feed the blur -- this is the difference between a hazy full-frame
    haze and a glow that picks out the neon highlights. Set threshold=0 to
    bloom everything (the old behaviour)."""
    bright = _create(container, "levelTOP", name + "_bright", x, y - 280)
    _connect(src, bright)
    _setpar(bright, "blacklevel", threshold)   # clamp dim pixels to black
    # Raising the black point of a floating-point input maps anything below it
    # BELOW zero, and a negative feeds straight through the add composite (the
    # RD scene reported a mean of -1.000). Clamp the range here.
    _setpar_any(bright, ("clamp", "clamplow"), True, quiet=True)
    _setpar_any(bright, ("blackclamp",), True, quiet=True)

    blur = _create(container, "blurTOP", name + "_blur", x, y - 150)
    _connect(bright, blur)
    _setpar(blur, "size", size)
    # A single-pass box blur has a hard shoulder that reads as a glow sticker.
    # Several passes of a Gaussian approximate the wide, soft falloff a lens
    # actually has, which is most of what separates bloom from haze.
    _setpar_any(blur, ("filter",), "gaussian", quiet=True)
    _setpar_any(blur, ("passes",), 3, quiet=True)
    _set_res(blur)

    black = _create(container, "constantTOP", name + "_bg", x, y - 300)
    _setpar(black, "colorr", 0.0)
    _setpar(black, "colorg", 0.0)
    _setpar(black, "colorb", 0.0)
    _setpar(black, "alpha", 1.0)
    _set_res(black)        # a Constant TOP is 256x256 unless told otherwise

    comp = _create(container, "compositeTOP", name + "_glow", x + 180, y)
    _setpar(comp, "operand", "add")
    _connect(src, comp, 0)
    _connect(blur, comp, 1)
    _connect(black, comp, 2)
    _set_res(comp)         # never let a smaller input decide the output size

    # The scene's final TOP is an *Out TOP*: it is what gives the scene COMP an
    # output connector, and only that connector can be wired to the deck
    # switches one network up (TD does not wire across COMP boundaries).
    # Intermediate stages (name != "out") stay Null TOPs.
    out = _create(container, "outTOP" if name == "out" else "nullTOP", name, x + 360, y)
    _connect(comp, out)
    _set_res(out)          # every scene leaves at the master size (upscales
    try:                   # the 256^2 Ising lattice / 320^2 RD state cleanly)
        out.viewer = True
    except Exception:
        pass
    return out


def _trails(container, src, name="trail", amount=0.0, x=300, y=200):
    """Insert a feedback motion-trail loop after ``src`` and return its output.

    Builds: a Feedback TOP (previous frame) -> Level (decay) -> Composite that
    draws the live frame *over* a fading copy of the history. A per-scene
    ``Trail`` parameter (0..1) sets how long trails persist; at 0 the history
    contributes nothing, so it's an exact passthrough (no extra look, no cost).
    """
    page = _custom_page(container, "VJ")
    if not hasattr(container.par, "Trail"):
        page.appendFloat("Trail", label="Trail (feedback)")
        _setpar(container, "Trail", amount)
        try:
            container.par.Trail.normMin, container.par.Trail.normMax = 0.0, 0.99
            container.par.Trail.clampMin = container.par.Trail.clampMax = True
        except Exception:
            pass

    # The standard loop: the live frame goes INTO the Feedback TOP (that input
    # is what it shows on reset and, crucially, what sets its resolution -- an
    # unwired Feedback TOP is 256x256 and dragged the whole trail composite
    # down to a square thumbnail), the Feedback's target is the composite.
    fb = _create(container, "feedbackTOP", name + "_fb", x, y - 150)
    _connect(src, fb, 0)
    _set_res(fb)
    decay = _create(container, "levelTOP", name + "_decay", x + 150, y - 150)
    _connect(fb, decay)
    _expr(decay, "opacity", "parent().par.Trail")

    comp = _create(container, "compositeTOP", name, x + 150, y)
    _setpar(comp, "operand", "over")      # live frame over the fading history
    _connect(src, comp, 0)
    _connect(decay, comp, 1)
    _set_res(comp)
    _setpar(fb, "top", comp)              # feed back the composite's last frame
    return comp


def _mass_hud(container, scene_top, x=1040, y=0):
    """Overlay a dimuon invariant-mass spectrum HUD onto ``scene_top``.

    A Script TOP draws the log-mass histogram (with J/psi, Upsilon and Z
    markers and a live marker for the current event); a Text TOP adds the
    title. A ``HUD Opacity`` control fades the whole overlay. Returns the
    final 'out' null TOP (the composited result).
    """
    page = _custom_page(container, "VJ")
    if not hasattr(container.par, "Hudopacity"):
        page.appendFloat("Hudopacity", label="HUD Opacity")
        _setpar(container, "Hudopacity", 1.0)
        try:
            container.par.Hudopacity.normMin, container.par.Hudopacity.normMax = 0.0, 1.0
            container.par.Hudopacity.clampMin = container.par.Hudopacity.clampMax = True
        except Exception:
            pass

    hud = _create(container, "scriptTOP", "mass_hud", x, y + 150)
    _set_res(hud)          # the callback draws at the TOP's own width/height
    _install_callbacks(hud, "mass_hud_top.py")

    title = _create(container, "textTOP", "mass_title", x, y + 320)
    _set_res(title)
    _setpar(title, "text",
            "DIMUON INVARIANT MASS  [GeV]      peaks L>R:  J/psi   Upsilon   Z")
    _font(title, "mono")
    _setpar(title, "fontsizex", 30)
    _setpar(title, "fontsizey", 30)
    _setpar(title, "alignx", "left")
    _setpar(title, "aligny", "top")
    _setpar(title, "fontcolorr", 0.85)
    _setpar(title, "fontcolorg", 0.92)
    _setpar(title, "fontcolorb", 1.0)
    _setpar(title, "fontalpha", 0.9)
    _setpar(title, "bgalpha", 0.0)        # transparent background

    label = _create(container, "compositeTOP", "hud_label", x + 180, y + 150)
    _setpar(label, "operand", "over")
    _connect(title, label, 0)
    _connect(hud, label, 1)
    _set_res(label)

    level = _create(container, "levelTOP", "hud_level", x + 340, y + 150)
    _connect(label, level)
    _expr(level, "opacity", "parent().par.Hudopacity")

    over = _create(container, "compositeTOP", "hud_over", x + 520, y)
    _setpar(over, "operand", "over")
    _connect(level, over, 0)              # HUD on top
    _connect(scene_top, over, 1)          # live scene behind
    _set_res(over)

    out = _create(container, "outTOP", "out", x + 700, y)   # see _glow
    _connect(over, out)
    _set_res(out)
    try:
        out.viewer = True
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Scene builders -- each returns a self-contained COMP with an 'out' TOP.
# ---------------------------------------------------------------------------
def build_ising(dest=None, name="ising"):
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    sim = _create(c, "scriptTOP", "sim", -300, 0)
    _set_res(sim, 256, 256)     # the lattice size; copyNumpyArray resizes to match
    _install_callbacks(sim, "ising_top.py")
    out = _glow(c, sim, size=6.0, x=-40, y=0)
    _cook_driver(c, sim)
    try:
        sim.cook(force=True)
    except Exception:
        pass
    print(f"[td_build] built Ising -> {c.path}")
    return c


def build_nbody(dest=None, name="nbody", palette="inferno"):
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    sim = _create(c, "scriptCHOP", "sim", -500, 0)
    _install_callbacks(sim, "nbody_chop.py")
    _setpar(sim, "Palette", palette)
    # Smooth spheres (only ~600 of them), Phong-lit by the 3-point rig with a
    # soft shadow from the key: reads as solid bodies in a dark room.
    # Only a few hundred bodies, so they can afford a genuinely round
    # silhouette -- at 1080p a 14x20 sphere still shows its facets.
    geo, mat = _instanced_geo(c, sim, "geo", (1.0, 0.55, 0.15), -260, 0,
                              look="lit", rows=24, cols=36)
    # Nothing lights a body except the beam: no ambient term, no fill, no
    # environment. A body outside the pool of light is simply not there.
    for pn in ("ambr", "ambg", "ambb"):
        _setpar_any(mat, (pn,), 0.0, quiet=True)
    _setpar_any(mat, ("roughness",), 0.38, quiet=True)
    _setpar_any(mat, ("metallic",), 0.05, quiet=True)

    # A second pass of the same bodies, larger and additive: a luminous shell
    # around each lit core. An opaque shaded sphere is the plastic look however
    # good the material is -- a mass under a beam should also *emit*, and the
    # halation in the film stock then does what a lens does with it. The shell
    # ignores lights (constant MAT), so it reads through the dark side too.
    halo, _ = _instanced_geo(c, sim, "halo", (1.0, 0.62, 0.28), -260, -220,
                             look="soft", rows=10, cols=14)
    _setpar_any(halo, ("scale",), 2.6, quiet=True)
    _orbit(c, geo, default=7.0)
    _expr(halo, "ry", "op('geo').par.ry")      # the shell turns with the bodies
    cam = _camera(c, dist=26.0)
    lights = _spot_rig(c, dest_reactor(dest), cone=17.0, delta=30.0,
                       pos=(11.0, 13.0, 8.0), dimmer=14.0)
    # Bodies shadow each other in the beam: the cue that says "rendered".
    for L in lights:
        _setpar_any(L, ("shadowtype",), "soft", quiet=True)
        _setpar_any(L, ("shadowquality",), "high", quiet=True)
    r = _render(c, [geo, halo], cam, lights)
    cine = _cinematic(c, r, cam, focus=26.0, dof=0.45, ao=0.9, haze=0.3)
    tr = _trails(c, cine, amount=0.7)
    out = _glow(c, tr, size=18.0, x=640, threshold=0.35)
    _cook_driver(c, sim)
    try:
        sim.cook(force=True)
    except Exception:
        pass
    print(f"[td_build] built N-Body -> {c.path}")
    return c


def build_particles(dest=None, name="particles", mode="flow", palette="cyber"):
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    sim = _create(c, "scriptCHOP", "sim", -500, 0)
    _install_callbacks(sim, "particles_chop.py")
    _setpar(sim, "Mode", mode)
    _setpar(sim, "Palette", palette)
    base_col = (0.1, 0.8, 1.0) if mode == "flow" else (1.0, 0.2, 0.9)
    if mode == "softbody":
        geo, _ = _instanced_geo(c, sim, "geo", base_col, -260, 0, look="lit", rows=8, cols=12)
    else:
        geo, _ = _instanced_geo(c, sim, "geo", base_col, -260, 0, look="lit", rows=5, cols=8)
    _orbit(c, geo, default=10.0 if mode == "softbody" else 4.0)
    dist = 10.0 if mode == "softbody" else 15.0
    cam = _camera(c, dist=dist)
    lights = _light_rig(c, dest_reactor(dest), shadows=True)
    # The soft body sits on a dark stage so the key's shadow has somewhere to
    # land; the flow fills the volume and wants no floor.
    scene_geo = [geo, _floor(c, height=-3.4)] if mode == "softbody" else geo
    r = _render(c, scene_geo, cam, lights)
    cine = _cinematic(c, r, cam, focus=dist, dof=0.6 if mode == "softbody" else 0.4,
                      ao=1.0 if mode == "softbody" else 0.7, haze=0.3)
    tr = _trails(c, cine, amount=0.88 if mode == "flow" else 0.75)
    out = _glow(c, tr, size=18.0, x=640, threshold=0.35)
    _cook_driver(c, sim)
    try:
        sim.cook(force=True)
    except Exception:
        pass
    print(f"[td_build] built Particles ({mode}) -> {c.path}")
    return c


def dest_reactor(dest):
    """The Reactor COMP beside the scenes being built, if build_all made one."""
    try:
        return dest.op("Reactor") if dest is not None else None
    except Exception:
        return None


def build_lhc(dest=None, name="lhc"):
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    geo = _create(c, "geometryCOMP", "geo", -260, 0)
    for child in list(geo.children):
        try:
            child.destroy()
        except Exception:
            pass
    sim = geo.create("scriptSOP", "sim")
    try:
        sim.render = True
        sim.display = True
    except Exception:
        pass
    _install_callbacks(sim, "lhc_sop.py")
    _line_geo(c, sim, "geo", -260, 0)
    _orbit(c, geo, default=9.0)
    # The detector readout: dog-leg leaders and vector-font labels pinned to
    # the highest-momentum tracks. It lives in its own Geometry COMP which is
    # deliberately NOT orbited, so the labels stay upright and square to the
    # camera while the event turns underneath; the callback rotates the
    # anchors by the same angle so each leader stays on its track.
    readout = _create(c, "geometryCOMP", "readout", -260, -200)
    for child in list(readout.children):
        try:
            child.destroy()
        except Exception:
            pass
    hud_sop = readout.create("scriptSOP", "hud")
    try:
        hud_sop.render = hud_sop.display = True
    except Exception:
        pass
    _install_callbacks(hud_sop, "lhc_hud_sop.py")
    _line_geo(c, hud_sop, "readout", -260, -200)
    cam = _camera(c, dist=12.0, tilt=-8.0)
    # Line art gets no cinema pass: ambient occlusion and a lens blur spread a
    # one-pixel track over nine and darken it to nothing (this scene measured
    # exactly black with the pass in). Depth here comes from the tracks' own
    # colour and the bloom.
    r = _render(c, [geo, readout], cam, None)
    tr = _trails(c, r, amount=0.0)
    out = _glow(c, tr, size=12.0, x=640)
    _cook_driver(c, sim)
    try:
        sim.cook(force=True)
        hud_sop.cook(force=True)
    except Exception:
        pass
    # One driver per scene, so the readout is pulled by the same frame-start
    # hook: it follows the tracks only if it re-cooks every frame.
    drv = c.op("cook_driver")
    if drv is not None:
        drv.text = drv.text.replace(
            "                op(target).cook(force=True)\n",
            "                op(target).cook(force=True)\n"
            "                extra = c.op('readout/hud')\n"
            "                if extra is not None:\n"
            "                    extra.cook(force=True)\n")
    print(f"[td_build] built LHC Tracks -> {c.path}")
    return c


def _cd_channel_names(probe):
    """The channel names a SOP to CHOP gives the Cd attribute on this build,
    as one space-separated string in component order (r g b [a]).

    Falls back to the four names the 2025 build reports if the probe yields
    nothing (e.g. the SOP had no Cd yet) so the wiring is still well-formed.
    """
    names = []
    try:
        probe.cook(force=True)
        names = [ch.name for ch in probe.chans() if ch.name.lower().startswith("cd")]
    except Exception as e:
        print(f"[td_build] Cd channel-name probe failed: {e}")
    if len(names) < 3:
        print(f"[td_build] Cd channel-name probe found {names!r}; using Cd_0_..Cd_3_ "
              "(the CHOP to SOP accepts these: it fills the attribute's components "
              "from the scoped channels in order)")
        _print_pars(probe)
        names = ["Cd_0_", "Cd_1_", "Cd_2_", "Cd_3_"]
    return " ".join(names[:4])


def build_feynman(dest=None, name="feynman"):
    """The Feynman field: static linework, animated by point colour.

    The one scene here whose geometry does not move. The field is built once
    by the Script SOP; the Script CHOP puts out one colour and alpha per point
    each frame and a CHOP to SOP lands them on the geometry, which is what
    keeps twelve thousand points at frame rate. If a TouchDesigner version
    names the CHOP to SOP's scope parameters differently and the field comes
    out flat white, docs/ARCHITECTURE.md says which two to set by hand.
    """
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    geo = _create(c, "geometryCOMP", "geo", -260, 0)
    for child in list(geo.children):
        try:
            child.destroy()
        except Exception:
            pass

    lines = geo.create("scriptSOP", "lines")
    _install_callbacks(lines, "feynman_sop.py")

    state = _create(c, "scriptCHOP", "state", -560, -180)
    _install_callbacks(state, "feynman_chop.py")
    _setpar(state, "Geosop", "geo/lines")   # relative to the scene, from a CHOP

    # A CHOP to SOP matches channels to attributes *by name*, and the exact
    # names are the build's business: its warnings print them as Cd(0)..Cd(3)
    # but channel names cannot hold parentheses (a Rename CHOP legalises them
    # to Cd_0_). So instead of assuming, ask TD: a SOP to CHOP reading the
    # field's own Cd attribute emits precisely the names this build uses, and
    # the Rename CHOP gives the Script CHOP's channels those names. (Script
    # CHOP channels are numbered from 1 on 2025 -- c1..c4 -- hence 'c*'.)
    try:
        lines.cook(force=True)
    except Exception:
        pass
    probe = _create(c, "soptoCHOP", "cd_names", -560, -300)
    _setpar(probe, "sop", lines)
    _setpar_any(probe, ("attscope", "attribs", "attribute"), "Cd", quiet=True)
    cd_names = _cd_channel_names(probe)

    named = _create(c, "renameCHOP", "state_cd", -400, -180)
    _connect(state, named)
    _setpar_any(named, ("renamefrom", "from"), "c*")
    _setpar_any(named, ("renameto", "to"), cd_names)

    paint = geo.create("choptoSOP", "paint")
    _connect(lines, paint)
    _setpar(paint, "chop", "../state_cd")
    _setpar(paint, "chanscope", cd_names)
    _setpar(paint, "attscope", "Cd")
    try:
        paint.render = True
        paint.display = True
        lines.render = False
        lines.display = False
    except Exception:
        pass

    _line_geo(c, paint, "geo", -260, 0)
    _orbit(c, geo, default=0.0)          # the field is flat: no spin by default
    cam = _camera(c, dist=11.0, tilt=0.0)
    r = _render(c, geo, cam, None)
    tr = _trails(c, r, amount=0.0)
    _glow(c, tr, size=10.0, x=640)
    _cook_driver(c, state)               # the CHOP is what has to cook per frame
    # Cook in dependency order so the build report reflects the wired state:
    # geometry, then the colour channels, then the CHOP to SOP that joins them
    # (cooked earlier, before state had any channels, it reports "Channel *
    # not found" for that stale pass).
    for o in (lines, probe, state, named, paint):
        try:
            o.cook(force=True)
        except Exception as e:
            print(f"[td_build] cook of {o.path} raised: {e}")
    # What the CHOP to SOP actually sees, for the build report: the two CHOPs'
    # channel names and the path it resolves. If the field renders white this
    # is the line that says why.
    try:
        def chans(o):
            return " ".join(ch.name for ch in o.chans()) or "(no channels)"
        print(f"[td_build] feynman join: Cd channels on this build = {chans(probe)}; "
              f"state[{state.numChans}] = {chans(state)}; "
              f"state_cd[{named.numChans}] = {chans(named)}; "
              f"paint.chop -> {paint.par.chop.eval()}; "
              f"paint warnings: {paint.warnings() or 'none'}")
    except Exception as e:
        print(f"[td_build] feynman join diagnostics failed: {e}")
    print(f"[td_build] built Feynman -> {c.path}")
    return c


def build_opendata(dest=None, name="opendata"):
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    geo = _create(c, "geometryCOMP", "geo", -260, 0)
    for child in list(geo.children):
        try:
            child.destroy()
        except Exception:
            pass
    sim = geo.create("scriptSOP", "sim")
    try:
        sim.render = True
        sim.display = True
    except Exception:
        pass
    _install_callbacks(sim, "opendata_sop.py")
    _line_geo(c, sim, "geo", -260, 0)
    _orbit(c, geo, default=6.0)
    cam = _camera(c, dist=12.0, tilt=-8.0)
    r = _render(c, geo, cam, None)      # line art: no cinema pass (see build_lhc)
    tr = _trails(c, r, amount=0.0)
    scene = _glow(c, tr, size=10.0, name="scene", x=640)
    out = _mass_hud(c, scene)
    _cook_driver(c, sim)
    try:
        sim.cook(force=True)
    except Exception:
        pass
    print(f"[td_build] built Open Data -> {c.path}")
    return c


def build_apc(dest=None, target=None, name="APCShow", device=1):
    """Build an Akai APC mini mk2 control surface for a PhysicsVJ component.

    Creates a self-contained ``APCShow`` COMP that maps the APC's pads, buttons
    and faders to the whole show (scene cuts, palette launches, the A/B
    crossfader, freerun, per-scene re-fire) and drives the controller's RGB LEDs
    to mirror the live state. Includes a ``Reset`` mechanism (and a hardware
    Reset button) that re-handshakes the surface and repaints every LED -- use
    it whenever the APC powers on dark or its lights drift out of sync.

    ``target`` is the PhysicsVJ COMP (or its path). If omitted, it points at a
    sibling named ``PhysicsVJ``. ``device`` is the MIDI Device Mapper id that
    your APC is mapped to (set the same id in TD's MIDI Device Mapper dialog).
    """
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)

    # Anchor Script CHOP: hosts onSetupParameters (builds the custom page) and
    # is harmless to cook. The real behaviour lives in the apc_mini module and
    # the two event DATs below.
    anchor = _create(c, "scriptCHOP", "controller", -300, 0)
    _install_callbacks(anchor, "apc_mini.py")

    target_path = "../PhysicsVJ"
    if target is not None:
        target_path = target if isinstance(target, str) else target.path
    elif dest.op("PhysicsVJ") is not None:
        target_path = dest.op("PhysicsVJ").path
    _setpar(c, "Target", target_path)
    _setpar(c, "Device", device)

    # MIDI input from the APC -> note/CC handling.
    # 'id' is the Device ID from TD's MIDI Device Mapper. ('device' on these
    # ops is the Device *Table* DAT path -- setting it to a number left an
    # "Invalid path for node" warning on the MIDI Out CHOP.)
    midiin = _create(c, "midiinDAT", "midiin", -300, 200)
    _setpar(midiin, "id", device)
    in_cb = _create(c, "textDAT", "midiin_callbacks", -300, 330)
    in_cb.text = (
        "import sys\n"
        f"sys.path.insert(0, r\"{REPO}\")\n"
        "from touchdesigner.callbacks import apc_mini\n\n"
        "def onReceiveMIDI(dat, rowIndex, message, channel, index, value, input, bytes):\n"
        "    try:\n"
        "        apc_mini.on_midi(dat.parent(), message, channel, index, value)\n"
        "    except Exception as e:\n"
        "        debug('[apc] midi', e)\n"
        "    return\n"
    )
    _setpar(midiin, "callbacks", in_cb)
    _setpar(midiin, "active", True)

    # LED output back to the APC (Note On with behaviour-selecting channel).
    ledout = _create(c, "midioutCHOP", "ledout", -100, 200)
    _setpar(ledout, "id", device)

    # Watch the show + the surface's own pars; repaint LEDs / handle Reset.
    watch = _create(c, "parameterexecuteDAT", "statewatch", 100, 200)
    _setpar(watch, "op", target_path)
    _setpar(watch, "pars", "Scene Nextscene Crossfade Freerunall Freeze Blackout " + " ".join(FX_NAMES))
    _setpar(watch, "valuechange", True)
    _setpar(watch, "active", True)
    watch.text = (
        "import sys\n"
        f"sys.path.insert(0, r\"{REPO}\")\n"
        "from touchdesigner.callbacks import apc_mini\n\n"
        "def onValueChange(par, prev):\n"
        "    try:\n"
        "        apc_mini.repaint(me.parent())\n"
        "    except Exception as e:\n"
        "        debug('[apc] repaint', e)\n"
    )

    # Catch the surface's own Reset pulse (its pars live on this COMP).
    selfwatch = _create(c, "parameterexecuteDAT", "selfwatch", 100, 330)
    _setpar(selfwatch, "op", c)
    _setpar(selfwatch, "pars", "Reset Device")
    _setpar(selfwatch, "onpulse", True)
    _setpar(selfwatch, "valuechange", True)
    _setpar(selfwatch, "active", True)
    selfwatch.text = (
        "import sys\n"
        f"sys.path.insert(0, r\"{REPO}\")\n"
        "from touchdesigner.callbacks import apc_mini\n\n"
        "def onPulse(par):\n"
        "    try:\n"
        "        apc_mini.reset(par.owner)\n"
        "    except Exception as e:\n"
        "        debug('[apc] reset', e)\n\n"
        "def onValueChange(par, prev):\n"
        "    # Re-route MIDI ops if the device id changes, then resync.\n"
        "    try:\n"
        "        c = par.owner\n"
        "        for nm in ('midiin', 'ledout'):\n"
        "            o = c.op(nm)\n"
        "            if o is not None and hasattr(o.par, 'id'):\n"
        "                o.par.id = par.eval()\n"
        "        apc_mini.reset(c)\n"
        "    except Exception as e:\n"
        "        debug('[apc] device', e)\n"
    )

    # Per-frame LED driver: the audio-reactive layer (live pad breathing,
    # PULSE on kicks, TAP on the tempo, FX pads on the beat) and the press
    # animations. By-difference sending keeps it to a few MIDI bytes a frame.
    ticker = _create(c, "executeDAT", "ticker", 300, 200)
    ticker.text = (
        "import sys\n"
        f"sys.path.insert(0, r\"{REPO}\")\n"
        "from touchdesigner.callbacks import apc_mini\n\n"
        "def onFrameStart(frame):\n"
        "    try:\n"
        "        apc_mini.tick(me.parent())\n"
        "    except Exception as e:\n"
        "        if frame % 300 == 0:\n"
        "            debug('[apc] tick', e)\n"
    )
    if not _setpar(ticker, "framestart", True):
        _setpar(ticker, "fs", True)
    _setpar(ticker, "active", True)

    # Paint the initial LED state now.
    try:
        from touchdesigner.callbacks import apc_mini
        apc_mini.reset(c)
    except Exception as e:
        print(f"[td_build] initial APC repaint skipped: {e}")

    print(f"[td_build] built APC mini mk2 surface -> {c.path} (target {target_path})")
    print("[td_build] In TD's MIDI Device Mapper, map your APC mini mk2 to "
          f"device id {device}. Shift + the first track button (or the Reset "
          "par) resyncs the LEDs.")
    return c


def _orbit(container, geo, default=8.0):
    """Add an 'Orbit' (deg/sec) control and slowly rotate the geometry."""
    page = _custom_page(container, "VJ")
    if not hasattr(container.par, "Orbit"):
        page.appendFloat("Orbit")
        _setpar(container, "Orbit", default)
        try:
            container.par.Orbit.normMin, container.par.Orbit.normMax = -45, 45
        except Exception:
            pass
    _expr(geo, "ry", "(parent().par.Orbit.eval() * absTime.seconds) % 360")


# ---------------------------------------------------------------------------
# Master build: every scene + a live switcher
# ---------------------------------------------------------------------------
def _map_size(default=(822, 966)):
    """The APC map picture's size, from the JSON tools/apc_map.py writes."""
    try:
        import json
        with open(os.path.join(REPO, "docs", "apc_map.json")) as fh:
            geo = json.load(fh)
        return int(geo["width"]), int(geo["height"])
    except Exception:
        return default


def build_dashboard(dest=None, target=None, name="Dashboard", monitor=1):
    """An operator's view at the top level: the show as it goes out, beside the
    APC map.

    Left, two thirds of the frame: exactly what the second display is being
    sent -- the same TOP the Window COMP renders, so what you are watching is
    the program feed and not a second render of it. Right: the control map,
    as a picture, so a pad you have forgotten is a glance away rather than a
    scroll through the README.

    Also creates the Window COMP that puts the show on ``monitor``. Open it
    from the COMP's own Open pulse (or its Perform-mode setting).
    """
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    target_path = "../PhysicsVJ"
    if target is not None:
        target_path = target if isinstance(target, str) else target.path
    elif dest.op("PhysicsVJ") is not None:
        target_path = dest.op("PhysicsVJ").path

    page = _custom_page(c, "Dashboard")
    page.appendFloat("Split", label="Program / Map Split")[0].val = 0.66
    _setpar(c, "Split", 0.66)
    try:
        c.par.Split.normMin, c.par.Split.normMax = 0.4, 0.9
    except Exception:
        pass
    page.appendToggle("Showmap", label="Show APC Map")[0].val = True
    page.appendPulse("Openwindow", label="Open Program Window (full screen)")
    page.appendPulse("Closewindow", label="Close Program Window")

    # The program feed, fetched across the COMP boundary with a Select TOP.
    prog = _create(c, "selectTOP", "program", -300, 0)
    _setpar(prog, "top", target_path + "/out")
    _set_res(prog)

    # The window that goes to the other display, fed by the same TOP.
    win = _try_create(dest, "windowCOMP", "program_window", 200, -200)
    if win is not None:
        _setpar_any(win, ("opcomp", "operator", "top"), target_path + "/out")
        # Full screen on one display: 'monitor' sizing fills the chosen screen,
        # so the show needs no width or height of its own. Names vary by build,
        # hence the spellings; each logs if it misses.
        _setpar_any(win, ("monitor", "whichmonitor", "displayindex"), monitor)
        _setpar_any(win, ("winsizemode", "sizemode", "size"), "monitor", quiet=True)
        _setpar_any(win, ("fullscreen",), True, quiet=True)
        _setpar_any(win, ("borders",), False, quiet=True)
        _setpar_any(win, ("cursorvisible",), False, quiet=True)
        _setpar_any(win, ("alwaysontop",), True, quiet=True)
        # If this build sizes windows only by number, fill the display anyway.
        _setpar_any(win, ("winw", "width"), MASTER_RES[0], quiet=True)
        _setpar_any(win, ("winh", "height"), MASTER_RES[1], quiet=True)
        _setpar_any(win, ("winoffsetx", "offsetx"), 0, quiet=True)
        _setpar_any(win, ("winoffsety", "offsety"), 0, quiet=True)
        opener = _create(c, "parameterexecuteDAT", "opener", -300, -300)
        _setpar(opener, "op", c)
        _setpar(opener, "pars", "Openwindow")
        _setpar(opener, "onpulse", True)
        _setpar(opener, "active", True)
        _setpar(opener, "pars", "Openwindow Closewindow")
        opener.text = (
            "def onPulse(par):\n"
            f"    w = op('{win.path}')\n"
            "    names = ('winopen', 'open') if par.name == 'Openwindow' \\\n"
            "            else ('winclose', 'close')\n"
            "    for pn in names:\n"
            "        try:\n"
            "            getattr(w.par, pn).pulse()\n"
            "            return\n"
            "        except Exception:\n"
            "            continue\n"
            "    debug('[td] window has no', names, 'pulse')\n"
        )

    # The cheatsheet: the map, rasterised beside the SVG by tools/apc_map.py,
    # with the surface's real LED state painted over it. The overlay reads
    # apc_mini's own send cache, so the panel and the hardware cannot disagree.
    png = os.path.join(REPO, "docs", "apc_map.png")
    sheet = _create(c, "moviefileinTOP", "apc_map", -300, -160)
    _setpar_any(sheet, ("file",), png)
    if not os.path.isfile(png):
        print(f"[td_build] APC map image missing ({png}); run tools/apc_map.py")

    live = _create(c, "scriptTOP", "apc_live", -300, -300)
    _install_callbacks(live, "apc_live_top.py")
    _setpar(live, "Target", target_path)
    apc_comp = dest.op("APCShow")
    _setpar(live, "Surface", apc_comp.path if apc_comp is not None else "/APCShow")
    lit = _create(c, "compositeTOP", "apc_lit", -140, -160)
    _setpar(lit, "operand", "over")
    _connect(live, lit, 0)          # the live pads over the printed labels
    _connect(sheet, lit, 1)
    # This one is pinned to the *map's* size, not the master's: the overlay and
    # the picture have to be the same shape or the pads land off the print.
    mw, mh = _map_size()
    _set_res(lit, mw, mh)
    sheet = lit

    # Lay the two panels onto one frame. A Transform TOP scales and shifts each
    # into its own column; a Composite stacks them over a black ground.
    def panel(src, nm, scale_expr, tx_expr, x, y):
        t = _create(c, "transformTOP", nm, x, y)
        _connect(src, t)
        _set_res(t)
        _setpar_any(t, ("extend", "extendleft"), "black", quiet=True)
        for pn, ex in ((("s1", "scalex"), scale_expr), (("s2", "scaley"), scale_expr),
                       (("t1", "translatex"), tx_expr)):
            if isinstance(ex, str):
                _bindexpr_any(t, pn, ex)
            else:
                _setpar_any(t, pn, ex)
        return t

    # The program keeps its aspect: it is scaled by the split and pushed left.
    prog_t = panel(prog, "program_fit", "parent().par.Split",
                   "-(1.0 - parent().par.Split.eval()) / 2.0", -100, 0)
    # The map fills the remaining column, fitted by width and pushed right.
    map_t = panel(sheet, "map_fit", "1.0 - parent().par.Split.eval()",
                  "parent().par.Split.eval() / 2.0", -100, -160)

    label = _create(c, "textTOP", "labels", -100, -320)
    _set_res(label)
    _setpar_any(label, ("text",), "PROGRAM  >  DISPLAY %d" % monitor, quiet=True)
    _font(label, "mono")
    _setpar_any(label, ("fontsizex", "fontsize"), 26, quiet=True)
    _setpar_any(label, ("fontsizey",), 26, quiet=True)
    _setpar_any(label, ("alignx",), "left", quiet=True)
    _setpar_any(label, ("aligny",), "top", quiet=True)
    _setpar_any(label, ("bgalpha",), 0.0, quiet=True)
    for pn in ("fontcolorr", "fontcolorg", "fontcolorb"):
        _setpar_any(label, (pn,), 0.75, quiet=True)

    stack = _create(c, "compositeTOP", "stack", 120, 0)
    _setpar(stack, "operand", "over")
    _connect(label, stack, 0)
    _connect(map_t, stack, 1)
    _connect(prog_t, stack, 2)
    _set_res(stack)

    gate = _create(c, "switchTOP", "map_gate", 300, 0)
    _connect(prog, gate, 0)          # map off: the program feed, full frame
    _connect(stack, gate, 1)
    _expr(gate, "index", "1 if parent().par.Showmap.eval() else 0")
    _set_res(gate)

    out = _create(c, "outTOP", "out", 480, 0)
    _connect(gate, out)
    _set_res(out)
    try:
        out.viewer = True
    except Exception:
        pass

    # Two panel containers, for the panes the layout puts on the right.
    #
    # Why these exist at all: a Panel pane can only show a COMP that *has* a
    # panel, and PhysicsVJ is a Base COMP, which has none -- point a pane at it
    # and you get an empty grey rectangle. A Container COMP does have one, and
    # draws a TOP as its background, so these two are the things to point a
    # pane at. (The other way round works too: a panel's own TOP field wants a
    # TOP, so PhysicsVJ/out rather than PhysicsVJ.)
    panels = {}
    for nm, src, label in (("program_panel", prog, "program"),
                           ("apc_panel", sheet, "APC")):
        pan = _try_create(c, "containerCOMP", nm, 480, -200 if nm[0] == "a" else -60)
        if pan is None:
            continue
        if _setpar_any(pan, ("top", "background", "bgtop"), src) is None:
            print(f"[td_build] {pan.path}: could not set its background TOP")
        _setpar_any(pan, ("opacity",), 1.0, quiet=True)
        _setpar_any(pan, ("display",), True, quiet=True)
        _setpar_any(pan, ("topsmoothness",), "mipmaplinear", quiet=True)
        # Fill the pane rather than letting the panel letterbox itself.
        _setpar_any(pan, ("aspect", "aspectratio"), 0, quiet=True)
        _setpar_any(pan, ("hmode", "horzmode"), "fill", quiet=True)
        _setpar_any(pan, ("vmode", "vertmode"), "fill", quiet=True)
        _setpar_any(pan, ("w", "width"), MASTER_RES[0] // 2, quiet=True)
        _setpar_any(pan, ("h", "height"), MASTER_RES[1] // 2, quiet=True)
        panels[label] = pan
    try:
        c.store("panels", {k: v.path for k, v in panels.items()})
    except Exception:
        pass
    for o in (prog, sheet, prog_t, map_t, stack, gate, out):
        try:
            o.cook(force=True)
            err = o.errors()
            if err:
                print(f"[td_build] {o.path}: {err.strip()}")
        except Exception as e:
            print(f"[td_build] cook of {o.path} raised: {e}")
    print(f"[td_build] built dashboard -> {c.path} (view 'out'; program feed "
          f"{target_path}/out, window on monitor {monitor})")
    # Say plainly what to point a pane at. PhysicsVJ is a Base COMP with no
    # panel of its own, so a Panel pane aimed at it comes up grey -- which is
    # a confusing five minutes unless the build says so.
    print(f"[td_build] to watch the show in a pane: set a Panel pane's owner to "
          f"{panels['program'].path if 'program' in panels else c.path + '/program_panel'}"
          f" (or a panel's TOP to {target_path}/out -- not {target_path}, "
          f"which is a Base COMP and has no panel)")
    if "APC" in panels:
        print(f"[td_build] the live APC panel is {panels['APC'].path}")

    # The working layout: network on the left, the program upper right, the
    # APC lower right. Guarded -- a build must not fail over a window layout.
    try:
        from touchdesigner import layout
        layout.three_panel(program=panels.get("program"), apc=panels.get("APC"))
    except Exception as e:
        print(f"[td_build] pane layout skipped: {e}")
    return c


def build_all(dest=None, name="PhysicsVJ", apc=True):
    dest = dest or op("/")  # noqa: F821
    base = _create(dest, "baseCOMP", name)

    # Top-level controls: an A/B deck pair + crossfader (DJ style).
    #   Scene      -> deck A (the live cut / instant switch)
    #   Nextscene  -> deck B (what you crossfade toward)
    #   Crossfade  -> 0 = all A, 1 = all B
    # A hard cut is just snapping Crossfade; a smooth blend is riding it.
    menu_names = [s[1].replace("build_", "") + str(i) for i, s in enumerate(SCENES)]
    labels = [s[0] for s in SCENES]
    # Top-level controls. Wrapped so a parameter-API quirk on some TD build
    # degrades (and is reported) instead of aborting the entire show build.
    try:
        page = base.appendCustomPage("PhysicsVJ")
        for parname, deflt in (("Scene", 0), ("Nextscene", 1)):
            mp = page.appendMenu(parname)[0]
            mp.menuNames = menu_names
            mp.menuLabels = labels
            mp.val = menu_names[deflt]
        cf = page.appendFloat("Crossfade", label="Crossfade A/B")[0]
        cf.val = 0.0
        base.par.Crossfade.normMin, base.par.Crossfade.normMax = 0.0, 1.0
        base.par.Crossfade.clampMin = base.par.Crossfade.clampMax = True
        page.appendPulse("Cut", label="Cut To B (commit)")
        page.appendToggle("Freerunall", label="Freerun All (evolve hidden scenes)")
        _setpar(base, "Freerunall", False)
        page.appendToggle("Clickpunch", label="Click / Space = Punch live scene")
        _setpar(base, "Clickpunch", True)
        page.appendPulse("Punch", label="Punch live scene")
        page.appendToggle("Freeze", label="Freeze (stop every sim)")
        _setpar(base, "Freeze", False)
        page.appendToggle("Blackout", label="Blackout")
        _setpar(base, "Blackout", False)
        page.appendPulse("Title", label="Show Scene Title")
        page.appendFloat("Titlehold", label="Title Hold (s)")[0].val = 4.0
        page.appendToggle("Reactive", label="Audio Reactive")
        _setpar(base, "Reactive", True)
        ra = page.appendFloat("Reactamount", label="Reactive Amount")[0]
        ra.val = 0.6
        base.par.Reactamount.normMin, base.par.Reactamount.normMax = 0.0, 1.0
    except Exception as e:
        print(f"[td_build] top-level control page setup hit a snag: {e}")

    # Whole-screen FX toggles (the APC's upper-right quadrant).
    try:
        fxq = base.appendCustomPage("FX")
        for fx in FX_NAMES:
            fxq.appendToggle(fx, label=fx)
            _setpar(base, fx, False)
    except Exception as e:
        print(f"[td_build] FX page setup hit a snag: {e}")

    # Audio + tempo engines, built first so scenes/shaders can bind to them.
    reactor = build_reactor(dest=base)
    # The show's Reactive switch / amount drive the Reactor's Depth: off = the
    # visuals ignore the music entirely, otherwise every reactive channel is
    # scaled (0.6 by default: subtle, not a light show).
    try:
        _bindexpr(reactor.op("analyze"), "Depth",
                  "parent(2).par.Reactamount.eval() if parent(2).par.Reactive.eval() else 0.0")
    except Exception as e:
        print(f"[td_build] Reactive binding skipped: {e}")
    reactor.nodeX, reactor.nodeY = -600, 460
    tempo = build_tempo(dest=base)
    tempo.nodeX, tempo.nodeY = -600, 360

    # Master FX controls (drive the GLSL post chain).
    fxpage = base.appendCustomPage("Look")
    fxpage.appendFloat("Kaleido", label="Kaleidoscope")[0].val = 0.0
    fxpage.appendFloat("Rgbshift", label="RGB Shift (px)")[0].val = 1.5
    fxpage.appendFloat("Punch", label="Beat Punch")[0].val = 1.0
    fxpage.appendToggle("Wavevis", label="Waveform Overlay")[0].val = False
    fxpage.appendToggle("Fx", label="Post FX (off = raw mix)")[0].val = True
    fxpage.appendToggle("Darkmode", label="Dark Mode (crushed blacks, low exposure)")[0].val = True
    fxpage.appendFloat("Exposure", label="Exposure")[0].val = 0.85
    try:
        base.par.Exposure.normMin, base.par.Exposure.normMax = 0.3, 2.0
    except Exception:
        pass
    for pn, mx in (("Kaleido", 1.0), ("Rgbshift", 8.0), ("Punch", 2.0)):
        try:
            getattr(base.par, pn).normMin = 0.0
            getattr(base.par, pn).normMax = mx
        except Exception:
            pass

    # A small DAT to commit a transition: Cut copies B->A and resets the fader.
    cutter = _create(base, "parameterexecuteDAT", "cutter", -200, -360)
    try:
        _setpar(cutter, "op", base)
        _setpar(cutter, "pars", "Cut")
        _setpar(cutter, "valuechange", False)
        _setpar(cutter, "onpulse", True)
        _setpar(cutter, "active", True)
    except Exception:
        pass
    cutter.text = (
        "def onPulse(par):\n"
        "    c = par.owner\n"
        "    c.par.Scene = c.par.Nextscene.eval()\n"
        "    c.par.Crossfade = 0\n"
    )

    outs = []
    scene_names = []
    for i, (label, builder, kwargs) in enumerate(SCENES):
        scene = globals()[builder](dest=base, **kwargs)
        scene_names.append(scene.name)
        scene.nodeX, scene.nodeY = -600, 260 - i * 170
        # Cook a scene only while its deck actually contributes to the mix
        # (deck A unless fully faded to B, deck B unless fully faded to A),
        # or when Freerun All is on. Keeps it to one live sim except mid-fade.
        _expr(
            scene, "Active",
            f"int(not parent().par.Freeze.eval() and "
            f"((parent().par.Scene.menuIndex == {i} and parent().par.Crossfade.eval() < 1) "
            f"or (parent().par.Nextscene.menuIndex == {i} and parent().par.Crossfade.eval() > 0) "
            f"or parent().par.Freerunall.eval()))",
        )
        out = scene.op("out")
        if out is not None:
            outs.append(out)

    # Two selector switches (deck A and deck B) blended by a Cross TOP. The
    # scenes' Out TOPs give each scene COMP an output connector; that is what
    # gets wired here (a wire straight from the inner 'out' would cross the
    # COMP boundary and silently not exist).
    switch_a = _create(base, "switchTOP", "deck_a", -40, 80)
    switch_b = _create(base, "switchTOP", "deck_b", -40, -80)
    for idx, out in enumerate(outs):
        feed = _scene_feed(base, out)
        _connect(feed, switch_a, idx)
        _connect(feed, switch_b, idx)
    # A Cross TOP cooks both inputs every frame, so with a plain A/B wiring the
    # armed-but-invisible deck would render its whole chain (render, trails,
    # bloom -- or a raymarch) for nothing. Instead, whenever a deck contributes
    # nothing to the mix it points at the *same* scene as the other deck; TD
    # cooks that node once per frame, so the idle deck is free. The scenes'
    # Active flags below use the same rule, so sim and render agree.
    _expr(switch_a, "index",
          "parent().par.Scene.menuIndex if parent().par.Crossfade.eval() < 1 "
          "else parent().par.Nextscene.menuIndex")
    _expr(switch_b, "index",
          "parent().par.Nextscene.menuIndex if parent().par.Crossfade.eval() > 0 "
          "else parent().par.Scene.menuIndex")

    cross = _create(base, "crossTOP", "crossfade", 160, 0)
    _connect(switch_a, cross, 0)
    _connect(switch_b, cross, 1)
    _expr(cross, "cross", "parent().par.Crossfade")

    # Creative waveform overlay (toggled by 'Wavevis'), then the GLSL post chain.
    mixed = _waveform_overlay(base, cross, reactor, x=300)
    post = _post_fx(base, mixed, reactor, tempo, x=480)

    # A live show must never go black because a post shader failed to compile
    # on some build: 'Fx' off routes the raw mix straight to 'out'.
    bypass = _create(base, "switchTOP", "fx_bypass", 600, 0)
    _connect(post, bypass, 0)
    _connect(mixed, bypass, 1)
    _expr(bypass, "index", "0 if parent().par.Fx.eval() else 1")

    # The film stock: one grade, grain and dirt over the whole show, after the
    # performer's FX and before the title (a title should be printed on the
    # same stock as everything else, not laid on top of it clean).
    stocked, stock = _grunge(base, bypass, reactor, tempo, x=700)

    # Ink-bleed scene title (TITLE button) over the show, then the blackout
    # level; both sit after the stock so they work with post FX off too.
    titled, ink = _title_overlay(base, stocked, scene_names, x=900)
    master = _create(base, "levelTOP", "master_level", 1060, 0)
    _connect(titled, master)
    _expr(master, "opacity", "0 if parent().par.Blackout.eval() else 1")
    _set_res(master)

    # A rebuild used to leave the previous 'out' behind, so TD named the new
    # one 'out1' and the COMP viewer kept showing the stale node. Clear any
    # twin, then point the viewer at this one by name.
    for stale in ("out1", "out2"):
        old = base.op(stale)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
    final = _create(base, "nullTOP", "out", 1220, 0)
    _connect(master, final)
    _set_res(final)
    if _setpar_any(base, ("opviewer",), "./out") is None:
        print("[td_build] could not point the PhysicsVJ viewer at ./out")
    try:
        final.viewer = True
    except Exception:
        pass

    # Pull the master chain once now. Nothing else does during a headless
    # build, so without this a GLSL compile failure in post/overlay would
    # never reach build_report.txt -- it would just be a black 'out' later.
    for o in (cross, mixed, post, stock, final):
        try:
            o.cook(force=True)
        except Exception as e:
            print(f"[td_build] cook of {o.path} raised: {e}")
    try:
        ink.cook(force=True)     # surface a title-shader compile error now
    except Exception as e:
        print(f"[td_build] cook of {ink.path} raised: {e}")
    for o in (switch_a, switch_b, cross, mixed, post, bypass, stock, ink, master, final):
        try:
            err = o.errors()
            if err:
                print(f"[td_build] {o.path}: {err.strip()}")
        except Exception:
            pass
    _report_master_chain(switch_a, switch_b, cross, final, len(outs))

    # Make the whole show breathe: bind scene params to the audio + tempo.
    _safe("reactive bindings", _reactive_bindings, base, reactor, tempo)
    try:
        base.store("scene_names", scene_names)
        base.store("scene_titles", [SCENE_TITLES.get(n, n.upper()) for n in scene_names])
        base.store("title_t0", -1e9)
        base.store("title_toff", -1e9)
    except Exception:
        pass
    _safe("punch / title controls", _punch_controls, base)
    _safe("scene health", _scene_health, outs)

    print(f"[td_build] built PhysicsVJ with {len(outs)} scenes -> {base.path}")
    print("[td_build] View 'out' in Perform mode. Cut with 'Scene'; blend with "
          "'Nextscene' + 'Crossfade'.")

    # An APC mini mk2 surface to run the whole show from hardware.
    if apc:
        try:
            build_apc(dest=dest, target=base)
        except Exception as e:
            print(f"[td_build] APC surface skipped: {e}")
    # The operator's view: the program feed beside the control map.
    _safe("dashboard", build_dashboard, dest, base)

    return base


def _safe(what, fn, *args):
    """Run a non-essential build step; a failure is one report line, never a
    dead show (a Keyboard In DAT refusing its text once aborted build_all
    after every scene had been built)."""
    try:
        return fn(*args)
    except Exception as e:
        print(f"[td_build] {what} skipped: {type(e).__name__}: {e}")
        return None


def _scene_feed(base, out):
    """The operator in ``base``'s network that carries a scene's picture: the
    scene COMP itself (its Out TOP connector) or, if this build gives the COMP
    no output connector, a Select TOP pointing at the inner 'out'."""
    scene = out.parent()
    try:
        if len(scene.outputConnectors) > 0:
            return scene
    except Exception:
        pass
    sel = _create(base, "selectTOP", "feed_" + scene.name, scene.nodeX + 260, scene.nodeY)
    _setpar(sel, "top", out)
    return sel


def _report_master_chain(switch_a, switch_b, cross, final, n_scenes):
    """One line per fact about the master chain, for the build report. Each
    is fetched on its own so one missing attribute cannot hide the rest."""
    def fact(label, fn):
        try:
            print(f"[td_build] master: {label} = {fn()}")
        except Exception as e:
            print(f"[td_build] master: {label} = ? ({e})")

    fact("scenes wired", lambda: n_scenes)
    fact("deck_a inputs", lambda: f"{len(switch_a.inputs)}: " + " ".join(i.path for i in switch_a.inputs))
    fact("deck_b inputs", lambda: len(switch_b.inputs))
    fact("deck_a index expr", lambda: switch_a.par.index.expr)
    fact("deck_a index value", lambda: switch_a.par.index.eval())
    fact("deck_a size", lambda: f"{switch_a.width}x{switch_a.height}")
    fact("cross inputs", lambda: len(cross.inputs))
    fact("cross value", lambda: cross.par.cross.eval())
    fact("out size", lambda: f"{final.width}x{final.height}")
    fact("out inputs", lambda: " -> ".join(i.path for i in final.inputs))


# ===========================================================================
# GLSL / audio / tempo layer
#
# Everything below adds compiled-shader scenes, a GLSL post chain, an audio
# Reactor (the DJ feed -> bass/mid/high/level/beat) and a Tempo engine that
# follows a MIDI beat clock (or free-runs). Scenes and shaders bind their
# uniforms/params to these so the whole show reacts and evolves with the music.
# ===========================================================================
_SHADER_DIR = os.path.join(REPO, "touchdesigner", "shaders")


def _try_create(parent, optype, name, x=0, y=0):
    """Like _create but returns None instead of raising on unknown op types
    (used for bleeding-edge families like POPs that may not exist on a build)."""
    try:
        return _create(parent, optype, name, x, y)
    except Exception as e:
        print(f"[td_build] cannot create {optype} '{name}': {e}")
        return None


def _load_shader(filename, prepend_common=True):
    with open(os.path.join(_SHADER_DIR, filename)) as fh:
        text = fh.read()
    if prepend_common and filename != "common.glsl":
        with open(os.path.join(_SHADER_DIR, "common.glsl")) as fh:
            text = fh.read() + "\n" + text
    return text


def _shader_dat(container, name, filename, x, y, prepend_common=True):
    dat = _create(container, "textDAT", name, x, y)
    dat.text = _load_shader(filename, prepend_common)
    return dat


def _print_pars(o):
    """List an operator's parameter names in the build log (for POP operators,
    whose names are still settling between builds)."""
    try:
        names = sorted({p.name for p in o.pars() if not p.name.startswith(("node", "op", "clone"))})
        print(f"[td_build] pars of {o.path} ({o.type}): {' '.join(names)}")
    except Exception as e:
        print(f"[td_build] could not list pars of {o.path}: {e}")


def _bindexpr_any(o, names, expression):
    """_bindexpr on the first of several candidate parameter names that exists
    (a POP strength has been called several things); logs once if none does."""
    for name in names:
        if hasattr(o.par, name):
            return _expr(o, name, expression, what="binding")
    print(f"[td_build] no par {o.path}.{'|'.join(names)} for binding")
    return False


def _bindexpr(o, name, expression):
    """An expression binding for uniforms/reactive params. Same mechanics as
    _expr (and the same report line on failure -- a silently unbound uniform
    is exactly the kind of fault that only shows up as a dead scene)."""
    return _expr(o, name, expression, what="binding")


def _glsl_uniforms(top, scalars, start=0):
    """Fill a GLSL TOP's 'Vectors' slots with float uniforms.

    ``scalars`` is an ordered list of ``(uniformName, exprStr_or_number)``.
    Each occupies one slot (uninameN + valueNx). Returns the next free slot.
    Defensive: if the parameter names differ on this TD version, the shader
    still runs -- you just bind these uniforms by hand on the node.
    """
    slot = start
    for uname, val in scalars:
        _setpar(top, f"uniname{slot}", uname)
        px = f"value{slot}x"
        if isinstance(val, str):
            _bindexpr(top, px, val)
        else:
            _setpar(top, px, val)
        slot += 1
    return slot


def _glsl_vec4(top, slot, uname, values):
    """Fill one Vectors slot with a vec4 uniform: ``values`` are four
    expression strings or numbers for x, y, z, w."""
    _setpar(top, f"uniname{slot}", uname)
    for comp, val in zip("xyzw", values):
        pn = f"value{slot}{comp}"
        if isinstance(val, str):
            _bindexpr(top, pn, val)
        else:
            _setpar(top, pn, val)
    return slot + 1


def _react_exprs(reactor):
    """Expression strings reading the Reactor's analyze CHOP (or constants)."""
    keys = ("bass", "mid", "high", "level", "beat", "kick", "pulse", "bpm")
    if reactor is None or reactor.op("analyze") is None:
        return {k: 0.0 for k in keys}
    base = reactor.op("analyze").path
    return {k: f"op('{base}')['{k}']" for k in keys}


def _tempo_exprs(tempo):
    keys = ("bpm", "beat", "bar", "sine", "pulse")
    if tempo is None or tempo.op("tempo") is None:
        return {k: 0.0 for k in keys}
    base = tempo.op("tempo").path
    return {k: f"op('{base}')['{k}']" for k in keys}


def _glsl_scene_palette(container, default_index=0):
    """A 'Palette' menu on a GLSL scene COMP (so the APC + UI can drive it)."""
    page = _custom_page(container, "VJ")
    if not hasattr(container.par, "Palette"):
        m = page.appendMenu("Palette")[0]
        try:
            from physics import palette
            m.menuNames = palette.PALETTE_NAMES
            m.menuLabels = palette.PALETTE_NAMES
            m.val = palette.PALETTE_NAMES[default_index % len(palette.PALETTE_NAMES)]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Audio Reactor + Tempo engine
# ---------------------------------------------------------------------------
def build_reactor(dest=None, name="Reactor"):
    """The DJ feed -> reactive control channels + data textures.

    Builds an Audio Device In CHOP (pick your interface on its 'source' node),
    a Script CHOP analyser (bass/mid/high/level/beat/bpm) and two row textures
    (waveform + spectrum) used by the waveform overlay shader.
    """
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    src = _try_create(c, "audiodeviceinCHOP", "source", -400, 0)

    analyze = _create(c, "scriptCHOP", "analyze", -180, 0)
    _install_callbacks(analyze, "audio_chop.py")
    if src is not None:
        _connect(src, analyze)
    _cook_driver(c, analyze)  # advance the envelope follower once per frame

    # Waveform texture (R = samples across width).
    wave_tex = _try_create(c, "choptoTOP", "wave_tex", -180, -160)
    if wave_tex is not None and src is not None:
        _setpar(wave_tex, "chop", src)
    # Spectrum texture (R = FFT magnitude across width).
    spec = _try_create(c, "audiospectrumCHOP", "spec", -400, -160)
    if spec is not None and src is not None:
        _connect(src, spec)
    spec_tex = _try_create(c, "choptoTOP", "spec_tex", -180, -260)
    if spec_tex is not None and spec is not None:
        _setpar(spec_tex, "chop", spec)

    print(f"[td_build] built Reactor -> {c.path} "
          "(set the 'source' node's Device to your DJ input)")
    return c


def build_tempo(dest=None, name="Tempo", device=1):
    """A tempo engine: follows an incoming MIDI beat clock (24 PPQN) on the
    given device id, or free-runs on a manual BPM. Outputs beat/bar phase."""
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)

    clockin = _create(c, "midiinDAT", "clockin", -360, 0)
    _setpar(clockin, "id", device)          # Device ID (not the Device Table path)
    _setpar(clockin, "active", True)
    # Whether clock/start/stop reach the callback depends on the build (see
    # docs/ARCHITECTURE.md, version-sensitive spots); the manual BPM path in
    # tempo_chop always works regardless.
    cb = _create(c, "textDAT", "clockin_callbacks", -360, 150)
    cb.text = (
        "import sys\n"
        f"sys.path.insert(0, r\"{REPO}\")\n"
        "from touchdesigner.callbacks import tempo_chop\n\n"
        "def onReceiveMIDI(dat, rowIndex, message, channel, index, value, input, bytes):\n"
        "    try:\n"
        "        tempo_chop.on_realtime(dat.parent().op('tempo'), message)\n"
        "    except Exception as e:\n"
        "        debug('[tempo] midi', e)\n"
        "    return\n"
    )
    _setpar(clockin, "callbacks", cb)

    tempo = _create(c, "scriptCHOP", "tempo", -120, 0)
    _install_callbacks(tempo, "tempo_chop.py")
    _cook_driver(c, tempo)

    print(f"[td_build] built Tempo -> {c.path} (MIDI clock device {device}; "
          "manual BPM always works as a fallback)")
    return c


# ---------------------------------------------------------------------------
# GLSL TOP scenes
# ---------------------------------------------------------------------------
def build_reaction_diffusion(dest=None, name="rd", palette_index=5):
    """Gray-Scott reaction-diffusion on the GPU -- organic, ever-evolving
    spots/stripes that bloom and dissolve with the music (feedback GLSL TOP)."""
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    reactor, tempo = dest.op("Reactor"), dest.op("Tempo")
    rex = _react_exprs(reactor)
    _glsl_scene_palette(c, palette_index)
    page = _custom_page(c, "VJ")
    if not hasattr(c.par, "Feed"):
        page.appendFloat("Feed", label="Feed Rate")[0].val = 0.037
        page.appendFloat("Kill", label="Kill Rate")[0].val = 0.06
        page.appendToggle("Invert", label="Invert Field")[0].val = True
        page.appendPulse("Reseed", label="Reseed")
    res = 320

    state = _create(c, "glslTOP", "rd_state", -200, 0)
    _set_res(state, res, res)
    _setpar(state, "format", "rgba32float")
    _setpar(state, "pixeldat", _shader_dat(c, "rd_state_src", "reaction_diffusion.frag", -200, 150))

    # A Feedback TOP needs an input ("Not enough sources specified" without
    # one): it is the reset image and sets the loop's size and format. A flat
    # (u=1, v=0) 32-bit constant is the resting chemistry; the shader's sparks
    # seed the pattern from there.
    seed = _create(c, "constantTOP", "rd_seed", -600, 0)
    for pn, v in (("colorr", 1.0), ("colorg", 0.0), ("colorb", 0.0), ("alpha", 1.0)):
        _setpar(seed, pn, v)
    _set_res(seed, res, res)
    _setpar_any(seed, ("format",), "rgba32float", quiet=True)
    fb = _create(c, "feedbackTOP", "rd_fb", -400, 0)
    _connect(seed, fb, 0)
    _set_res(fb, res, res)  # same size as the state, or the loop resamples
    _setpar(fb, "top", state)             # (and blurs) the chemistry each frame
    _connect(fb, state, 0)

    # Reseed: store the trigger frame; the shader's uReseed reads it for 1 frame.
    reseeder = _create(c, "parameterexecuteDAT", "reseeder", -400, 150)
    _setpar(reseeder, "op", c)
    _setpar(reseeder, "pars", "Reseed")
    _setpar(reseeder, "onpulse", True)
    _setpar(reseeder, "active", True)
    reseeder.text = (
        "def onPulse(par):\n"
        "    par.owner.store('rs', absTime.frame)\n"
    )
    try:
        c.store("rs", -99)
    except Exception:
        pass

    # Resolution comes from TD's built-in uTDOutputInfo.res inside the shaders
    # (the #define at the top of each .frag), so every Vectors slot here is a
    # real uniform the shader declares.
    _glsl_uniforms(state, [
        ("uTime", "absTime.seconds"),
        ("uBass", rex["bass"]), ("uMid", rex["mid"]), ("uHigh", rex["high"]),
        ("uLevel", rex["level"]), ("uBeat", rex["beat"]),
        ("uFeed", "parent().par.Feed"), ("uKill", "parent().par.Kill"),
        ("uReseed", "1.0 if (absTime.frame - parent().fetch('rs', -99)) in (0, 1) else 0.0"),
    ])

    color = _create(c, "glslTOP", "rd_color", 20, 0)
    _connect(state, color, 0)
    _set_res(color)        # colourise at output size; the state is sampled by uv
    # Pin the colour stage to 8-bit. A GLSL TOP inherits its input's format, so
    # this one came out 32-bit float like the chemistry it reads -- and a float
    # frame has nothing clamping it, so the bloom's black point mapped a black
    # pixel to exactly -1.0 (the scene reported a mean of -1.000).
    _setpar_any(color, ("format",), "rgba8fixed")
    _setpar(color, "pixeldat", _shader_dat(c, "rd_color_src", "rd_color.frag", 20, 150))
    _glsl_uniforms(color, [
        ("uTime", "absTime.seconds"), ("uLevel", rex["level"]),
        ("uHigh", rex["high"]), ("uBeat", rex["beat"]),
        ("uPalette", "parent().par.Palette.menuIndex"),
        ("uInvert", "int(parent().par.Invert.eval())"),
    ])

    out = _glow(c, color, size=8.0, x=240, threshold=0.25)
    _cook_driver(c, state)  # keep the feedback advancing while the scene is live
    try:
        c.par.Reseed.pulse()  # seed the pattern now
    except Exception:
        pass
    for pn in ("resetpulse", "reset"):
        try:
            getattr(fb.par, pn).pulse()   # load the seed image into the loop
            break
        except Exception:
            continue
    # Run the chemistry a few frames and report what the state holds: a dead
    # loop (all u=1, v=0), a poisoned one (NaN) and a live one look alike in
    # the network editor but not here.
    try:
        for _ in range(12):
            state.cook(force=True)
        arr = state.numpyArray()
        u, v = arr[..., 0], arr[..., 1]
        import numpy as _np
        print(f"[td_build] rd state after 12 frames: u mean {float(_np.nanmean(u)):.3f} "
              f"v mean {float(_np.nanmean(v)):.4f} v max {float(_np.nanmax(v)):.3f} "
              f"nan {float(_np.isnan(arr).mean()):.4f} size {arr.shape[1]}x{arr.shape[0]}")
    except Exception as e:
        print(f"[td_build] rd state check failed: {e}")
    print(f"[td_build] built Reaction-Diffusion -> {c.path}")
    return c


def build_raymarch(dest=None, name="sdf", palette_index=2, quality=0.6):
    """An audio-reactive raymarched SDF: morphing metaballs that twist to the
    bass and orbit on the bar (single compiled fragment shader)."""
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    reactor, tempo = dest.op("Reactor"), dest.op("Tempo")
    rex = _react_exprs(reactor)
    tex = _tempo_exprs(tempo)
    _glsl_scene_palette(c, palette_index)
    page = _custom_page(c, "VJ")
    if not hasattr(c.par, "Shape"):
        m = page.appendMenu("Shape", label="Form")[0]
        m.menuNames = ["metaballs", "gyroid", "fractal", "knot"]
        m.menuLabels = ["Metaballs", "Gyroid lattice", "IFS fractal", "Torus knot"]
        m.val = "metaballs"
        page.appendFloat("Speed", label="Speed")[0].val = 1.0
        c.par.Speed.normMin, c.par.Speed.normMax = 0.0, 4.0
        page.appendFloat("Twist", label="Twist")[0].val = 1.0
        c.par.Twist.normMin, c.par.Twist.normMax = 0.0, 4.0
        page.appendFloat("Zoom", label="Zoom")[0].val = 1.0
        c.par.Zoom.normMin, c.par.Zoom.normMax = 0.3, 3.0
        page.appendInt("Detail", label="Detail")[0].val = 2
        c.par.Detail.normMin, c.par.Detail.normMax = 1, 4
        page.appendFloat("Morph", label="Morph")[0].val = 0.5
        c.par.Morph.normMin, c.par.Morph.normMax = 0.0, 1.0
        q = page.appendFloat("Quality", label="Render Scale")[0]
        q.val = quality
        c.par.Quality.normMin, c.par.Quality.normMax = 0.3, 1.0
        page.appendPulse("Reseed", label="Next Form")
    # 'Next Form' steps the Shape menu.
    stepper = _create(c, "parameterexecuteDAT", "formstep", -120, 320)
    _setpar(stepper, "op", c)
    _setpar(stepper, "pars", "Reseed")
    _setpar(stepper, "onpulse", True)
    _setpar(stepper, "active", True)
    stepper.text = (
        "def onPulse(par):\n"
        "    p = par.owner.par.Shape\n"
        "    p.menuIndex = (p.menuIndex + 1) % len(p.menuNames)\n"
    )

    sdf = _create(c, "glslTOP", "sdf", -120, 0)
    # A raymarch is the one scene whose smoothness is a frame-rate question,
    # not a maths one: 110 marches plus soft shadows and occlusion per pixel
    # at 1920x1080 is millions of steps a frame, and it stutters however clean
    # the motion is. Render it below master size and let the upscale carry it;
    # the forms are smooth and bloomed, so the softness does not read as low
    # resolution the way an edge would. Render Scale trades it back.
    _set_res(sdf, scale_expr="parent().par.Quality.eval()")
    _setpar(sdf, "pixeldat", _shader_dat(c, "sdf_src", "raymarch.frag", -120, 160))
    # Packed vec4 uniforms: four Vectors slots carry everything the shader
    # needs (each slot is a vec4; a float uniform would read only .x).
    _glsl_vec4(sdf, 0, "uAudio", (rex["bass"], rex["mid"], rex["high"], rex["level"]))
    _glsl_vec4(sdf, 1, "uTempo", (rex["beat"], rex["pulse"], "absTime.seconds",
                                   "parent().par.Palette.menuIndex"))
    _glsl_vec4(sdf, 2, "uCtrl", ("parent().par.Shape.menuIndex", "parent().par.Speed",
                                  "parent().par.Twist", "parent().par.Zoom"))
    _glsl_vec4(sdf, 3, "uCtrl2", ("parent().par.Detail", "parent().par.Morph", 0.0, 0.0))

    # Back up to master size before the bloom, so the scene leaves at the show
    # resolution like every other one.
    up = _try_create(c, "fitTOP", "upscale", 20, 0)
    if up is None or not _connect(sdf, up):
        up = sdf
    else:
        _set_res(up)
        _setpar_any(up, ("fit",), "fill", quiet=True)
        _setpar_any(up, ("filter", "interpolate"), "gaussian", quiet=True)
    out = _glow(c, up, size=10.0, x=120)
    _cook_driver(c, sdf)
    print(f"[td_build] built Raymarch SDF -> {c.path}")
    return c


# ---------------------------------------------------------------------------
# Master post-FX + waveform overlay
# ---------------------------------------------------------------------------
def _waveform_overlay(container, src, reactor, name="wave", x=300, y=0):
    """Composite a creative waveform/spectrum visualiser over ``src``, gated
    by the 'Wavevis' toggle. Returns the mixed TOP (or ``src``).

    The gate is a Switch TOP, not a Level opacity: a Switch only cooks the
    input it shows, so with Wavevis off the overlay GLSL TOP is never pulled
    and a shader that fails to compile on some build cannot black out the
    master output (a Level of an errored TOP propagates the error).
    """
    if reactor is None or reactor.op("wave_tex") is None:
        return src
    rex = _react_exprs(reactor)
    ov = _create(container, "glslTOP", name + "_glsl", x, y - 160)
    _set_res(ov)
    _setpar(ov, "pixeldat", _shader_dat(container, name + "_src", "waveform_tunnel.frag", x, y - 320))
    # The textures live inside the Reactor COMP; fetch them into this network
    # with Select TOPs (a direct wire across the COMP boundary does nothing,
    # and a GLSL TOP whose shader reads sTD2DInputs[1] with no inputs
    # connected fails to compile -- the 'wave_glsl has compile errors' line).
    wave_sel = _create(container, "selectTOP", name + "_tex", x - 160, y - 160)
    _setpar(wave_sel, "top", reactor.op("wave_tex"))
    spec_sel = _create(container, "selectTOP", name + "_spec", x - 160, y - 260)
    _setpar(spec_sel, "top", reactor.op("spec_tex"))
    _connect(wave_sel, ov, 0)
    _connect(spec_sel, ov, 1)
    _glsl_uniforms(ov, [
        ("uTime", "absTime.seconds"), ("uLevel", rex["level"]),
        ("uBeat", rex["beat"]), ("uBass", rex["bass"]), ("uPalette", 3),
    ])

    comp = _create(container, "compositeTOP", name + "_over", x + 160, y - 160)
    _setpar(comp, "operand", "over")
    _connect(ov, comp, 0)    # overlay on top
    _connect(src, comp, 1)
    _set_res(comp)

    gate = _create(container, "switchTOP", name, x + 160, y)
    _connect(src, gate, 0)   # Wavevis off: the plain mix, overlay not cooked
    _connect(comp, gate, 1)
    _expr(gate, "index", "1 if parent().par.Wavevis.eval() else 0")
    _set_res(gate)
    return gate


def _post_fx(container, src, reactor, tempo, name="post", x=480, y=0):
    """The master GLSL post chain: beat punch, chromatic aberration, scanline
    shimmer, vignette, tonemap -- plus the sixteen whole-screen FX toggles on
    the show's FX page. Six packed vec4 uniforms (see post_fx.frag)."""
    rex = _react_exprs(reactor)
    tex = _tempo_exprs(tempo)
    post = _create(container, "glslTOP", name, x, y)
    _setpar(post, "pixeldat", _shader_dat(container, name + "_src", "post_fx.frag", x, y - 170))
    _connect(src, post, 0)
    _set_res(post)         # the master output is always MASTER_RES
    _glsl_vec4(post, 0, "uAudio", (rex["level"], rex["beat"], rex["high"], tex["bar"]))
    _glsl_vec4(post, 1, "uLook", ("parent().par.Kaleido", "parent().par.Rgbshift",
                                  "parent().par.Punch", "absTime.seconds"))
    for slot, uname in enumerate(("uFxA", "uFxB", "uFxC", "uFxD"), start=2):
        names = FX_NAMES[(slot - 2) * 4:(slot - 1) * 4]
        _glsl_vec4(post, slot, uname, tuple(f"int(parent().par.{n}.eval())" for n in names))
    # Tone: exposure, black crush, vignette strength, dark mode on/off.
    _glsl_vec4(post, 6, "uTone", ("parent().par.Exposure", 0.03, 1.0,
                                  "int(parent().par.Darkmode.eval())"))
    return post


def _grunge(container, src, reactor, tempo, name="stock", x=700, y=0):
    """The film stock: the grade, the grain, the dirt and the chaos dial.

    Separate from _post_fx on purpose. That pass is the performer's effects --
    things toggled for a bar and turned off. This one is what the show is made
    of, and it stays on all night: crushed contrast, cold shadows against warm
    highlights, halation around anything hot, grain in the mids, dust, a gate
    that never quite registers, and one Chaos dial that takes the frame apart
    on the beat. Switch-gated, so a shader that will not compile leaves the
    clean mix rather than a black screen.
    """
    rex = _react_exprs(reactor)
    page = _custom_page(container, "Stock")
    if not hasattr(container.par, "Stock"):
        page.appendToggle("Stock", label="Film Stock (grade / grain / dirt)")
        _setpar(container, "Stock", True)
        for pn, label, val, lo, hi in (
            ("Grain", "Grain", 0.65, 0.0, 2.0),
            ("Halation", "Halation", 0.55, 0.0, 2.0),
            ("Dust", "Dust + Scratches", 0.5, 0.0, 2.0),
            ("Weave", "Gate Weave", 0.6, 0.0, 2.0),
            ("Contrast", "Contrast", 0.45, 0.0, 1.5),
            ("Splittone", "Split Tone", 0.7, 0.0, 1.5),
            ("Chaos", "Chaos", 0.0, 0.0, 1.0),
        ):
            page.appendFloat(pn, label=label)[0].val = val
            try:
                getattr(container.par, pn).normMin = lo
                getattr(container.par, pn).normMax = hi
            except Exception:
                pass
        page.appendPulse("Chaosburst", label="Chaos Burst")
        page.appendFloat("Chaosdecay", label="Chaos Burst Decay (s)")[0].val = 0.7

    # A burst is a timestamp in storage and an expression that decays from it:
    # nothing per-frame in Python, and it cannot get stuck on.
    burst = _create(container, "parameterexecuteDAT", name + "_burst", -200, -510)
    _setpar(burst, "op", container)
    _setpar(burst, "pars", "Chaosburst")
    _setpar(burst, "onpulse", True)
    _setpar(burst, "active", True)
    burst.text = (
        "def onPulse(par):\n"
        "    par.owner.store('chaos_t0', absTime.seconds)\n"
    )
    try:
        container.store("chaos_t0", -1e9)
    except Exception:
        pass

    stock = _create(container, "glslTOP", name, x, y)
    _setpar(stock, "pixeldat", _shader_dat(container, name + "_src", "grunge.frag", x, y - 170))
    _connect(src, stock, 0)
    _set_res(stock)
    _glsl_vec4(stock, 0, "uGrain", ("parent().par.Grain", "parent().par.Halation",
                                    "parent().par.Dust", "parent().par.Weave"))
    # Chaos is the dial plus whatever is left of the last burst.
    chaos_expr = ("min(1.0, parent().par.Chaos.eval() + max(0.0, 1.0 - "
                  "(absTime.seconds - parent().fetch('chaos_t0', -1e9)) / "
                  "max(parent().par.Chaosdecay.eval(), 1e-3)))")
    _glsl_vec4(stock, 1, "uGrade", ("parent().par.Contrast", "parent().par.Splittone",
                                    chaos_expr, "absTime.seconds"))
    _glsl_vec4(stock, 2, "uAudio", (rex["level"], rex["beat"], rex["high"], rex["pulse"]))

    gate = _create(container, "switchTOP", name + "_gate", x + 170, y)
    _connect(src, gate, 0)          # Stock off: the clean mix, pass not cooked
    _connect(stock, gate, 1)
    _expr(gate, "index", "1 if parent().par.Stock.eval() else 0")
    _set_res(gate)
    return gate, stock


def _title_overlay(container, src, scene_names, name="title", x=760, y=0):
    """The TITLE button: the live scene's physics title in big caps, soaked in
    like ink on wet paper, composited over ``src``. Returns (gated TOP, ink TOP).

    A Text TOP renders the words (transparent background); a GLSL TOP bleeds
    them (ink_title.frag) from 'title_t0' / 'title_toff' in the show's storage
    (set by the Title pulse, the APC and the T key); a Switch shows the plain
    feed whenever no title is up so none of it cooks between titles.
    """
    text = _create(container, "textTOP", name + "_text", x, y - 320)
    _set_res(text)
    _setpar_any(text, ("text",), "", quiet=True)
    _expr(text, "text",
          "parent().fetch('scene_titles', [])[int(parent().par.Scene.menuIndex)] "
          "if int(parent().par.Scene.menuIndex) < len(parent().fetch('scene_titles', [])) else ''")
    _font(text, "display")
    _setpar_any(text, ("fontsizex", "fontsize"), 210, quiet=True)
    _setpar_any(text, ("fontsizey",), 210, quiet=True)
    _setpar_any(text, ("bold",), True, quiet=True)
    _setpar_any(text, ("alignx",), "center", quiet=True)
    _setpar_any(text, ("aligny",), "center", quiet=True)
    _setpar_any(text, ("wordwrap",), True, quiet=True)
    _setpar_any(text, ("bgalpha",), 0.0, quiet=True)
    for pn in ("fontcolorr", "fontcolorg", "fontcolorb"):
        _setpar_any(text, (pn,), 1.0, quiet=True)
    _setpar_any(text, ("fontalpha",), 1.0, quiet=True)

    ink = _create(container, "glslTOP", name + "_ink", x, y - 160)
    _set_res(ink)
    _setpar(ink, "pixeldat", _shader_dat(container, name + "_src", "ink_title.frag", x - 160, y - 320))
    _connect(text, ink, 0)
    # bleed: 0 -> 1 over 1.2 s from the moment the title was asked for;
    # fade: up in 0.25 s, down over 0.6 s after the release time.
    _glsl_vec4(ink, 0, "uInk", (
        "max(0.0, min(1.0, (absTime.seconds - parent().fetch('title_t0', -1e9)) / 1.2))",
        "max(0.0, min(1.0, (absTime.seconds - parent().fetch('title_t0', -1e9)) / 0.25, "
        "1.0 - (absTime.seconds - parent().fetch('title_toff', -1e9)) / 0.6))",
        "absTime.seconds", 0.0))

    over = _create(container, "compositeTOP", name + "_over", x + 160, y - 160)
    _setpar(over, "operand", "over")
    _connect(ink, over, 0)
    _connect(src, over, 1)
    _set_res(over)

    gate = _create(container, "switchTOP", name, x + 160, y)
    _connect(src, gate, 0)   # no title up: the plain feed, nothing else cooks
    _connect(over, gate, 1)
    _expr(gate, "index", "1 if absTime.seconds < parent().fetch('title_toff', -1e9) + 0.7 else 0")
    _set_res(gate)

    # The show's own Title pulse.
    te = _create(container, "parameterexecuteDAT", name + "r", -200, -410)
    _setpar(te, "op", container)
    _setpar(te, "pars", "Title")
    _setpar(te, "onpulse", True)
    _setpar(te, "active", True)
    te.text = (
        "def onPulse(par):\n"
        "    c = par.owner\n"
        "    now = absTime.seconds\n"
        "    c.store('title_t0', now)\n"
        "    c.store('title_toff', now + float(c.par.Titlehold.eval()))\n"
    )
    return gate, ink


def _punch_controls(base):
    """A hit on the live scene from a mouse click anywhere, the space bar, or
    the show's own Punch pulse: pulses the live scene sim's 'Punch' (a shock
    wave through the soft body, a blast through the flow / storm). Scenes
    without a Punch ignore it. The APC re-fire button does the same."""
    code = (
        "def _live_sim():\n"
        "    c = me.parent()\n"
        "    try:\n"
        "        names = c.fetch('scene_names', [])\n"
        "        name = names[int(c.par.Scene.menuIndex)]\n"
        "        return c.op(name + '/sim')\n"
        "    except Exception:\n"
        "        return None\n"
        "\n"
        "def punch():\n"
        "    sim = _live_sim()\n"
        "    if sim is not None and hasattr(sim.par, 'Punch'):\n"
        "        sim.par.Punch.pulse()\n"
        "\n"
    )
    # 1) the show's own pulse
    pe = _create(base, "parameterexecuteDAT", "puncher", -200, -460)
    _setpar(pe, "op", base)
    _setpar(pe, "pars", "Punch")
    _setpar(pe, "onpulse", True)
    _setpar(pe, "active", True)
    pe.text = code + "def onPulse(par):\n    punch()\n"
    # 2) any left click (global mouse; Perform mode included)
    mouse = _try_create(base, "mouseinCHOP", "mouse", -400, -560)
    if mouse is not None:
        ce = _create(base, "chopexecuteDAT", "clickpunch", -200, -560)
        _setpar(ce, "chop", mouse)
        _setpar(ce, "valuechange", True)
        _setpar(ce, "active", True)
        ce.text = code + (
            "def onValueChange(channel, sampleIndex, val, prev):\n"
            "    if 'lbutton' in channel.name and val > 0.5 and prev <= 0.5 \\\n"
            "            and int(me.parent().par.Clickpunch.eval()):\n"
            "        punch()\n"
        )
    # 3) the space bar
    # The whole surface, on the keyboard. Every action the pads reach has a
    # key (apc_mini.KEYMAP), so the show can be built, tested and played
    # without the controller plugged in at all.
    #
    # A Keyboard In DAT is an *input* DAT: its text is the key log and is not
    # editable ("The operator is not editable" aborted a whole build). Like the
    # MIDI In DAT, its script lives in a Text DAT named by 'callbacks'.
    kb = _try_create(base, "keyboardinDAT", "keys", -400, -660)
    if kb is not None:
        try:
            from touchdesigner.callbacks import apc_mini as _apc
            keys_param = _apc.KEYS_PARAM
        except Exception:
            keys_param = "space t"
        _setpar_any(kb, ("keys",), keys_param)
        _setpar(kb, "active", True)
        kb_cb = _create(base, "textDAT", "keys_callbacks", -400, -760)
        _setpar(kb, "callbacks", kb_cb)
        kb_cb.text = (
            "import sys\n"
            f"sys.path.insert(0, r\"{REPO}\")\n"
            "from touchdesigner.callbacks import apc_mini\n\n"
            "# The keyboard drives the same perform() the pads do, so a key and\n"
            "# a pad cannot drift apart. The APC surface is passed when it\n"
            "# exists so its LEDs answer the keyboard too.\n"
            "def _surface():\n"
            "    try:\n"
            "        for c in op('/').children:\n"
            "            if c.name.startswith('APCShow'):\n"
            "                return c\n"
            "    except Exception:\n"
            "        pass\n"
            "    return None\n\n"
            "def onKey(dat, key, character, alt, lAlt, rAlt, ctrl, lCtrl, rCtrl,\n"
            "          shift, lShift, rShift, state, time, cmd, lCmd, rCmd):\n"
            "    show = me.parent()\n"
            "    k = key\n"
            "    if shift and isinstance(k, str) and len(k) == 1 and k.isalpha():\n"
            "        k = k.upper()          # shift picks the heavier variant\n"
            "    try:\n"
            "        did = apc_mini.on_key(show, k, bool(state), _surface())\n"
            "    except Exception as e:\n"
            "        debug('[keys]', e)\n"
            "        return\n"
            "    if did is None and k == 'space' and state \\\n"
            "            and int(show.par.Clickpunch.eval()):\n"
            "        show.par.Punch.pulse()\n"
        )


def _scene_health(outs):
    """One line per scene: what its 'out' actually holds after a few frames.

    Black (0.000), white (~1.0) and negative or NaN pixels are the failure
    modes that hide behind a clean error walk, so the range is printed, not
    just the mean. A scene that comes out black is also *repaired* where the
    builder can: the cinema pass is switched off, and failing that the
    geometry's material is swapped for the other family (an additive cloud
    that renders black becomes lit, a PBR body with no environment light to
    reflect becomes Phong). Every step says what it did and what it measured.
    """
    try:
        import numpy as _np
    except Exception:
        return

    def stats(out):
        for _ in range(3):
            out.cook(force=True)
        arr = out.numpyArray()
        rgb = arr[..., :3]
        return (float(_np.nanmean(rgb)), float(_np.nanmin(rgb)), float(_np.nanmax(rgb)),
                float(_np.isnan(rgb).mean()), float((rgb.max(axis=2) > 0.05).mean()))

    for out in outs:
        scene = out.parent()
        try:
            mean, lo, hi, nan, lit = stats(out)
        except Exception as e:
            print(f"[td_build] health {out.path}: unreadable ({e})")
            continue
        note = ""
        if mean < 0.002 or nan > 0.01 or lo < -0.001:
            # Remedy 1: the depth-aware pass, which can darken a sparse scene.
            if hasattr(scene.par, "Cinema") and int(scene.par.Cinema.eval()):
                _setpar(scene, "Cinema", False)
                try:
                    mean2, lo, hi, nan, lit = stats(out)
                    note += f"  cinema off -> {mean2:.3f}"
                    mean = mean2
                except Exception:
                    pass
            # Remedy 2: the other material family.
            if mean < 0.002:
                geo = scene.op("geo")
                if geo is not None and hasattr(geo.par, "material"):
                    swapped = _swap_material(scene, geo)
                    if swapped:
                        try:
                            mean3, lo, hi, nan, lit = stats(out)
                            note += f"  {swapped} -> {mean3:.3f}"
                            mean = mean3
                        except Exception:
                            pass
        flag = "  <-- BLACK" if mean < 0.002 else ("  <-- WHITE" if mean > 0.9 else "")
        if nan > 0.01:
            flag += f"  <-- {nan * 100:.0f}% NaN"
        if lo < -0.001:
            flag += f"  <-- NEGATIVE (min {lo:.3f})"
        print(f"[td_build] health {scene.name:9s} mean {mean:.3f} lit {lit:.2f} "
              f"range [{lo:.3f}, {hi:.3f}]{flag}{note}")


def _swap_material(scene, geo, x=-260, y=-320):
    """Give ``geo`` the other kind of material and say which. An additive
    cloud that renders black becomes a lit PBR body; a PBR body with nothing
    to reflect becomes Phong, which needs no environment light."""
    try:
        current = geo.par.material.eval()
        kind = current.OPType if current is not None else ""
    except Exception:
        kind = ""
    try:
        if "pbr" in str(kind).lower():
            _setpar(geo, "material", _lit_mat(scene, "phong_fallback_mat", x, y))
            return "phong material"
        _setpar(geo, "material", _pbr_mat(scene, "lit_fallback_mat", x, y,
                                          metallic=0.0, roughness=0.55))
        return "lit material"
    except Exception as e:
        print(f"[td_build] {scene.name}: material swap failed: {e}")
        return None


def _reactive_bindings(base, reactor, tempo):
    """Bind a tasteful set of *non-APC* scene params to the audio so the show
    evolves on its own. (APC faders own Trail/Orbit/Pointsize/Crossfade, so we
    deliberately avoid those to keep manual control of them.)"""
    rex = _react_exprs(reactor)
    if reactor is None:
        return

    def bind(path, par, expr):
        o = base.op(path)
        if o is not None:
            _bindexpr(o, par, expr)

    bind("ising/sim", "Temperature", f"2.27 + 0.30*{rex['bass']} - 0.10*{rex['high']}")
    bind("ising/sim", "Wallglow", f"0.5 + 1.2*{rex['high']}")
    bind("nbody/sim", "Gravity", f"1.0 + 0.8*{rex['bass']}")
    bind("flow/sim", "Speed", f"1.4*(1.0 + 0.9*{rex['level']})")
    bind("flow/sim", "Evolve", f"0.10 + 0.30*{rex['bass']}")
    bind("softbody/sim", "Spin", f"1.0 + 1.5*{rex['mid']}")
    bind("pops/sim", "Speed", f"2.6*(1.0 + 0.8*{rex['level']})")
    bind("pops/sim", "Evolve", f"0.22 + 0.4*{rex['bass']}")


# ---------------------------------------------------------------------------
# Professional lighting + a compiled glow material
# ---------------------------------------------------------------------------
def _light_rig(container, reactor=None, x=-200, y=300, shadows=False, env=True):
    """A low-key, dramatic rig -- the lighting of a rendered still, not a
    product shot: one strong warm key high on the side (soft shadow map), a
    fill kept low and cold so shadowed sides fall to near black, a hard cold
    rim from behind that pulses with the beat, and a coloured practical that
    slowly circles the scene so highlights travel. All point lights attenuate
    with distance. With ``env`` the procedural studio environment is added as
    an Environment Light (image-based lighting: PBR bodies reflect a softbox).
    Returns a list of lights to hand to a Render TOP."""
    rex = _react_exprs(reactor)

    def light(name, pos, rgb, dy, dimmer=1.0):
        L = _create(container, "lightCOMP", name, x, y - dy)
        _setpar(L, "tx", pos[0]); _setpar(L, "ty", pos[1]); _setpar(L, "tz", pos[2])
        # Light COMP colour is cr/cg/cb (colorr/g/b is the Constant TOP/MAT spelling).
        _setpar_any(L, ("cr", "colorr"), rgb[0])
        _setpar_any(L, ("cg", "colorg"), rgb[1])
        _setpar_any(L, ("cb", "colorb"), rgb[2])
        _setpar(L, "dimmer", dimmer)
        _setpar_any(L, ("attenuated",), True, quiet=True)
        _setpar_any(L, ("attenuationstart",), 4.0, quiet=True)
        _setpar_any(L, ("attenuationend",), 60.0, quiet=True)
        return L

    key = light("key", (9.0, 11.0, 5.0), (1.0, 0.82, 0.6), 0, dimmer=3.0)
    if shadows:
        _setpar_any(key, ("shadowtype",), "soft")
        _setpar_any(key, ("shadowquality",), "high", quiet=True)
        _setpar_any(key, ("shadowsoftness",), 3.0, quiet=True)
    fill = light("fill", (-9.0, 1.0, 6.0), (0.3, 0.45, 0.9), 120, dimmer=0.45)
    rim = light("rim", (-2.0, 5.0, -10.0), (0.7, 0.85, 1.0), 240, dimmer=1.8)
    if reactor is not None:
        _bindexpr(rim, "dimmer", f"1.8 + 1.2*{rex['beat']}")
    # The practical: a saturated accent that orbits low around the scene.
    prac = light("practical", (6.0, -2.0, 6.0), (1.0, 0.35, 0.2), 360, dimmer=1.2)
    _expr(prac, "tx", "8.0 * math.cos(absTime.seconds * 0.21)")
    _expr(prac, "tz", "8.0 * math.sin(absTime.seconds * 0.21)")
    if reactor is not None:
        _bindexpr(prac, "dimmer", f"0.8 + 0.9*{rex['bass']}")
    lights = [key, fill, rim, prac]
    if env:
        e = _studio_env(container, x=x, y=y - 480)
        if e is not None:
            lights.append(e)
    return lights


def _spot_rig(container, reactor=None, x=-200, y=300, cone=20.0, delta=26.0,
              pos=(7.0, 9.0, 6.0), dimmer=9.0):
    """A single soft-edged spotlight and nothing else.

    The point of it: with no ambient, no fill and no environment, a body is
    only visible while it is inside the beam. A wide cone *delta* (the soft
    shoulder) is what makes the pool of light read as a gaussian rather than a
    hard theatre circle. Returns the list of lights for a Render TOP.
    """
    rex = _react_exprs(reactor)
    L = _create(container, "lightCOMP", "spot", x, y)
    _setpar(L, "tx", pos[0]); _setpar(L, "ty", pos[1]); _setpar(L, "tz", pos[2])
    # Aim it at the origin: a Light COMP points down -z, so rotate it there.
    import math as _math
    dist_xz = _math.hypot(pos[0], pos[2])
    _setpar(L, "rx", -_math.degrees(_math.atan2(pos[1], dist_xz)))
    _setpar(L, "ry", _math.degrees(_math.atan2(pos[0], pos[2])))
    if _setpar_any(L, ("lighttype",), "cone") is None:
        _setpar_any(L, ("lighttype",), 1)          # 0 point, 1 cone on some builds
    _setpar_any(L, ("coneangle",), cone)
    _setpar_any(L, ("conedelta",), delta)          # the soft shoulder
    _setpar_any(L, ("conerolloff",), 1.0, quiet=True)
    _setpar_any(L, ("cr", "colorr"), 1.0)
    _setpar_any(L, ("cg", "colorg"), 0.95)
    _setpar_any(L, ("cb", "colorb"), 0.88)
    _setpar(L, "dimmer", dimmer)
    _setpar_any(L, ("attenuated",), True, quiet=True)
    _setpar_any(L, ("attenuationstart",), 2.0, quiet=True)
    _setpar_any(L, ("attenuationend",), 40.0, quiet=True)
    # The beam sweeps slowly, so bodies drift in and out of it on their own.
    _expr(L, "tx", "%.3f * math.cos(absTime.seconds * 0.07)" % pos[0])
    _expr(L, "tz", "%.3f * math.sin(absTime.seconds * 0.07) + %.3f" % (pos[0], pos[2] * 0.2))
    _expr(L, "ry", "math.degrees(math.atan2(me.par.tx.eval(), me.par.tz.eval()))")
    if reactor is not None:
        _bindexpr(L, "dimmer", f"{dimmer:.2f} * (0.85 + 0.3*{rex['pulse']})")
    return [L]


USE_GLSL_MAT = False   # glow_mat.vert/.pixel fail to compile on 2025.3 (error
                       # material = red/blue checker); the soft MAT is the look
                       # until the shader is fixed against that build's log.


def _glow_mat(container, reactor=None, name="glow_mat", x=-200, y=-180):
    """Compiled GLSL MAT: emissive core + Fresnel rim, audio-reactive. Opt-in
    (USE_GLSL_MAT); otherwise the additive soft MAT, which needs no shader."""
    if not USE_GLSL_MAT:
        return _soft_mat(container, name, x, y)
    rex = _react_exprs(reactor)
    mat = _try_create(container, "glslMAT", name, x, y)
    if mat is None:
        return _soft_mat(container, name, x, y)
    # GLSL MAT shader DAT parameters are vdat/pdat (the GLSL TOP's is pixeldat).
    _setpar_any(mat, ("vdat", "vertexdat"),
                _shader_dat(container, name + "_vert", "glow_mat.vert", x, y - 130, prepend_common=False))
    _setpar_any(mat, ("pdat", "pixeldat"),
                _shader_dat(container, name + "_pix", "glow_mat.pixel", x + 150, y - 130, prepend_common=False))
    _glsl_uniforms(mat, [("uLevel", rex["level"]), ("uBeat", rex["beat"])])
    return mat


# ---------------------------------------------------------------------------
# POPs: GPU particles in huge, organic, physics-driven numbers
# ---------------------------------------------------------------------------
def build_pops(dest=None, name="pops", palette="acid", count=200000, use_pops=False):
    """A GPU particle storm built with TouchDesigner's POP family (the new
    GPU-resident 3D operators). Particles are emitted from a sphere, driven by
    a radial force + curl-style noise so they swirl in organic, physical ways,
    and rendered with the compiled glow material under a 3-point light rig.

    POPs require TouchDesigner 2023.30000+ (officially 2024+). If the POP
    operators aren't available on this build, this falls back to a high-count
    curl-noise particle scene (the proven Flow callback) so the slot always
    renders -- just with fewer particles than POPs would allow.
    """
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    reactor, tempo = dest.op("Reactor"), dest.op("Tempo")
    rex = _react_exprs(reactor)
    _glsl_scene_palette(c, 5)

    geo = _create(c, "geometryCOMP", "geo", -260, 0)
    for child in list(geo.children):
        try:
            child.destroy()
        except Exception:
            pass

    # --- Attempt the real POP network (opt-in: its parameters are still
    # being learned from build reports, and a wrong one is a checkered ball)
    emitter = _try_create(geo, "spherePOP", "emitter") if use_pops else None
    particle = _try_create(geo, "particlePOP", "sim") if emitter is not None else None

    if emitter is not None and particle is not None:
        _setpar_any(emitter, ("radius", "rad", "radx"), 1.5)
        _connect(emitter, particle, 0)
        _setpar_any(particle, ("maxparticles", "maxpoints"), int(count))
        _setpar_any(particle, ("birthrate", "birth"), max(1000, int(count / 20)))
        _setpar_any(particle, ("lifeexpect", "life", "lifespan"), 6.0)
        _setpar_any(particle, ("lifevariance", "lifevar"), 2.0)
        _setpar_any(particle, ("velocitydamping", "damping", "drag"), 0.04)
        _setpar_any(particle, ("timeintegration", "enabletimeintegration"), True, quiet=True)
        # Forces in a feedback loop: a radial push + turbulent noise.
        force = _try_create(geo, "forceradialPOP", "force")
        noise = _try_create(geo, "noisePOP", "turb")
        nullp = _try_create(geo, "nullPOP", "loop")
        chain_tail = particle
        if force is not None:
            _connect(chain_tail, force, 0)
            # forceradialPOP (2025): 'radial' enables the radial term, its
            # magnitude is 'radialstrength' (negative pulls toward the centre).
            _setpar_any(force, ("radial",), True, quiet=True)
            if not _bindexpr_any(force, ("radialstrength", "force", "strength"),
                                 f"-2.0 - 6.0*{rex['bass']}"):
                _print_pars(force)          # so the report shows the real names
            chain_tail = force
        if noise is not None:
            _connect(chain_tail, noise, 0)
            for pn, val in (("amp", 2.5), ("period", 3.0)):
                _setpar(noise, pn, val)
            _bindexpr(noise, "amp", f"1.5 + 4.0*{rex['mid']}")
            chain_tail = noise
        if nullp is not None:
            _connect(chain_tail, nullp, 0)
            chain_tail = nullp
        # Close the feedback loop so the forces integrate into the particles.
        _setpar_any(particle, ("targetfeedbackpop", "feedbackpop", "targetpop"), chain_tail)
        render_pop = chain_tail
        try:
            render_pop.render = True
            render_pop.display = True
        except Exception:
            pass
        # A Geometry COMP renders whichever POP inside it has its render flag on
        # (set above), the same as SOPs; no parameter names it.
        built_pops = True
    else:
        # --- The storm: the curl-noise engine, dense, fast and fine-grained,
        # rendered as an additive cloud (a different animal from the Flow scene:
        # twice the particles, a third the point size, faster, stronger trails).
        sim = _create(c, "scriptCHOP", "sim", -560, 0)
        _install_callbacks(sim, "particles_chop.py")
        _setpar(sim, "Mode", "flow")
        _setpar(sim, "Palette", palette)
        _setpar(sim, "Count", min(int(count), 40000))
        _setpar(sim, "Pointsize", 0.03)
        _setpar(sim, "Speed", 2.6)
        _setpar(sim, "Scale", 0.32)
        _setpar(sim, "Evolve", 0.22)
        sph = geo.create("sphereSOP", "shape")
        _setpar(sph, "type", "poly"); _setpar(sph, "rows", 4); _setpar(sph, "cols", 6)
        try:
            sph.render = sph.display = True
        except Exception:
            pass
        _instance_channels(geo, sim)
        built_pops = False

    # Additive soft material + 3-point lighting (lights matter for the POP path;
    # the additive constant ignores them).
    mat = _glow_mat(c, reactor)
    _setpar(geo, "material", mat)
    lights = _light_rig(c, reactor, shadows=False)
    _orbit(c, geo, default=5.0)
    cam = _camera(c, dist=8.0 if built_pops else 13.0)
    r = _render(c, geo, cam, lights)
    tr = _trails(c, r, amount=0.94)
    out = _glow(c, tr, size=22.0, x=640, threshold=0.3)
    if built_pops:
        _ensure_active(c)      # no driver needed (GPU, time-dependent), but
    else:                      # build_all still binds Active to the decks
        _cook_driver(c, c.op("sim"))
        try:
            c.op("sim").cook(force=True)
        except Exception:
            pass
    _ensure_visible(c, geo, out, "storm")
    print(f"[td_build] built particle storm -> {c.path} "
          f"({'POPs' if built_pops else 'curl-noise engine, 40k additive points'})")
    return c


def build_bohmian(dest=None, name="hydrogen", palette="ice", count=20000):
    """Bohmian (pilot-wave) electrons in hydrogen orbitals.

    A cloud of electrons sampled from |psi|^2 is advected by the de Broglie-Bohm
    guidance velocity (physics/hydrogen.py, unit-tested). For m != 0 orbitals
    they circulate about the z-axis (glowing rings under the trail feedback);
    for real / m = 0 orbitals they sit nearly still; superpositions slosh. Pick
    the state on the sim's 'Orbital' menu.

    Rendered by instancing with the compiled glow material + a 3-point light
    rig (a POP point-cloud path was tried; the operator type does not exist on
    2025.3, and instancing is fast enough at these counts).
    """
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    reactor = dest.op("Reactor")

    sim = _create(c, "scriptCHOP", "sim", -500, 0)
    _install_callbacks(sim, "hydrogen_chop.py")
    _setpar(sim, "Palette", palette)
    _setpar(sim, "Count", count)
    _setpar(sim, "Pointsize", 0.04)

    # Tens of thousands of tiny additive points: each electron is a faint dot,
    # where they crowd the glow adds up, the long trails draw the circulation
    # as rings and the wide bloom softens it all -- a probability cloud, not a
    # ball of marbles.
    geo, _ = _instanced_geo(c, sim, "geo", (1.0, 1.0, 1.0), -260, 0, look="soft", rows=3, cols=5)
    _orbit(c, geo, default=6.0)            # slow camera spin to read the 3D shape
    cam = _camera(c, dist=18.0, tilt=-10.0)
    # The additive cloud ignores lights; the rig is here for the lit fallback
    # _ensure_visible switches to if the cloud renders black on this build.
    lights = _light_rig(c, dest_reactor(dest), shadows=False)
    r = _render(c, geo, cam, lights)
    tr = _trails(c, r, amount=0.94)         # trails turn circulation into rings
    out = _glow(c, tr, size=26.0, x=640, threshold=0.25)
    _cook_driver(c, sim)
    try:
        sim.cook(force=True)
    except Exception:
        pass
    _ensure_visible(c, geo, out, "hydrogen")
    print(f"[td_build] built Bohmian hydrogen -> {c.path}")
    return c
