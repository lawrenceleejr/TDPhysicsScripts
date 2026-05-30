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

# Stable scene table used by build_all (label, builder, kwargs).
SCENES = [
    ("Ising", "build_ising", {}),
    ("N-Body", "build_nbody", {}),
    ("Flow", "build_particles", {"mode": "flow", "name": "flow", "palette": "cyber"}),
    ("Soft Body", "build_particles", {"mode": "softbody", "name": "softbody", "palette": "synth"}),
    ("LHC Tracks", "build_lhc", {}),
    ("Open Data", "build_opendata", {}),
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


def _expr(o, name, expression):
    """Set a parameter to an expression; never raise."""
    try:
        p = getattr(o.par, name)
        p.expr = expression
        p.mode = ParMode.EXPRESSION  # noqa: F821 (TD global)
        return True
    except Exception as e:
        print(f"[td_build] could not set expr {o.path}.{name}: {e}")
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


def _cook_driver(container, sim_op):
    """Make ``sim_op`` cook every frame while the scene's Active flag is on."""
    page = _custom_page(container, "VJ")
    if not hasattr(container.par, "Active"):
        page.appendToggle("Active")
        _setpar(container, "Active", True)
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
    _setpar(drv, "fs", True)     # Frame Start
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


def _glow(container, src, name="out", size=14.0, x=460, y=200):
    """Add a soft bloom over solid black and output a stable 'out' null TOP."""
    blur = _create(container, "blurTOP", name + "_blur", x, y - 150)
    _connect(src, blur)
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
def build_all(dest=None, name="PhysicsVJ"):
    dest = dest or op("/")  # noqa: F821
    base = _create(dest, "baseCOMP", name)

    # Top-level controls: an A/B deck pair + crossfader (DJ style).
    #   Scene      -> deck A (the live cut / instant switch)
    #   Nextscene  -> deck B (what you crossfade toward)
    #   Crossfade  -> 0 = all A, 1 = all B
    # A hard cut is just snapping Crossfade; a smooth blend is riding it.
    menu_names = [s[1].replace("build_", "") + str(i) for i, s in enumerate(SCENES)]
    labels = [s[0] for s in SCENES]
    page = base.appendCustomPage("PhysicsVJ")
    for parname, deflt in (("Scene", 0), ("Nextscene", 1)):
        mp = page.appendMenu(parname)[0]
        mp.menuNames = menu_names
        mp.menuLabels = labels
        mp.val = menu_names[deflt]
    cf = page.appendFloat("Crossfade", label="Crossfade A/B")[0]
    cf.val = 0.0
    try:
        base.par.Crossfade.normMin, base.par.Crossfade.normMax = 0.0, 1.0
        base.par.Crossfade.clampMin = base.par.Crossfade.clampMax = True
    except Exception:
        pass
    page.appendPulse("Cut", label="Cut To B (commit)")
    page.appendToggle("Freerunall", label="Freerun All (evolve hidden scenes)")
    _setpar(base, "Freerunall", False)

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
    _expr(switch_a, "index", "parent().par.Scene.menuIndex")
    _expr(switch_b, "index", "parent().par.Nextscene.menuIndex")

    cross = _create(base, "crossTOP", "crossfade", 160, 0)
    _connect(switch_a, cross, 0)
    _connect(switch_b, cross, 1)
    _expr(cross, "cross", "parent().par.Crossfade")

    final = _create(base, "nullTOP", "out", 340, 0)
    _connect(cross, final)
    try:
        final.viewer = True
    except Exception:
        pass

    print(f"[td_build] built PhysicsVJ with {len(outs)} scenes -> {base.path}")
    print("[td_build] View 'out' in Perform mode. Cut with 'Scene'; blend with "
          "'Nextscene' + 'Crossfade'.")
    return base
