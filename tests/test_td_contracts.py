"""Contracts between the pieces of the TouchDesigner layer.

The builder, the callbacks and the APC controller each hold a copy of some
fact about the show -- which scene sits at which index, what a scene's re-fire
pulse is called, which callback and shader files exist. None of that can be
exercised inside TouchDesigner from here, but all of it can be cross-checked
statically, and the APC controller's decision logic can be run against a stub
of the handful of TD calls it makes. These tests are what would have caught
the Cut/Freerun buttons arming scenes once the grid grew past six columns.
"""

import ast
import importlib
import inspect
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TD = os.path.join(ROOT, "touchdesigner")
CALLBACKS = os.path.join(TD, "callbacks")


def _src(*parts):
    with open(os.path.join(ROOT, *parts)) as fh:
        return fh.read()


def _scene_names():
    """Scene COMP names in build_all order, derived from td_build.SCENES."""
    td_build = importlib.import_module("touchdesigner.td_build")
    names = []
    for _, fn_name, kwargs in td_build.SCENES:
        if "name" in kwargs:
            names.append(kwargs["name"])
        else:
            sig = inspect.signature(getattr(td_build, fn_name))
            names.append(sig.parameters["name"].default)
    return names


# --- files the builder refers to must exist ------------------------------
def test_every_callback_the_builder_installs_exists():
    src = _src("touchdesigner", "td_build.py")
    referenced = set(re.findall(r'_install_callbacks\([^,]+,\s*"([^"]+\.py)"', src))
    assert referenced, "no _install_callbacks calls found"
    for name in referenced:
        assert os.path.isfile(os.path.join(CALLBACKS, name)), name
    # and every callback file is installed by something (no orphans)
    on_disk = {f for f in os.listdir(CALLBACKS) if f.endswith(".py")}
    assert on_disk == referenced, on_disk ^ referenced


def test_every_shader_the_builder_loads_exists():
    src = _src("touchdesigner", "td_build.py")
    referenced = set(re.findall(r'_shader_dat\([^)]*?"([^"]+\.(?:frag|vert|pixel|glsl))"', src))
    referenced.add("common.glsl")
    assert len(referenced) >= 8, referenced
    shader_dir = os.path.join(TD, "shaders")
    on_disk = set(os.listdir(shader_dir))
    assert referenced <= on_disk, referenced - on_disk
    assert on_disk <= referenced, on_disk - referenced  # no orphaned shader files


def test_shaders_take_resolution_from_td_not_a_bound_uniform():
    """dd63ac5: uRes as a bound uniform was (0,0) when it failed to bind and
    blacked out the master output. Every fragment shader now #defines it from
    uTDOutputInfo, and the builder must not spend a Vectors slot on it."""
    shader_dir = os.path.join(TD, "shaders")
    for f in os.listdir(shader_dir):
        if f.endswith(".frag"):
            text = open(os.path.join(shader_dir, f)).read()
            assert "#define uRes (uTDOutputInfo.res.zw)" in text, f
            assert "uniform vec2 uRes" not in text, f
    assert '"uRes"' not in _src("touchdesigner", "td_build.py")


def test_builder_reads_cook_driver_frame_start_flag():
    """The Execute DAT toggle is 'framestart'; a wrong spelling is silently
    swallowed by _setpar and the hidden scenes would simply never advance."""
    src = _src("touchdesigner", "td_build.py")
    assert '_setpar(drv, "framestart", True)' in src


# --- the APC controller agrees with the builder -------------------------
def test_apc_scene_table_matches_build_all():
    ns = _load_apc()
    assert ns["SCENE_NAMES"] == _scene_names()
    assert len(ns["REFIRE_PULSE"]) == len(ns["SCENE_NAMES"])


def test_apc_refire_pulses_exist_on_the_scenes():
    """Each scene's re-fire pulse must be a parameter that scene actually has:
    on its Script OP callbacks (numpy scenes) or appended by the builder
    (GLSL scenes)."""
    ns = _load_apc()
    td_src = _src("touchdesigner", "td_build.py")
    # pulses the builder appends on GLSL scene COMPs
    builder_pulses = set(re.findall(r'appendPulse\("([A-Za-z]+)"', td_src))
    # pulses each callback file defines
    cb_pulses = {}
    for f in os.listdir(CALLBACKS):
        if f.endswith(".py"):
            cb_pulses[f] = set(re.findall(r'appendPulse\("([A-Za-z]+)"', _src("touchdesigner", "callbacks", f)))
    # which callback each scene's live op uses (from the builder source)
    scene_cb = {
        "ising": "ising_top.py", "nbody": "nbody_chop.py", "flow": "particles_chop.py",
        "softbody": "particles_chop.py", "lhc": "lhc_sop.py", "opendata": "opendata_sop.py",
        "hydrogen": "hydrogen_chop.py", "feynman": "feynman_chop.py",
        "pops": "particles_chop.py",   # the Flow fallback; the POP path has no sim
    }
    for name, pulse in zip(ns["SCENE_NAMES"], ns["REFIRE_PULSE"]):
        if name in scene_cb:
            assert pulse in cb_pulses[scene_cb[name]] or pulse in builder_pulses, (name, pulse)
        else:  # rd, sdf: GLSL scenes, pulse lives on the COMP
            assert pulse in builder_pulses, (name, pulse)


# --- run the controller against a stub of the TD API --------------------
class _Par:
    def __init__(self, owner, name, val=0, menu=None):
        self.owner, self.name, self.val = owner, name, val
        self.menuNames = menu or []
        self.pulses = 0
        self.normMin = self.normMax = 0
        self.clampMin = False

    def eval(self):
        return self.val

    @property
    def menuIndex(self):
        return int(self.val)

    @menuIndex.setter
    def menuIndex(self, i):
        self.val = int(i)

    def pulse(self):
        self.pulses += 1
        if self.name == "Cut":     # what td_build's cutter DAT does
            o = self.owner
            o.par.Scene.val = o.par.Nextscene.val
            o.par.Crossfade.val = 0.0


class _Pars:
    pass


class _Op:
    def __init__(self, path, children=None, pars=()):
        self.path = path
        self.par = _Pars()
        self.children = children or {}
        for name, val in pars:
            setattr(self.par, name, _Par(self, name, val))

    def op(self, name):
        return self.children.get(name.lstrip("./"))

    def parent(self):
        return None


class _LedOut:
    def __init__(self):
        self.sent = []

    def sendNoteOn(self, channel, note, velocity):
        self.sent.append((channel, note, velocity))


def _fake_show(n_scenes, palettes):
    scenes = {}
    for i, name in enumerate(_scene_names()[:n_scenes]):
        sim = _Op("/PhysicsVJ/%s/sim" % name, pars=[("Palette", 0), ("Reset", 0), ("Newevent", 0),
                                                     ("Nextevent", 0), ("Reseed", 0), ("Pointsize", 0.02)])
        scenes[name] = _Op("/PhysicsVJ/" + name, {"sim": sim},
                           pars=[("Trail", 0.0), ("Orbit", 0.0), ("Palette", 0), ("Reseed", 0)])
    show = _Op("/PhysicsVJ", scenes, pars=[("Scene", 0), ("Nextscene", 1), ("Crossfade", 0.0),
                                            ("Freerunall", 0), ("Cut", 0)])
    return show


def _load_apc(show=None):
    src = _src("touchdesigner", "callbacks", "apc_mini.py")
    src = src.replace('_REPO = r""', '_REPO = r"%s"' % ROOT, 1)
    ns = {"__name__": "cb_apc_mini", "op": (lambda path: show)}
    exec(compile(src, "apc_mini.py", "exec"), ns)
    return ns


def _surface(show):
    apc = _Op("/APCShow", {"ledout": _LedOut()}, pars=[("Target", "/PhysicsVJ"), ("Device", 1)])
    return apc


def test_cut_and_freerun_buttons_are_transport_not_arming():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    show.par.Nextscene.val = 4
    show.par.Crossfade.val = 0.7
    ns["on_midi"](apc, "Note On", 1, ns["BTN_CUT"], 127)
    assert show.par.Cut.pulses == 1
    assert show.par.Scene.val == 4 and show.par.Crossfade.val == 0.0
    assert show.par.Nextscene.val == 4          # NOT re-armed to scene 6
    ns["on_midi"](apc, "Note On", 1, ns["BTN_FREERUN"], 127)
    assert show.par.Freerunall.val == 1
    assert show.par.Nextscene.val == 4          # NOT re-armed to scene 7
    ns["on_midi"](apc, "Note On", 1, ns["BTN_FREERUN"], 127)
    assert show.par.Freerunall.val == 0


def test_track_buttons_one_to_six_arm_deck_b():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    for i in range(ns["N_ARM"]):
        ns["on_midi"](apc, "Note On", 1, ns["TRACK_BTN"][i], 127)
        assert show.par.Nextscene.val == i


def test_grid_pad_cuts_scene_and_sets_palette():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    col, row = 3, 5
    ns["on_midi"](apc, "Note On", 1, row * 8 + col, 100)
    assert show.par.Scene.val == col
    holder = show.op(ns["SCENE_NAMES"][col]).op("sim")
    assert holder.par.Palette.val == row
    # note-off / zero-velocity presses do nothing
    ns["on_midi"](apc, "Note On", 1, 0, 0)
    assert show.par.Scene.val == col


def test_scene_buttons_launch_scenes_past_the_grid():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    for k, idx in enumerate(range(ns["N_GRID_COLS"], ns["N_SCENES"])):
        ns["on_midi"](apc, "Note On", 1, ns["SCENE_BTN"][2 + k], 127)
        assert show.par.Scene.val == idx


def test_refire_pulses_the_live_scenes_signature_event():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    for idx, pulse in enumerate(ns["REFIRE_PULSE"]):
        show.par.Scene.val = idx
        ns["on_midi"](apc, "Note On", 1, ns["BTN_REFIRE"], 127)
    lhc = show.op("lhc").op("sim")
    assert lhc.par.Newevent.pulses == 1
    assert show.op("opendata").op("sim").par.Nextevent.pulses == 1


def test_master_fader_is_crossfade_and_faders_drive_live_scene():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    ns["on_midi"](apc, "Control Change", 1, ns["FADER_CC"][8], 64)
    assert abs(show.par.Crossfade.val - 64 / 127) < 1e-9
    show.par.Scene.val = 1
    ns["on_midi"](apc, "Control Change", 1, ns["FADER_CC"][0], 127)
    assert abs(show.op("nbody").par.Trail.val - 0.99) < 1e-9
    ns["on_midi"](apc, "Control Change", 1, ns["FADER_CC"][2], 127)
    assert abs(show.op("nbody").op("sim").par.Pointsize.val - 0.2) < 1e-9


def test_leds_are_sent_by_difference_and_reset_repaints_everything():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    led = apc.op("ledout")
    ns["reset"](apc)
    full = len(led.sent)
    # 64 pads + 16 round buttons blanked, then a full repaint on top
    assert full > 64 + 16
    led.sent.clear()
    ns["repaint"](apc)
    assert led.sent == []                      # nothing changed -> nothing sent
    show.par.Crossfade.val = 0.5
    ns["repaint"](apc)
    assert 0 < len(led.sent) <= 3              # only the Cut indicator moved
    led.sent.clear()
    ns["reset"](apc)
    # A hard resync ignores the cache: the blank (64 pads + 16 buttons, all
    # velocity 0) goes out again in full, then the repaint on top of it.
    assert len(led.sent) >= full
    assert all(v == 0 for _, _, v in led.sent[:80])
    assert any(v != 0 for _, _, v in led.sent[80:])


def test_apc_module_parses_with_required_hooks():
    tree = ast.parse(_src("touchdesigner", "callbacks", "apc_mini.py"))
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {"on_midi", "repaint", "reset", "onSetupParameters", "onCook"} <= funcs


# --- lessons from the first real build report ----------------------------
def test_every_sop_callback_creates_cd_without_relying_on_a_default():
    """TD 2025+ rejects a default for the standard Cd attribute ("Cannot
    specify default for standard attributes"); older builds want one. Every
    Script SOP callback that colours points must try both spellings."""
    for f in os.listdir(CALLBACKS):
        if not f.endswith("_sop.py"):
            continue
        src = _src("touchdesigner", "callbacks", f)
        if 'pointAttribs.create("Cd"' not in src:
            continue
        assert 'pointAttribs.create("Cd", (0.0, 0.0, 0.0))' in src, f
        assert 'pointAttribs.create("Cd")' in src, f


def test_builder_never_sets_the_midi_device_table_to_an_id():
    """On MIDI In DATs and MIDI Out CHOPs 'device' is the Device *Table* DAT
    path; the Device Mapper id is 'id'. Setting 'device' to a number left an
    "Invalid path for node" warning on the APC's LED output."""
    src = _src("touchdesigner", "td_build.py")
    assert not re.search(r'_setpar\(\w+,\s*"device"', src)
    assert not re.search(r"""\(['"]id['"],\s*['"]device['"]\)""", src)   # the old loops
    assert '_setpar(ledout, "id", device)' in src
    assert '_setpar(midiin, "id", device)' in src


def test_control_chops_leave_time_slice_mode_before_sizing():
    """A Script CHOP fed by audio inherits Time Slice mode, where numSamples
    cannot be edited (a tdWarning every frame). Both control CHOPs must
    switch it off before emitting their one-sample channels."""
    for f in ("audio_chop.py", "tempo_chop.py"):
        src = _src("touchdesigner", "callbacks", f)
        assert "isTimeSlice = False" in src, f
        assert src.index("isTimeSlice = False") < src.index("numSamples = 1"), f


def test_opendata_publishes_mass_on_the_scene_not_on_itself():
    """Storing on your own op while cooking is a cook-dependency loop; the
    live invariant mass goes on the scene COMP and the HUD reads it there."""
    sop = _src("touchdesigner", "callbacks", "opendata_sop.py")
    hud = _src("touchdesigner", "callbacks", "mass_hud_top.py")
    assert 'scriptOp.parent().store("invariant_mass"' in sop
    assert 'scriptOp.store("invariant_mass"' not in sop
    assert 'scriptOp.parent().fetch("invariant_mass"' in hud


# --- lessons from the second build report ---------------------------------
class _ModeEnum:
    """Stand-in for TD's ParMode enum: the class of a parameter's .mode value."""
    class _M:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return isinstance(other, _ModeEnum._M) and other.name == self.name

        def __repr__(self):
            return "ParMode." + self.name


_ModeEnum._M.CONSTANT = _ModeEnum._M("CONSTANT")
_ModeEnum._M.EXPRESSION = _ModeEnum._M("EXPRESSION")


class _ExprPar:
    def __init__(self):
        self.expr = ""
        self.mode = _ModeEnum._M.CONSTANT
        self.val = 0


def test_expressions_bind_without_a_parmode_import():
    """On a 2025 build `from td import ParMode` came back None and every
    expression in the show (decks, Active flags, crossfade, uniforms) went
    unset. _expr must switch the mode using the enum class of the parameter's
    own .mode value, with no import at all."""
    td_build = importlib.import_module("touchdesigner.td_build")
    assert td_build.ParMode is None            # as it is outside TD
    o = _Op("/x")
    o.par.tx = _ExprPar()
    assert td_build._expr(o, "tx", "absTime.frame") is True
    assert o.par.tx.expr == "absTime.frame"
    assert o.par.tx.mode == _ModeEnum._M.EXPRESSION
    assert td_build._bindexpr(o, "tx", "1+1") is True
    assert td_build._expr(o, "nope", "1") is False


def test_setpar_any_tries_each_spelling_once():
    td_build = importlib.import_module("touchdesigner.td_build")
    o = _Op("/light", pars=[("cr", 0.0)])
    assert td_build._setpar_any(o, ("colorr", "cr"), 0.5) == "cr"
    assert o.par.cr.val == 0.5
    assert td_build._setpar_any(o, ("nothing", "here"), 1.0, quiet=True) is None


def test_builder_uses_the_parameter_spellings_this_build_reported():
    src = _src("touchdesigner", "td_build.py")
    assert '("cr", "colorr")' in src and '("cg", "colorg")' in src and '("cb", "colorb")' in src
    assert '("vdat", "vertexdat")' in src and '("pdat", "pixeldat")' in src
    # CHOP to SOP maps by name, and the names are probed from a SOP to CHOP on
    # the field itself rather than assumed (they hold no parentheses on 2025).
    assert '"soptoCHOP"' in src and "_cd_channel_names(probe)" in src
    assert '"renameCHOP"' in src and '"c*"' in src   # Script CHOP numbers from 1
    assert "Cd(0)" not in src.replace("Cd(0)..Cd(3)", "")
    assert "_ensure_active(c)" in src                 # POP scene has an Active flag


def test_startup_reads_td_globals_through_the_td_module():
    src = _src("touchdesigner", "startup.py")
    assert "_td_global(\"app\")" in src and "_td_global(\"project\")" in src
    assert "app.version, app.build" in src


def test_instancing_reads_channel_names_off_the_sim():
    """copyNumpyArray numbers channels from 1 on 2025 (c1..c7), from 0 before;
    a hard-coded c0 mapping shifted position to (0, x, y) and colour to
    (z, r, g). Every instanced scene must go through _instance_channels."""
    src = _src("touchdesigner", "td_build.py")
    assert '"instancetx", "c0"' not in src
    assert src.count("_instance_channels(") >= 4          # def + 3 scenes
    td_build = importlib.import_module("touchdesigner.td_build")

    class _Ch:
        def __init__(self, n):
            self.name = n

    class _Chop(_Op):
        def cook(self, force=False):
            self.cooked = True

        def chans(self):
            return [_Ch("c%d" % i) for i in range(1, 8)]     # 1-based, as on 2025
    chop = _Chop("/scene/sim")
    assert td_build._chan_names(chop, 7) == ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    geo = _Op("/scene/geo", pars=[(n, "") for n in (
        "instancing", "instanceop", "instancetx", "instancety", "instancetz",
        "instancesx", "instancesy", "instancesz", "instancer", "instanceg", "instanceb",
        "instancecolormode")])
    td_build._instance_channels(geo, chop)
    assert (geo.par.instancetx.val, geo.par.instancety.val, geo.par.instancetz.val) == ("c1", "c2", "c3")
    assert (geo.par.instancer.val, geo.par.instanceg.val, geo.par.instanceb.val) == ("c4", "c5", "c6")
    assert geo.par.instancesx.val == "c7"


def test_every_explicit_resolution_sets_custom_mode():
    """resolutionw/h are ignored unless Output Resolution is 'custom'; the
    Render TOP sat at 256x256 and the show came out square and blocky."""
    src = _src("touchdesigner", "td_build.py")
    # resolutionw/h are set in exactly one place, _set_res, right after the mode.
    assert src.count('"resolutionw"') == 1 and src.count('"resolutionh"') == 1
    assert src.index('"outputresolution", "custom"') < src.index('"resolutionw"')
    assert src.count("_set_res(") >= 14                 # render, glow, trails, HUD, GLSL, post
    # every Feedback TOP has a source on its input (an unwired one is 256x256)
    trails = src[src.index("def _trails("):src.index("def _mass_hud(")]
    assert "_connect(src, fb, 0)" in trails and "_set_res(fb)" in trails
    # every composite is pinned rather than inheriting from its smallest input
    assert src.count('"compositeTOP"') == src.count("_set_res(comp)") + src.count("_set_res(label)") + src.count("_set_res(over)")


def test_master_chain_has_an_fx_bypass_and_is_cooked_at_build():
    """A post shader that fails to compile must not black out the show, and a
    headless build must surface such a failure in the report (nothing else
    pulls the master chain there)."""
    src = _src("touchdesigner", "td_build.py")
    assert 'appendToggle("Fx"' in src
    assert '"0 if parent().par.Fx.eval() else 1"' in src
    build_all = src[src.index("def build_all("):src.index("# =====")]
    assert "for o in (cross, mixed, post, final):" in build_all
    # Par objects are compared by value, never as objects
    assert "par.Crossfade < 1" not in src and "par.Crossfade > 0" not in src


def test_overlay_is_switch_gated_and_pop_binds_the_reported_names():
    """A Level of an errored GLSL TOP propagates the error; the overlay is
    gated by a Switch TOP so it is not cooked unless Wavevis is on. The POP
    strength binds 'radialstrength', the name the 2025 build reported."""
    src = _src("touchdesigner", "td_build.py")
    overlay = src[src.index("def _waveform_overlay("):src.index("def _post_fx(")]
    assert '"switchTOP"' in overlay and "Wavevis.eval()" in overlay
    assert '"levelTOP"' not in overlay
    assert '"radialstrength"' in src and '("timeintegration"' in src
    assert "_report_master_chain(" in src
    startup = _src("touchdesigner", "startup.py")
    assert "_glsl_compile_log(" in startup and '"infoDAT"' in startup
