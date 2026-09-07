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
