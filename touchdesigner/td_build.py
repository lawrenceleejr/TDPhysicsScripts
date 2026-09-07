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
    try:
        src.outputConnectors[0].connect(dst.inputConnectors[index])
    except Exception as e:
        print(f"[td_build] could not connect {src.path} -> {dst.path}[{index}]: {e}")


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


# ---------------------------------------------------------------------------
# Reusable visual building blocks
# ---------------------------------------------------------------------------
def _instanced_geo(container, chop, name, base_color, x, y):
    """Geometry COMP that instances a small glowing sphere at each CHOP sample.

    Expects the CHOP channels produced by the nbody/particles callbacks:
    c0,c1,c2 = position; c3,c4,c5 = colour; c6 = scale.
    """
    geo = _create(container, "geometryCOMP", name, x, y)
    for child in list(geo.children):
        try:
            child.destroy()
        except Exception:
            pass
    sph = geo.create("sphereSOP", "shape")
    _setpar(sph, "type", "poly")
    _setpar(sph, "rows", 6)
    _setpar(sph, "cols", 8)
    try:
        sph.render = True
        sph.display = True
    except Exception:
        pass

    mat = _create(container, "constantMAT", name + "_mat", x, y - 130)
    _setpar(mat, "colorr", base_color[0])
    _setpar(mat, "colorg", base_color[1])
    _setpar(mat, "colorb", base_color[2])
    _setpar(mat, "applypointcolor", False)

    _setpar(geo, "material", mat)          # assign the OP (TD stores the path)
    _setpar(geo, "instancing", True)
    _setpar(geo, "instanceop", chop)
    _setpar(geo, "instancetx", "c0")
    _setpar(geo, "instancety", "c1")
    _setpar(geo, "instancetz", "c2")
    _setpar(geo, "instancesx", "c6")
    _setpar(geo, "instancesy", "c6")
    _setpar(geo, "instancesz", "c6")
    # Per-instance colour (gradient by speed/pt). Falls back to MAT colour if
    # the colour mode name differs on this version -- still looks good.
    _setpar(geo, "instancecolormode", "mult")
    _setpar(geo, "instancer", "c3")
    _setpar(geo, "instanceg", "c4")
    _setpar(geo, "instanceb", "c5")
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


def _camera(container, dist, name="cam", tilt=-12.0, x=-200, y=200):
    cam = _create(container, "cameraCOMP", name, x, y)
    _setpar(cam, "tz", dist)
    _setpar(cam, "ty", dist * 0.12)
    _setpar(cam, "rx", tilt)
    return cam


def _light(container, name="light", x=-200, y=120):
    light = _create(container, "lightCOMP", name, x, y)
    _setpar(light, "tx", 5.0)
    _setpar(light, "ty", 8.0)
    _setpar(light, "tz", 6.0)
    return light


def _render(container, geo, cam, light, name="render", x=200, y=200, w=1280, h=720):
    r = _create(container, "renderTOP", name, x, y)
    _setpar(r, "camera", cam)
    _setpar(r, "geometry", geo)
    if light is not None:
        _setpar(r, "lights", light)
    _setpar(r, "resolutionw", w)
    _setpar(r, "resolutionh", h)
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

    blur = _create(container, "blurTOP", name + "_blur", x, y - 150)
    _connect(bright, blur)
    _setpar(blur, "size", size)

    black = _create(container, "constantTOP", name + "_bg", x, y - 300)
    _setpar(black, "colorr", 0.0)
    _setpar(black, "colorg", 0.0)
    _setpar(black, "colorb", 0.0)
    _setpar(black, "alpha", 1.0)

    comp = _create(container, "compositeTOP", name + "_glow", x + 180, y)
    _setpar(comp, "operand", "add")
    _connect(src, comp, 0)
    _connect(blur, comp, 1)
    _connect(black, comp, 2)

    out = _create(container, "nullTOP", name, x + 360, y)
    _connect(comp, out)
    try:
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

    fb = _create(container, "feedbackTOP", name + "_fb", x, y - 150)
    decay = _create(container, "levelTOP", name + "_decay", x + 150, y - 150)
    _connect(fb, decay)
    _expr(decay, "opacity", "parent().par.Trail")

    comp = _create(container, "compositeTOP", name, x + 150, y)
    _setpar(comp, "operand", "over")      # live frame over the fading history
    _connect(src, comp, 0)
    _connect(decay, comp, 1)
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
    _setpar(hud, "resolutionw", 1280)
    _setpar(hud, "resolutionh", 720)
    _install_callbacks(hud, "mass_hud_top.py")

    title = _create(container, "textTOP", "mass_title", x, y + 320)
    _setpar(title, "resolutionw", 1280)
    _setpar(title, "resolutionh", 720)
    _setpar(title, "text",
            "DIMUON INVARIANT MASS  [GeV]      peaks L>R:  J/psi   Upsilon   Z")
    _setpar(title, "fontsizex", 26)
    _setpar(title, "fontsizey", 26)
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

    level = _create(container, "levelTOP", "hud_level", x + 340, y + 150)
    _connect(label, level)
    _expr(level, "opacity", "parent().par.Hudopacity")

    over = _create(container, "compositeTOP", "hud_over", x + 520, y)
    _setpar(over, "operand", "over")
    _connect(level, over, 0)              # HUD on top
    _connect(scene_top, over, 1)          # live scene behind

    out = _create(container, "nullTOP", "out", x + 700, y)
    _connect(over, out)
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
    _setpar(sim, "resolutionw", 256)
    _setpar(sim, "resolutionh", 256)
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
    geo, _ = _instanced_geo(c, sim, "geo", (1.0, 0.55, 0.15), -260, 0)
    _orbit(c, geo, default=7.0)
    cam = _camera(c, dist=26.0)
    light = _light(c)
    r = _render(c, geo, cam, light)
    tr = _trails(c, r, amount=0.85)
    out = _glow(c, tr, size=16.0, x=640)
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
    geo, _ = _instanced_geo(c, sim, "geo", base_col, -260, 0)
    _orbit(c, geo, default=10.0 if mode == "softbody" else 4.0)
    dist = 10.0 if mode == "softbody" else 15.0
    cam = _camera(c, dist=dist)
    light = _light(c)
    r = _render(c, geo, cam, light)
    tr = _trails(c, r, amount=0.9 if mode == "flow" else 0.8)
    out = _glow(c, tr, size=18.0, x=640)
    _cook_driver(c, sim)
    try:
        sim.cook(force=True)
    except Exception:
        pass
    print(f"[td_build] built Particles ({mode}) -> {c.path}")
    return c


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
    cam = _camera(c, dist=12.0, tilt=-8.0)
    r = _render(c, geo, cam, None)
    tr = _trails(c, r, amount=0.0)
    out = _glow(c, tr, size=12.0, x=640)
    _cook_driver(c, sim)
    try:
        sim.cook(force=True)
    except Exception:
        pass
    print(f"[td_build] built LHC Tracks -> {c.path}")
    return c


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

    # A CHOP to SOP matches channels to attributes *by name*: Cd(0)..Cd(3),
    # the same convention SOP to CHOP emits. The Script CHOP puts out c0..c3,
    # so a Rename CHOP gives them those names (a rename is free), and the
    # CHOP to SOP is told exactly which channels and which attribute.
    cd_names = "Cd(0) Cd(1) Cd(2) Cd(3)"
    named = _create(c, "renameCHOP", "state_cd", -400, -180)
    _connect(state, named)
    _setpar_any(named, ("renamefrom", "from"), "c0 c1 c2 c3")
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
    for o in (lines, state, named, paint):
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
        print(f"[td_build] feynman join: state[{state.numChans}] = {chans(state)}; "
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
    r = _render(c, geo, cam, None)
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
    _setpar(watch, "pars", "Scene Nextscene Crossfade Freerunall")
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

    # Paint the initial LED state now.
    try:
        from touchdesigner.callbacks import apc_mini
        apc_mini.reset(c)
    except Exception as e:
        print(f"[td_build] initial APC repaint skipped: {e}")

    print(f"[td_build] built APC mini mk2 surface -> {c.path} (target {target_path})")
    print("[td_build] In TD's MIDI Device Mapper, map your APC mini mk2 to "
          f"device id {device}. Press the top-right button (or the Reset par) "
          "to resync LEDs.")
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
    _expr(geo, "ry", "parent().par.Orbit * absTime.seconds")


# ---------------------------------------------------------------------------
# Master build: every scene + a live switcher
# ---------------------------------------------------------------------------
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
    except Exception as e:
        print(f"[td_build] top-level control page setup hit a snag: {e}")

    # Audio + tempo engines, built first so scenes/shaders can bind to them.
    reactor = build_reactor(dest=base)
    reactor.nodeX, reactor.nodeY = -600, 460
    tempo = build_tempo(dest=base)
    tempo.nodeX, tempo.nodeY = -600, 360

    # Master FX controls (drive the GLSL post chain).
    fxpage = base.appendCustomPage("Look")
    fxpage.appendFloat("Kaleido", label="Kaleidoscope")[0].val = 0.0
    fxpage.appendFloat("Rgbshift", label="RGB Shift (px)")[0].val = 1.5
    fxpage.appendFloat("Punch", label="Beat Punch")[0].val = 1.0
    fxpage.appendToggle("Wavevis", label="Waveform Overlay")[0].val = False
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
    for i, (label, builder, kwargs) in enumerate(SCENES):
        scene = globals()[builder](dest=base, **kwargs)
        scene.nodeX, scene.nodeY = -600, 260 - i * 170
        # Cook a scene only while its deck actually contributes to the mix
        # (deck A unless fully faded to B, deck B unless fully faded to A),
        # or when Freerun All is on. Keeps it to one live sim except mid-fade.
        _expr(
            scene, "Active",
            f"int((parent().par.Scene.menuIndex == {i} and parent().par.Crossfade < 1) "
            f"or (parent().par.Nextscene.menuIndex == {i} and parent().par.Crossfade > 0) "
            f"or parent().par.Freerunall)",
        )
        out = scene.op("out")
        if out is not None:
            outs.append(out)

    # Two selector switches (deck A and deck B) blended by a Cross TOP.
    switch_a = _create(base, "switchTOP", "deck_a", -40, 80)
    switch_b = _create(base, "switchTOP", "deck_b", -40, -80)
    for idx, out in enumerate(outs):
        _connect(out, switch_a, idx)
        _connect(out, switch_b, idx)
    # A Cross TOP cooks both inputs every frame, so with a plain A/B wiring the
    # armed-but-invisible deck would render its whole chain (render, trails,
    # bloom -- or a raymarch) for nothing. Instead, whenever a deck contributes
    # nothing to the mix it points at the *same* scene as the other deck; TD
    # cooks that node once per frame, so the idle deck is free. The scenes'
    # Active flags below use the same rule, so sim and render agree.
    _expr(switch_a, "index",
          "parent().par.Scene.menuIndex if parent().par.Crossfade < 1 "
          "else parent().par.Nextscene.menuIndex")
    _expr(switch_b, "index",
          "parent().par.Nextscene.menuIndex if parent().par.Crossfade > 0 "
          "else parent().par.Scene.menuIndex")

    cross = _create(base, "crossTOP", "crossfade", 160, 0)
    _connect(switch_a, cross, 0)
    _connect(switch_b, cross, 1)
    _expr(cross, "cross", "parent().par.Crossfade")

    # Creative waveform overlay (toggled by 'Wavevis'), then the GLSL post chain.
    mixed = _waveform_overlay(base, cross, reactor, x=300)
    post = _post_fx(base, mixed, reactor, tempo, x=480)

    final = _create(base, "nullTOP", "out", 700, 0)
    _connect(post, final)
    try:
        final.viewer = True
    except Exception:
        pass

    # Make the whole show breathe: bind scene params to the audio + tempo.
    _reactive_bindings(base, reactor, tempo)

    print(f"[td_build] built PhysicsVJ with {len(outs)} scenes -> {base.path}")
    print("[td_build] View 'out' in Perform mode. Cut with 'Scene'; blend with "
          "'Nextscene' + 'Crossfade'.")

    # An APC mini mk2 surface to run the whole show from hardware.
    if apc:
        try:
            build_apc(dest=dest, target=base)
        except Exception as e:
            print(f"[td_build] APC surface skipped: {e}")

    return base


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


def _react_exprs(reactor):
    """Expression strings reading the Reactor's analyze CHOP (or constants)."""
    keys = ("bass", "mid", "high", "level", "beat", "bpm")
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
        page.appendPulse("Reseed", label="Reseed")
    res = 320

    state = _create(c, "glslTOP", "rd_state", -200, 0)
    _setpar(state, "resolutionw", res)
    _setpar(state, "resolutionh", res)
    _setpar(state, "format", "rgba32float")
    _setpar(state, "pixeldat", _shader_dat(c, "rd_state_src", "reaction_diffusion.frag", -200, 150))

    fb = _create(c, "feedbackTOP", "rd_fb", -400, 0)
    _setpar(fb, "top", state)
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
    _setpar(color, "pixeldat", _shader_dat(c, "rd_color_src", "rd_color.frag", 20, 150))
    _glsl_uniforms(color, [
        ("uTime", "absTime.seconds"), ("uLevel", rex["level"]),
        ("uHigh", rex["high"]), ("uBeat", rex["beat"]),
        ("uPalette", "parent().par.Palette.menuIndex"),
    ])

    out = _glow(c, color, size=8.0, x=240)
    _cook_driver(c, state)  # keep the feedback advancing while the scene is live
    try:
        c.par.Reseed.pulse()  # seed the pattern now
    except Exception:
        pass
    print(f"[td_build] built Reaction-Diffusion -> {c.path}")
    return c


def build_raymarch(dest=None, name="sdf", palette_index=2):
    """An audio-reactive raymarched SDF: morphing metaballs that twist to the
    bass and orbit on the bar (single compiled fragment shader)."""
    dest = dest or op("/")  # noqa: F821
    c = _create(dest, "baseCOMP", name)
    reactor, tempo = dest.op("Reactor"), dest.op("Tempo")
    rex = _react_exprs(reactor)
    tex = _tempo_exprs(tempo)
    _glsl_scene_palette(c, palette_index)
    page = _custom_page(c, "VJ")
    if not hasattr(c.par, "Reseed"):
        page.appendPulse("Reseed", label="New Form")

    sdf = _create(c, "glslTOP", "sdf", -120, 0)
    _setpar(sdf, "resolutionw", 1280)
    _setpar(sdf, "resolutionh", 720)
    _setpar(sdf, "pixeldat", _shader_dat(c, "sdf_src", "raymarch.frag", -120, 160))
    _glsl_uniforms(sdf, [
        ("uTime", "absTime.seconds"),
        ("uBass", rex["bass"]), ("uMid", rex["mid"]), ("uHigh", rex["high"]),
        ("uLevel", rex["level"]), ("uBeat", rex["beat"]), ("uBar", tex["bar"]),
        ("uPalette", "parent().par.Palette.menuIndex"),
    ])

    out = _glow(c, sdf, size=10.0, x=120)
    _cook_driver(c, sdf)
    print(f"[td_build] built Raymarch SDF -> {c.path}")
    return c


# ---------------------------------------------------------------------------
# Master post-FX + waveform overlay
# ---------------------------------------------------------------------------
def _waveform_overlay(container, src, reactor, name="wave", x=300, y=0):
    """Composite a creative waveform/spectrum visualiser over ``src``,
    gated by the 'Wavevis' toggle. Returns the mixed TOP (or ``src``)."""
    if reactor is None or reactor.op("wave_tex") is None:
        return src
    rex = _react_exprs(reactor)
    ov = _create(container, "glslTOP", name + "_glsl", x, y - 160)
    _setpar(ov, "resolutionw", 1280)
    _setpar(ov, "resolutionh", 720)
    _setpar(ov, "pixeldat", _shader_dat(container, name + "_src", "waveform_tunnel.frag", x, y - 320))
    _connect(reactor.op("wave_tex"), ov, 0)
    _connect(reactor.op("spec_tex"), ov, 1)
    _glsl_uniforms(ov, [
        ("uTime", "absTime.seconds"), ("uLevel", rex["level"]),
        ("uBeat", rex["beat"]), ("uBass", rex["bass"]), ("uPalette", 3),
    ])

    lvl = _create(container, "levelTOP", name + "_op", x + 160, y - 160)
    _connect(ov, lvl)
    _expr(lvl, "opacity", "parent().par.Wavevis")
    comp = _create(container, "compositeTOP", name, x + 160, y)
    _setpar(comp, "operand", "over")
    _connect(lvl, comp, 0)   # overlay on top
    _connect(src, comp, 1)
    return comp


def _post_fx(container, src, reactor, tempo, name="post", x=480, y=0):
    """The master GLSL post chain: beat punch, chromatic aberration, optional
    kaleidoscope, scanline shimmer and vignette. Returns the processed TOP."""
    rex = _react_exprs(reactor)
    tex = _tempo_exprs(tempo)
    post = _create(container, "glslTOP", name, x, y)
    _setpar(post, "pixeldat", _shader_dat(container, name + "_src", "post_fx.frag", x, y - 170))
    _connect(src, post, 0)
    _glsl_uniforms(post, [
        ("uTime", "absTime.seconds"), ("uLevel", rex["level"]),
        ("uBeat", rex["beat"]), ("uHigh", rex["high"]), ("uBar", tex["bar"]),
        ("uKaleido", "parent().par.Kaleido"),
        ("uRGBShift", "parent().par.Rgbshift"),
        ("uPunch", "parent().par.Punch"),
    ])
    return post


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


# ---------------------------------------------------------------------------
# Professional lighting + a compiled glow material
# ---------------------------------------------------------------------------
def _light_rig(container, reactor=None, x=-200, y=300):
    """A 3-point rig: warm key, cool fill, bright rim -- the lighting that
    makes 3D read as 'pro'. Rim intensity pulses with the beat if a Reactor is
    given. Returns a list of Light COMPs to hand to a Render TOP."""
    rex = _react_exprs(reactor)
    def light(name, pos, rgb, dy):
        L = _create(container, "lightCOMP", name, x, y - dy)
        _setpar(L, "tx", pos[0]); _setpar(L, "ty", pos[1]); _setpar(L, "tz", pos[2])
        # Light COMP colour is cr/cg/cb (colorr/g/b is the Constant TOP/MAT spelling).
        _setpar_any(L, ("cr", "colorr"), rgb[0])
        _setpar_any(L, ("cg", "colorg"), rgb[1])
        _setpar_any(L, ("cb", "colorb"), rgb[2])
        return L

    key = light("key", (6.0, 7.0, 6.0), (1.0, 0.85, 0.65), 0)
    fill = light("fill", (-7.0, 2.0, 4.0), (0.4, 0.6, 1.0), 120)
    _setpar(fill, "dimmer", 0.5)
    rim = light("rim", (0.0, 4.0, -8.0), (0.9, 0.95, 1.0), 240)
    if reactor is not None:
        _bindexpr(rim, "dimmer", f"1.0 + 2.0*{rex['beat']}")
    return [key, fill, rim]


def _glow_mat(container, reactor=None, name="glow_mat", x=-200, y=-180):
    """Compiled GLSL MAT: emissive core + Fresnel rim, audio-reactive. Looks
    expensive, costs little, and blooms through the scene glow pass. Falls back
    to a Constant MAT (showing the instance colours) if glslMAT is unavailable."""
    rex = _react_exprs(reactor)
    mat = _try_create(container, "glslMAT", name, x, y)
    if mat is None:
        mat = _create(container, "constantMAT", name, x, y)
        _setpar(mat, "applypointcolor", True)
        return mat
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
def build_pops(dest=None, name="pops", palette="acid", count=200000):
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

    # --- Attempt the real POP network -------------------------------------
    emitter = _try_create(geo, "spherePOP", "emitter")
    particle = _try_create(geo, "particlePOP", "sim") if emitter is not None else None

    if emitter is not None and particle is not None:
        _setpar_any(emitter, ("radius", "rad", "radx"), 1.5)
        _connect(emitter, particle, 0)
        _setpar_any(particle, ("maxparticles", "maxpoints"), int(count))
        _setpar_any(particle, ("birthrate", "birth"), max(1000, int(count / 20)))
        _setpar_any(particle, ("lifeexpect", "life", "lifespan"), 6.0)
        _setpar_any(particle, ("lifevariance", "lifevar"), 2.0)
        _setpar_any(particle, ("velocitydamping", "damping", "drag"), 0.04)
        _setpar_any(particle, ("enabletimeintegration", "integrate"), True, quiet=True)
        # Forces in a feedback loop: a radial push + turbulent noise.
        force = _try_create(geo, "forceradialPOP", "force")
        noise = _try_create(geo, "noisePOP", "turb")
        nullp = _try_create(geo, "nullPOP", "loop")
        chain_tail = particle
        if force is not None:
            _connect(chain_tail, force, 0)
            _bindexpr_any(force, ("force", "strength", "magnitude", "amount", "scale"),
                          f"-2.0 - 6.0*{rex['bass']}")
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
        # --- Fallback: proven curl-noise flow at a high particle count -----
        print("[td_build] POPs unavailable -- falling back to curl-noise flow.")
        sim = _create(geo, "scriptCHOP", "sim", -460, 0)
        _install_callbacks(sim, "particles_chop.py")
        _setpar(sim, "Mode", "flow")
        _setpar(sim, "Palette", palette)
        _setpar(sim, "Count", min(int(count), 120000))
        _setpar(sim, "Pointsize", 0.012)
        sph = geo.create("sphereSOP", "shape")
        _setpar(sph, "type", "poly"); _setpar(sph, "rows", 4); _setpar(sph, "cols", 6)
        try:
            sph.render = sph.display = True
        except Exception:
            pass
        _setpar(geo, "instancing", True)
        _setpar(geo, "instanceop", sim)
        for p, ch in (("instancetx", "c0"), ("instancety", "c1"), ("instancetz", "c2"),
                      ("instancesx", "c6"), ("instancesy", "c6"), ("instancesz", "c6"),
                      ("instancer", "c3"), ("instanceg", "c4"), ("instanceb", "c5")):
            _setpar(geo, p, ch)
        _setpar(geo, "instancecolormode", "mult")
        built_pops = False

    # Compiled glow material + 3-point lighting (shared by both paths).
    mat = _glow_mat(c, reactor)
    _setpar(geo, "material", mat)
    lights = _light_rig(c, reactor)
    _orbit(c, geo, default=5.0)
    cam = _camera(c, dist=8.0)
    r = _render(c, geo, cam, lights[0])
    try:
        r.par.lights = " ".join(l.name for l in lights)  # all three
    except Exception:
        pass
    tr = _trails(c, r, amount=0.92)
    out = _glow(c, tr, size=18.0, x=640)
    if built_pops:
        _ensure_active(c)      # no driver needed (GPU, time-dependent), but
    else:                      # build_all still binds Active to the decks
        _cook_driver(c, geo.op("sim"))
    print(f"[td_build] built POP particle storm -> {c.path} "
          f"({'POPs' if built_pops else 'flow fallback'})")
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

    geo = _create(c, "geometryCOMP", "geo", -260, 0)
    for child in list(geo.children):
        try:
            child.destroy()
        except Exception:
            pass
    sph = geo.create("sphereSOP", "shape")
    _setpar(sph, "type", "poly"); _setpar(sph, "rows", 4); _setpar(sph, "cols", 6)
    try:
        sph.render = sph.display = True
    except Exception:
        pass
    _setpar(geo, "instancing", True)
    _setpar(geo, "instanceop", sim)
    for p, ch in (("instancetx", "c0"), ("instancety", "c1"), ("instancetz", "c2"),
                  ("instancesx", "c6"), ("instancesy", "c6"), ("instancesz", "c6"),
                  ("instancer", "c3"), ("instanceg", "c4"), ("instanceb", "c5")):
        _setpar(geo, p, ch)
    _setpar(geo, "instancecolormode", "mult")

    mat = _glow_mat(c, reactor)
    _setpar(geo, "material", mat)
    lights = _light_rig(c, reactor)
    _orbit(c, geo, default=8.0)            # slow camera spin to read the 3D shape
    cam = _camera(c, dist=18.0, tilt=-10.0)
    r = _render(c, geo, cam, lights[0])
    try:
        r.par.lights = " ".join(l.name for l in lights)
    except Exception:
        pass
    tr = _trails(c, r, amount=0.9)          # trails turn circulation into rings
    out = _glow(c, tr, size=16.0, x=640)
    _cook_driver(c, sim)
    try:
        sim.cook(force=True)
    except Exception:
        pass
    print(f"[td_build] built Bohmian hydrogen -> {c.path}")
    return c
