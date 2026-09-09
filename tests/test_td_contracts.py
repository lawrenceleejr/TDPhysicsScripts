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
import json
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
        self.channels = {}          # CHOP channel values for op['name']
        for name, val in pars:
            setattr(self.par, name, _Par(self, name, val))

    def __getitem__(self, name):
        return self.channels.get(name, 0.0)

    def op(self, name):
        o = self
        for part in name.lstrip("./").split("/"):
            o = o.children.get(part) if o is not None else None
        return o

    def parent(self):
        return None

    def store(self, key, val):
        self.__dict__.setdefault("_storage", {})[key] = val

    def fetch(self, key, default=None):
        return self.__dict__.setdefault("_storage", {}).get(key, default)


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
    fx = ["Invert", "Edges", "Posterize", "Pixelate", "Mirror", "Mono", "Solarize", "Fisheye",
          "Tiles", "Shake", "Ascii", "Blur", "Kaleidofx", "Rgbboost", "Strobe", "Negflash"]
    analyze = _Op("/PhysicsVJ/Reactor/analyze", pars=[("Beatsens", 1.6), ("Mute", 0),
                                                       ("Resetlevels", 0), ("Hit", 0)])
    tempo = _Op("/PhysicsVJ/Tempo/tempo", pars=[("Bpm", 120.0), ("Resetphase", 0)])
    scenes["Reactor"] = _Op("/PhysicsVJ/Reactor", {"analyze": analyze})
    scenes["Tempo"] = _Op("/PhysicsVJ/Tempo", {"tempo": tempo})
    show = _Op("/PhysicsVJ", scenes, pars=[("Scene", 0), ("Nextscene", 1), ("Crossfade", 0.0),
                                            ("Freerunall", 0), ("Cut", 0), ("Freeze", 0),
                                            ("Blackout", 0), ("Title", 0), ("Titlehold", 4.0),
                                            ("Reactive", 1), ("Reactamount", 0.6),
                                            ("Chaosburst", 0), ("Chaos", 0.0)]
                                            + [(f, 0) for f in fx])
    return show


class _Clock:
    seconds = 100.0


def _load_apc(show=None):
    src = _src("touchdesigner", "callbacks", "apc_mini.py")
    src = src.replace('_REPO = r""', '_REPO = r"%s"' % ROOT, 1)
    ns = {"__name__": "cb_apc_mini", "op": (lambda path: show), "absTime": _Clock}
    exec(compile(src, "apc_mini.py", "exec"), ns)
    return ns


def test_apc_fx_names_match_the_builder_and_the_shader():
    """The upper-right quadrant toggles pars the builder appends and the post
    shader reads in the same four-per-vec4 order."""
    ns = _load_apc()
    td_build = importlib.import_module("touchdesigner.td_build")
    assert ns["FX_NAMES"] == td_build.FX_NAMES and len(ns["FX_NAMES"]) == 16
    frag = _src("touchdesigner", "shaders", "post_fx.frag")
    for u in ("uAudio", "uLook", "uFxA", "uFxB", "uFxC", "uFxD"):
        assert "uniform vec4 %s;" % u in frag, u
    src = _src("touchdesigner", "td_build.py")
    assert '"uFxA", "uFxB", "uFxC", "uFxD"' in src
    # every FX pad is a toggle on the show (lower-case names would be new pars)
    assert all(n[0].isupper() and n[1:].islower() for n in ns["FX_NAMES"])
    # every scene has a title, and the title shader ships
    assert set(td_build.SCENE_TITLES) == set(_scene_names())
    assert all(t == t.upper() and 1 <= len(t.split()) <= 2 for t in td_build.SCENE_TITLES.values())


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


def test_lower_left_quadrant_arms_deck_b_and_shift_cuts():
    """Scene pads are the 4x4 lower-left block in reading order. A press
    queues the scene on deck B (for the crossfader); SHIFT + pad cuts."""
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    for idx in range(ns["N_SCENES"]):
        col, row = idx % 4, 3 - idx // 4
        ns["on_midi"](apc, "Note On", 1, row * 8 + col, 100)
        assert show.par.Nextscene.val == idx
        assert show.par.Scene.val == 0                        # never cuts unshifted
    ns["on_midi"](apc, "Note On", 1, 0 * 8 + 3, 100)          # slot 15: no scene, inert
    assert show.par.Nextscene.val == ns["N_SCENES"] - 1
    ns["on_midi"](apc, "Note On", 1, ns["SHIFT"], 127)
    ns["on_midi"](apc, "Note On", 1, 3 * 8 + 2, 100)          # shift: cut to scene 2
    assert show.par.Scene.val == 2
    ns["on_midi"](apc, "Note Off", 1, ns["SHIFT"], 0)
    ns["on_midi"](apc, "Note On", 1, 3 * 8 + 0, 0)            # zero velocity: nothing
    assert show.par.Scene.val == 2 and show.par.Nextscene.val == ns["N_SCENES"] - 1


def test_right_column_buttons_select_scenes_with_shift_for_the_rest():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    for k in range(8):
        ns["on_midi"](apc, "Note On", 1, ns["SCENE_BTN"][k], 127)
        assert show.par.Scene.val == k
    ns["on_midi"](apc, "Note On", 1, ns["SHIFT"], 127)
    for k in range(3):
        ns["on_midi"](apc, "Note On", 1, ns["SCENE_BTN"][k], 127)
        assert show.par.Scene.val == 8 + k
    ns["on_midi"](apc, "Note On", 1, ns["SCENE_BTN"][5], 127)  # shift+6 = scene 13: none
    assert show.par.Scene.val == 10
    # while shift is held the column shows the shifted layer: scene 10 lit on button 3
    led = apc.op("ledout")
    led.sent.clear()
    ns["repaint"](apc)
    ns["on_midi"](apc, "Note Off", 1, ns["SHIFT"], 0)
    lit = {n: v for ch, n, v in led.sent if n in ns["SCENE_BTN"]}
    assert lit.get(ns["SCENE_BTN"][2]) == 0        # unshifted layer: button 3 = scene 2, not live
    # shifted layer: Reset LEDs lives on shift + track 1
    ns["on_midi"](apc, "Note On", 1, ns["SHIFT"], 127)
    led.sent.clear()
    ns["on_midi"](apc, "Note On", 1, ns["BTN_RESET"], 127)
    assert len(led.sent) >= 80 and all(v == 0 for _, _, v in led.sent[:80])


def test_leds_react_to_the_audio_and_animate_on_presses():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    analyze = show.op("Reactor/analyze")
    pulse_pad = ns["_pad_of"]("action", "pulse")
    live_pad = ns["_pad_of"]("scene", 0)
    led = apc.op("ledout")
    _Clock.seconds = 500.0
    ns["reset"](apc)
    # quiet: PULSE dim white, live pad at the lowest breathing step
    state = {n: (v, ch) for ch, n, v in led.sent}
    assert state[pulse_pad] == (ns["COL_ACTION"], ns["CH_DIM"])
    assert state[live_pad][1] == ns["CH_LEVELS"][0]
    # a kick: PULSE flashes yellow, the live pad jumps to full
    analyze.channels.update({"beat": 0.9, "level": 0.6})
    led.sent.clear()
    ns["tick"](apc)
    state = {n: (v, ch) for ch, n, v in led.sent}
    assert state[pulse_pad] == (ns["COL_YELLOW"], ns["CH_BRIGHT"])
    assert state[live_pad][1] == ns["CH_BRIGHT"]
    assert len(led.sent) <= 6                     # only the reactive pads moved
    # muted: nothing reacts
    analyze.par.Mute.val = 1
    led.sent.clear()
    ns["tick"](apc)
    state = {n: (v, ch) for ch, n, v in led.sent}
    assert state[pulse_pad] == (ns["COL_ACTION"], ns["CH_DIM"])
    analyze.par.Mute.val = 0
    analyze.channels.update({"beat": 0.0, "level": 0.0})
    ns["tick"](apc)
    # PUNCH: a white ripple crosses the grid, then everything returns to rest
    rest = dict(ns["_led_cache"](apc))
    ns["on_midi"](apc, "Note On", 1, 7 * 8 + 0, 100)
    _Clock.seconds = 500.15
    led.sent.clear()
    ns["tick"](apc)
    white = [n for ch, n, v in led.sent if v == ns["COL_WHITE"] and ch == ns["CH_BRIGHT"] and n < 64]
    assert len(white) >= 3
    _Clock.seconds = 501.5
    ns["tick"](apc)
    assert ns["_st"](apc)["anims"] == []
    after = dict(ns["_led_cache"](apc))
    assert after == rest
    # a scene cut from the right column ripples in the new scene's palette colour
    ns["on_midi"](apc, "Note On", 1, ns["SCENE_BTN"][1], 127)
    anims = ns["_st"](apc)["anims"]
    assert len(anims) == 1 and anims[0]["kind"] == "ripple"
    assert anims[0]["color"] == ns["_scene_color"](show, 1)


def test_apc_map_renders_every_control_from_the_tables():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import apc_map
    ns = apc_map.load_controller()
    svg = apc_map.render_svg(ns)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    for name in ns["SCENE_NAMES"]:
        assert ns["SCENE_LABELS"][name] in svg, name
    for lab in ns["LABELS"].values():
        assert apc_map._esc(lab) in svg, lab
    for pal in ns["_PALETTES"]:
        assert pal in svg
    assert svg.count("<rect") >= 64 + 9


def test_lower_right_quadrant_sets_palettes_and_tempo_level_tools():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    show.par.Scene.val = 1
    holder = show.op("nbody").op("sim")
    ns["on_midi"](apc, "Note On", 1, 3 * 8 + 6, 100)          # row 3 col 6 = palette 2
    assert holder.par.Palette.val == 2
    ns["on_midi"](apc, "Note On", 1, 2 * 8 + 5, 100)          # row 2 col 5 = palette 5
    assert holder.par.Palette.val == 5
    analyze = show.op("Reactor/analyze")
    tempo = show.op("Tempo/tempo")
    ns["on_midi"](apc, "Note On", 1, 1 * 8 + 6, 100)          # BPM x2
    assert tempo.par.Bpm.val == 240.0
    ns["on_midi"](apc, "Note On", 1, 1 * 8 + 7, 100)          # BPM /2
    assert tempo.par.Bpm.val == 120.0
    ns["on_midi"](apc, "Note On", 1, 1 * 8 + 5, 100)          # SYNC
    assert tempo.par.Resetphase.pulses == 1 and analyze.par.Hit.pulses == 1
    ns["on_midi"](apc, "Note On", 1, 0 * 8 + 4, 100)          # AUTO RESET
    assert analyze.par.Resetlevels.pulses == 1
    ns["on_midi"](apc, "Note On", 1, 0 * 8 + 6, 100)          # SENS+
    assert abs(analyze.par.Beatsens.val - 1.75) < 1e-9
    ns["on_midi"](apc, "Note On", 1, 0 * 8 + 5, 100)          # SENS-
    assert abs(analyze.par.Beatsens.val - 1.6) < 1e-9
    ns["on_midi"](apc, "Note On", 1, 0 * 8 + 7, 100)          # MUTE = the show's Reactive switch
    assert show.par.Reactive.val == 0 and analyze.par.Mute.val == 0
    ns["on_midi"](apc, "Note On", 1, 0 * 8 + 7, 100)
    assert show.par.Reactive.val == 1
    # tap tempo: three taps 0.5 s apart -> 120 bpm
    for k in range(3):
        _Clock.seconds = 200.0 + 0.5 * k
        ns["on_midi"](apc, "Note On", 1, 1 * 8 + 4, 100)
    assert abs(tempo.par.Bpm.val - 120.0) < 1e-6
    assert tempo.par.Resetphase.pulses == 4


def test_upper_right_quadrant_toggles_whole_screen_fx_and_shift_clears():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    # top row: Invert Edges Posterize Pixelate on cols 4..7 of row 7
    ns["on_midi"](apc, "Note On", 1, 7 * 8 + 4, 100)
    assert show.par.Invert.val == 1
    ns["on_midi"](apc, "Note On", 1, 5 * 8 + 6, 100)          # row 5 col 6 = Ascii
    assert show.par.Ascii.val == 1
    ns["on_midi"](apc, "Note On", 1, 7 * 8 + 5, 100)          # Edges
    assert show.par.Edges.val == 1
    ns["on_midi"](apc, "Note On", 1, 7 * 8 + 4, 100)          # Invert off again
    assert show.par.Invert.val == 0
    assert show.par.Scene.val == 0                            # FX never change the scene
    ns["on_midi"](apc, "Note On", 1, ns["SHIFT"], 127)
    ns["on_midi"](apc, "Note On", 1, 4 * 8 + 7, 100)          # shift + any FX = clear all
    assert all(getattr(show.par, f).val == 0 for f in ns["FX_NAMES"])
    ns["on_midi"](apc, "Note Off", 1, ns["SHIFT"], 0)
    # the round CLEAR FX button does the same without shift
    show.par.Mono.val = 1
    ns["on_midi"](apc, "Note On", 1, ns["TRACK_BTN"][4], 127)
    assert show.par.Mono.val == 0


def test_upper_left_quadrant_performs_on_the_live_scene():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    show.par.Scene.val = 3                                    # softbody
    sim = show.op("softbody").op("sim")
    sim.par.Punch = _Par(sim, "Punch", 0)
    sim.par.Punchstrength = _Par(sim, "Punchstrength", 1.0)
    ns["on_midi"](apc, "Note On", 1, 7 * 8 + 0, 100)          # PUNCH
    assert sim.par.Punch.pulses == 1
    ns["on_midi"](apc, "Note On", 1, 7 * 8 + 1, 100)          # BIG PUNCH
    assert sim.par.Punch.pulses == 2 and sim.par.Punchstrength.val == 3.5
    assert show.par.Chaosburst.pulses == 1     # the frame comes apart with it
    ns["on_midi"](apc, "Note On", 1, 7 * 8 + 3, 100)          # RESET
    assert sim.par.Reset.pulses == 1
    ns["on_midi"](apc, "Note On", 1, 6 * 8 + 0, 100)          # PULSE = manual beat
    assert show.op("Reactor/analyze").par.Hit.pulses == 1
    ns["on_midi"](apc, "Note On", 1, 6 * 8 + 1, 100)          # FREEZE
    assert show.par.Freeze.val == 1
    ns["on_midi"](apc, "Note On", 1, 6 * 8 + 1, 100)
    assert show.par.Freeze.val == 0
    ns["on_midi"](apc, "Note On", 1, 4 * 8 + 2, 100)          # BLACKOUT
    assert show.par.Blackout.val == 1
    ns["on_midi"](apc, "Note On", 1, 4 * 8 + 1, 100)          # SCENE >
    assert show.par.Scene.val == 4
    ns["on_midi"](apc, "Note On", 1, 4 * 8 + 0, 100)          # SCENE <
    assert show.par.Scene.val == 3
    ns["on_midi"](apc, "Note On", 1, 5 * 8 + 3, 100)          # PALETTE >
    assert sim.par.Palette.val == 1
    # ORBIT FLIP negates the camera spin
    show.op("softbody").par.Orbit.val = 8.0
    ns["on_midi"](apc, "Note On", 1, 5 * 8 + 1, 100)
    assert show.op("softbody").par.Orbit.val == -8.0
    # TRAIL MAX and STROBE act while held and undo on release
    show.op("softbody").par.Trail.val = 0.3
    ns["on_midi"](apc, "Note On", 1, 5 * 8 + 0, 100)
    assert show.op("softbody").par.Trail.val > 0.9
    ns["on_midi"](apc, "Note Off", 1, 5 * 8 + 0, 0)
    assert show.op("softbody").par.Trail.val == 0.3
    ns["on_midi"](apc, "Note On", 1, 4 * 8 + 3, 100)
    assert show.par.Strobe.val == 1
    ns["on_midi"](apc, "Note Off", 1, 4 * 8 + 3, 0)
    assert show.par.Strobe.val == 0
    # VARIANT steps the scene's own menu (N-Body initial condition)
    show.par.Scene.val = 1
    nb = show.op("nbody").op("sim")
    nb.par.Initial = _Par(nb, "Initial", 0, menu=["a", "b", "c"])
    ns["on_midi"](apc, "Note On", 1, 5 * 8 + 2, 100)
    assert nb.par.Initial.val == 1
    for _ in range(2):
        ns["on_midi"](apc, "Note On", 1, 5 * 8 + 2, 100)
    assert nb.par.Initial.val == 0                            # wraps


def test_title_button_holds_while_pressed_and_lingers_on_a_tap():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    _Clock.seconds = 300.0
    ns["on_midi"](apc, "Note On", 1, 6 * 8 + 3, 100)          # TITLE down
    assert show.fetch("title_t0") == 300.0 and show.fetch("title_toff") > 1e8
    _Clock.seconds = 300.1
    ns["on_midi"](apc, "Note Off", 1, 6 * 8 + 3, 0)           # quick tap
    assert abs(show.fetch("title_toff") - (300.1 + ns["TITLE_TAP_SECONDS"])) < 1e-9
    _Clock.seconds = 310.0
    ns["on_midi"](apc, "Note On", 1, ns["TRACK_BTN"][0], 127)  # round TITLE button
    _Clock.seconds = 313.0
    ns["on_midi"](apc, "Note Off", 1, ns["TRACK_BTN"][0], 0)   # long hold -> off now
    assert show.fetch("title_toff") == 313.0
    # the show's own pulse helper
    ns["title_pulse"](show, 2.0)
    assert show.fetch("title_toff") == 315.0


def test_faders_follow_the_live_scenes_map():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    fm = ns["FADER_MAP"]
    assert set(fm) == set(ns["SCENE_NAMES"]) and all(len(v) == 8 for v in fm.values())
    # scenes with a Trail get Trail / Orbit on faders 1-2; the ranges are sane
    for name, specs in fm.items():
        for spec in specs:
            if spec is not None:
                where, par, lo, hi = spec
                assert lo < hi, (name, par)
    show.par.Scene.val = 0                                    # ising: fader 4 = Temperature
    show.op("ising").op("sim").par.Temperature = _Par(None, "Temperature", 2.27)
    ns["on_midi"](apc, "Control Change", 1, ns["FADER_CC"][3], 0)
    assert show.op("ising").op("sim").par.Temperature.val == 1.5
    ns["on_midi"](apc, "Control Change", 1, ns["FADER_CC"][0], 127)   # unmapped: no-op
    assert show.op("ising").par.Trail.val == 0.0


def test_refire_pulses_the_live_scenes_signature_event():
    show = _fake_show(11, 8)
    ns = _load_apc(show)
    apc = _surface(show)
    ns["on_midi"](apc, "Note On", 1, ns["SHIFT"], 127)        # re-fire is shift + track 2
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
    assert {"on_midi", "repaint", "reset", "tick", "perform", "onSetupParameters", "onCook"} <= funcs
    src = _src("touchdesigner", "td_build.py")
    assert "apc_mini.tick(me.parent())" in src           # the per-frame LED driver is wired


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
    assert src.count("_instance_channels(") >= 3          # def, _instanced_geo, storm
    assert 'look="soft"' in src and 'look="lit"' in src   # hydrogen soft, bodies lit
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
    # resolutionw/h are written in exactly one place, _set_res, and only after
    # the mode that makes them count -- including the scaled (expression) form.
    assert src.count('"resolutionw"') == 1 and src.count('"resolutionh"') == 1
    assert src.index('"outputresolution", "custom"') < src.index('"resolutionw"')
    setres = src[src.index("def _set_res("):src.index("def _chan_names(")]
    assert '"resolutionw"' in setres and "scale_expr" in setres
    assert src.count("_set_res(") >= 14                 # render, glow, trails, HUD, GLSL, post
    # every Feedback TOP has a source on its input (an unwired one is 256x256)
    trails = src[src.index("def _trails("):src.index("def _mass_hud(")]
    assert "_connect(src, fb, 0)" in trails and "_set_res(fb)" in trails
    # every composite is pinned rather than inheriting from its smallest input
    made = re.findall(r'(\w+)\s*=\s*_create\([^,]+,\s*"compositeTOP"', src)
    assert made, "no composite TOPs found"
    for var in set(made):
        # pinned -- to the master size, or to an explicit one (the APC overlay
        # has to match the map picture, not the show)
        assert "_set_res(%s)" % var in src or "_set_res(%s," % var in src, var


def test_master_chain_has_an_fx_bypass_and_is_cooked_at_build():
    """A post shader that fails to compile must not black out the show, and a
    headless build must surface such a failure in the report (nothing else
    pulls the master chain there)."""
    src = _src("touchdesigner", "td_build.py")
    assert 'appendToggle("Fx"' in src
    assert '"0 if parent().par.Fx.eval() else 1"' in src
    build_all = src[src.index("def build_all("):src.index("# =====")]
    assert "for o in (cross, mixed, post, stock, final):" in build_all
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


def test_scenes_end_in_an_out_top_and_wires_are_verified():
    """TD does not wire across COMP boundaries (and connect() stays silent), so
    a scene must end in an Out TOP -- the COMP's output connector is what the
    deck switches take -- the overlay must fetch Reactor textures with Select
    TOPs, and _connect must check that each wire took."""
    src = _src("touchdesigner", "td_build.py")
    glow = src[src.index("def _glow("):src.index("def _trails(")]
    assert '"outTOP" if name == "out" else "nullTOP"' in glow
    hud = src[src.index("def _mass_hud("):src.index("def build_ising(")]
    assert '_create(container, "outTOP", "out"' in hud
    build_all = src[src.index("def build_all("):src.index("# =====")]
    assert "feed = _scene_feed(base, out)" in build_all
    assert "_connect(out, switch_a" not in build_all
    overlay = src[src.index("def _waveform_overlay("):src.index("def _post_fx(")]
    assert overlay.count('"selectTOP"') == 2
    assert 'reactor.op("wave_tex"), ov' not in overlay
    connect = src[src.index("def _connect("):src.index("def _install_callbacks(")]
    assert "dst.inputs" in connect and "did not take" in connect


# --- the 3D scenes are lit dramatically and finished by the cinema pass -----
def test_3d_scenes_use_pbr_under_a_dramatic_rig_and_the_cinema_pass():
    """The lit 3D scenes go through _cinematic (ambient occlusion, depth of
    field, haze) on the PBR material under the low-key rig with an environment
    light; the line-art scenes do not, because a lens blur and occlusion erase
    a one-pixel track (LHC measured exactly black with the pass in). Each step
    is guarded so a missing OP type or a failed shader degrades to the raw
    render, never to black."""
    src = _src("touchdesigner", "td_build.py")
    for builder, nxt in (("build_nbody", "build_particles"), ("build_particles", "dest_reactor")):
        body = src[src.index("def %s(" % builder):src.index("def %s(" % nxt)]
        assert "_cinematic(" in body, builder
        assert "_trails(c, cine" in body, builder          # trails/bloom come after it
    for builder, nxt in (("build_lhc", "_cd_channel_names"), ("build_opendata", "build_apc")):
        body = src[src.index("def %s(" % builder):src.index("def %s(" % nxt)]
        assert "_cinematic(" not in body, builder
    # the lit instanced look is PBR (falls back to Phong), lit by the rig + env
    geo = src[src.index("def _instanced_geo("):src.index("def _line_geo(")]
    assert "_pbr_mat(" in geo and "_lit_mat(" not in geo
    pbr = src[src.index("def _pbr_mat("):src.index("def _studio_env(")]
    assert '"pbrMAT"' in pbr and "return _lit_mat(" in pbr
    for pn in ("basecolorr", "metallic", "roughness"):
        assert '"%s"' % pn in pbr, pn
    rig = src[src.index("def _light_rig("):src.index("USE_GLSL_MAT = False")]
    assert '"shadowtype",), "soft"' in rig and "_studio_env(" in rig
    assert "practical" in rig and "absTime.seconds" in rig     # the travelling accent
    env = src[src.index("def _studio_env("):src.index("def _floor(")]
    assert '"environmentlightCOMP"' in env and '"envlightmap"' in env and "studio_env.frag" in env
    # the cinema pass: Depth TOP -> GLSL, Switch-gated by a Cinema toggle
    cine = src[src.index("def _cinematic("):src.index("# ---------------------------------------------------------------------------\n# Reusable visual building blocks")]
    assert '"depthTOP"' in cine and "cinema.frag" in cine
    assert '"switchTOP"' in cine and "Cinema.eval()" in cine
    assert "return render" in cine                             # no Depth TOP -> raw render
    for u in ("uCam", "uCine", "uFog"):
        assert '"%s"' % u in cine, u
    # cameras carry tight planes so normalized depth is usable
    assert "CAM_NEAR, CAM_FAR = 0.5, 200.0" in src
    cam = src[src.index("def _camera("):src.index("def _light(")]
    assert '"near", CAM_NEAR' in cam and '"far", CAM_FAR' in cam


def test_cinema_and_env_shaders_declare_the_uniforms_the_builder_binds():
    cine = _src("touchdesigner", "shaders", "cinema.frag")
    for u in ("uCam", "uCine", "uFog"):
        assert "uniform vec4 %s;" % u in cine, u
    assert "sTD2DInputs[1]" in cine                            # reads the depth input
    assert "uLinear" in cine and "uNear" in cine and "uFar" in cine   # linearises if needed
    env = _src("touchdesigner", "shaders", "studio_env.frag")
    assert "uniform vec4 uEnv;" in env
    assert "softbox(" in env


def test_input_dats_get_their_scripts_through_callbacks_not_text():
    """Keyboard In and MIDI In DATs are input DATs: their text is the event
    log and is read-only ("The operator is not editable" aborted a whole
    build). Their scripts must go in a Text DAT named by 'callbacks', and
    the non-essential control extras must be guarded in build_all."""
    src = _src("touchdesigner", "td_build.py")
    assert not re.search(r"\bkb\.text\s*=", src)
    assert not re.search(r"\bmidiin\.text\s*=", src)
    assert '_setpar(kb, "callbacks", kb_cb)' in src
    assert '_setpar(midiin, "callbacks", in_cb)' in src
    build_all = src[src.index("def build_all("):src.index("# =====")]
    assert '_safe("punch / title controls", _punch_controls, base)' in build_all
    assert '_safe("reactive bindings", _reactive_bindings' in build_all
    assert '_safe("scene health", _scene_health, outs)' in build_all


def test_reactivity_is_switchable_subtle_and_smoothed():
    """The show has a Reactive switch and amount that drive the Reactor's
    Depth; the Reactor eases the beat flash in; the post punch is gentle;
    dark mode is the default tone."""
    src = _src("touchdesigner", "td_build.py")
    assert 'appendToggle("Reactive"' in src and 'appendFloat("Reactamount"' in src
    assert 'reactor.op("analyze"), "Depth"' in src
    assert 'appendToggle("Darkmode"' in src and '"uTone"' in src
    cb = _src("touchdesigner", "callbacks", "audio_chop.py")
    assert 'appendFloat("Depth"' in cb and "beat_s" in cb and "KickTracker(decay=0.3)" in cb
    post = _src("touchdesigner", "shaders", "post_fx.frag")
    assert "uniform vec4 uTone;" in post and "uBeat * 0.035" in post
    # the soft scenes get a lit fallback if they render black
    for scene in ("storm", "hydrogen"):
        assert '_ensure_visible(c, geo, out, "%s")' % scene in src
    hydrogen = src[src.index("def build_bohmian("):]
    assert "_light_rig(" in hydrogen                    # lights for the fallback


def test_palette_pads_glow_in_the_palettes_own_colour():
    """Each palette's LED is chosen from the APC's 128-colour table as the
    bright, saturated entry nearest the palette's most saturated stop, so the
    pad reads as the palette it selects (never a grey, never a dim twin)."""
    ns = _load_apc()
    assert len(ns["APC_RGB"]) == 128
    for name in ns["_PALETTES"]:
        rep = ns["palette_rgb"](name)
        v = ns["PAL_COLOR"][name]
        lr, lg, lb = ns["_rgb_of"](ns["APC_RGB"][v])
        assert max(lr, lg, lb) >= 0.85, (name, v)                 # bright
        assert max(lr, lg, lb) - min(lr, lg, lb) >= 0.35, (name, v)   # hued
        # the dominant channel agrees (an orange palette gets an orange LED)
        assert max(range(3), key=lambda i: rep[i]) == max(range(3), key=lambda i: (lr, lg, lb)[i]), name
    assert ns["nearest_led"]((0.0, 0.6, 0.9)) in (36, 37, 78)      # sky blue
    assert ns["nearest_led"]((0.4, 1.0, 0.0)) in (16, 17, 73, 74, 75, 85, 86, 98, 110)
    # the map draws its palette dots from the same representative colour
    map_src = _src("tools", "apc_map.py")
    assert 'ns["palette_rgb"](name)' in map_src and "PAL_HEX" not in map_src


def test_every_risky_pass_measures_itself_and_the_health_check_repairs():
    """A pass that can cost a scene its light has to prove it did not: the
    cinema pass compares the frame with and without itself and starts off if
    it dimmed it, and the health check reports the pixel range (black, white,
    NaN and negative all look alike in a mean) and repairs a black scene by
    switching the pass off and swapping the material family."""
    src = _src("touchdesigner", "td_build.py")
    cine = src[src.index("def _cinematic("):src.index("# ---------------------------------------------------------------------------\n# Reusable visual building blocks")]
    assert "_mean_of(render), _mean_of(cine)" in cine
    assert 'Cinema", False' in cine and "raw * 0.65" in cine
    health = src[src.index("def _scene_health("):src.index("def _swap_material(")]
    for probe in ("nanmin", "nanmax", "isnan"):
        assert probe in health, probe
    assert "NEGATIVE" in health and "NaN" in health
    assert 'Cinema", False' in health and "_swap_material(" in health
    swap = src[src.index("def _swap_material("):src.index("def _reactive_bindings(")]
    assert "_lit_mat(" in swap and "_pbr_mat(" in swap          # both directions
    # the bloom's black-point stage cannot push negatives into the composite
    glow = src[src.index("def _glow("):src.index("def _trails(")]
    assert '"blacklevel", threshold' in glow and "clamp" in glow


def test_dashboard_shows_the_program_feed_beside_the_control_map():
    """The top-level view is the same TOP the second display gets -- not a
    second render of it -- next to the APC map as an image, and it degrades to
    the plain program feed if the map is switched off."""
    src = _src("touchdesigner", "td_build.py")
    dash = src[src.index("def build_dashboard("):src.index("def build_all(")]
    assert '"selectTOP"' in dash and 'target_path + "/out"' in dash
    assert '"windowCOMP"' in dash and '"moviefileinTOP"' in dash
    assert '"switchTOP"' in dash and "Showmap.eval()" in dash
    assert '_create(c, "outTOP", "out"' in dash
    # the window and the dashboard show the SAME source
    assert dash.count('target_path + "/out"') >= 2
    # build_all wires it up, guarded like the other extras
    build_all = src[src.index("def build_all("):src.index("def _scene_feed(")]
    assert '_safe("dashboard", build_dashboard, dest, base)' in build_all
    # the cheatsheet image the dashboard loads is committed
    png = os.path.join(ROOT, "docs", "apc_map.png")
    assert os.path.isfile(png) and os.path.getsize(png) > 10000
    tool = _src("tools", "apc_map.py")
    assert "def write_png(" in tool and "apc_map.png" not in tool.split("def write_png")[0]


def test_lhc_readout_is_a_second_geometry_that_does_not_orbit():
    """The leaders and labels have to stay upright while the event turns, so
    they live in their own Geometry COMP that is never orbited, and the
    callback rotates the anchors instead. Both geometries render together."""
    src = _src("touchdesigner", "td_build.py")
    lhc = src[src.index("def build_lhc("):src.index("def _cd_channel_names(")]
    assert '"geometryCOMP", "readout"' in lhc
    assert "_render(c, [geo, readout], cam, None)" in lhc
    assert "_orbit(c, readout" not in lhc                  # never orbited
    assert '_install_callbacks(hud_sop, "lhc_hud_sop.py")' in lhc
    assert "readout/hud" in lhc                            # driven every frame
    cb = _src("touchdesigner", "callbacks", "lhc_hud_sop.py")
    assert "hud.rotate_y(" in cb and 'fetch("hud_tracks"' in cb
    sop = _src("touchdesigner", "callbacks", "lhc_sop.py")
    assert 'scene.store("hud_tracks"' in sop and "track_labels(" in sop
    # the readout stores on the scene COMP, never on the op that is cooking
    assert "scriptOp.store(" not in sop


def test_scene_looks_the_report_asked_for_are_wired():
    """The batch of look changes, each where the show reads it."""
    src = _src("touchdesigner", "td_build.py")
    # N-Body: a spotlight and nothing else, and no ambient to give it away
    nbody = src[src.index("def build_nbody("):src.index("def build_particles(")]
    assert "_spot_rig(" in nbody and "_light_rig(" not in nbody
    assert '"ambr"' in nbody
    spot = src[src.index("def _spot_rig("):src.index("USE_GLSL_MAT = False")]
    assert '"lighttype"' in spot and '"coneangle"' in spot and '"conedelta"' in spot
    # the raymarch takes the smooth pulse, never a phase ramp that wraps
    assert 'rex["beat"], rex["pulse"]' in src
    frag = _src("touchdesigner", "shaders", "raymarch.frag")
    assert "uBar" not in frag and "uPulse" in frag
    # RD inverts through a toggle the shader reads
    assert '("uInvert", "int(parent().par.Invert.eval())")' in src
    assert "uniform float uInvert;" in _src("touchdesigner", "shaders", "rd_color.frag")
    # Ising inverts in its callback
    assert '_p(scriptOp, "Invert", True)' in _src("touchdesigner", "callbacks", "ising_top.py")
    # the hydrogen cloud morphs between orbitals and is depth-cued
    hy = _src("touchdesigner", "callbacks", "hydrogen_chop.py")
    assert "HydrogenState.blend(" in hy and 'appendFloat("Morphtime"' in hy
    assert 'appendFloat("Depthcue"' in hy
    ns = _load_apc()
    assert ("sim", "Morphtime", 0.5, 25.0) in ns["FADER_MAP"]["hydrogen"]


def test_the_film_stock_is_one_pass_over_the_whole_show():
    """The grade, grain and dirt are a single pass after the performer's FX
    and before the title, gated so a failed shader leaves the clean mix. It is
    deliberately separate from _post_fx: that is what gets toggled for a bar,
    this is what the show is made of all night."""
    src = _src("touchdesigner", "td_build.py")
    g = src[src.index("def _grunge("):src.index("def _title_overlay(")]
    assert '"switchTOP"' in g and "Stock.eval()" in g
    assert "grunge.frag" in g
    for u in ("uGrain", "uGrade", "uAudio"):
        assert '"%s"' % u in g, u
    # the dials, appended from one table so label and range stay together
    assert "page.appendFloat(pn, label=label)" in g
    for pn in ("Grain", "Halation", "Dust", "Weave", "Contrast", "Splittone", "Chaos"):
        assert '("%s",' % pn in g, pn
    # a burst decays from a stored timestamp: no per-frame Python, cannot stick
    assert "chaos_t0" in g and "Chaosdecay" in g
    build_all = src[src.index("def build_all("):src.index("def _scene_feed(")]
    assert "_grunge(base, bypass, reactor, tempo" in build_all
    assert "_title_overlay(base, stocked" in build_all      # the title is printed on it
    frag = _src("touchdesigner", "shaders", "grunge.frag")
    for u in ("uGrain", "uGrade", "uAudio"):
        assert "uniform vec4 %s;" % u in frag, u
    # the look it is aiming for, as the shader's own knobs
    for tell in ("HALATION", "GRAIN", "DUST", "WEAVE", "CHAOS", "SHADOW_TONE", "HIGH_TONE"):
        assert tell in frag, tell


def test_the_show_runs_at_1080p_with_bundled_type():
    """Master resolution and the fonts the show is set in -- the two things
    that made it read as a game rather than a render."""
    td_build = importlib.import_module("touchdesigner.td_build")
    assert td_build.MASTER_RES == (1920, 1080)
    src = _src("touchdesigner", "td_build.py")
    # renders are master size, not a hardcoded 720p
    assert "w=MASTER_RES[0], h=MASTER_RES[1]" in src
    assert "w=1280, h=720" not in src
    # the fonts are bundled with their licences, and set from the file first
    for f in ("ArchivoBlack-Regular.ttf", "Archivo-Variable.ttf", "JetBrainsMono-Variable.ttf"):
        path = os.path.join(ROOT, "assets", "fonts", f)
        assert os.path.isfile(path), f
        with open(path, "rb") as fh:
            assert fh.read(4) == b"\x00\x01\x00\x00", f      # real TrueType
    assert os.path.isfile(os.path.join(ROOT, "assets", "fonts", "Archivo-OFL.txt"))
    font = src[src.index("def _font("):src.index("CAM_NEAR, CAM_FAR = 0.5")]
    assert '"fontfile"' in font and '"font"' in font and "FONT_DIR" in font
    # every Text TOP the show draws gets one of the roles
    assert src.count("_font(") >= 4
    assert '_font(text, "display")' in src and '_font(title, "mono")' in src


def test_the_raymarch_buys_its_smoothness_with_resolution():
    """A raymarch stutters because of what it costs, not how it moves: it
    renders below master size and is fitted back up before the bloom."""
    src = _src("touchdesigner", "td_build.py")
    sdf = src[src.index("def build_raymarch("):src.index("# ---------------------------------------------------------------------------\n# Master post-FX")]
    assert 'appendFloat("Quality"' in sdf
    assert '_set_res(sdf, scale_expr="parent().par.Quality.eval()")' in sdf
    assert '"fitTOP"' in sdf and "_glow(c, up," in sdf
    frag = _src("touchdesigner", "shaders", "raymarch.frag")
    assert "for (int i = 0; i < 88; i++)" in frag       # fewer marches
    assert "uMid * 0.35" not in frag                    # no audio in the camera


def test_the_program_window_is_full_screen_on_display_one():
    src = _src("touchdesigner", "td_build.py")
    dash = src[src.index("def build_dashboard("):src.index("def build_all(")]
    assert 'name="Dashboard", monitor=1' in dash
    assert '"fullscreen"' in dash and '"winsizemode", "sizemode", "size"' in dash
    assert '"borders"' in dash and '"cursorvisible"' in dash
    assert 'appendPulse("Closewindow"' in dash
    # the PhysicsVJ viewer is pointed at ./out, and no stale twin survives
    build_all = src[src.index("def build_all("):src.index("def _scene_feed(")]
    assert '"opviewer",), "./out"' in build_all
    assert 'for stale in ("out1", "out2")' in build_all


def test_the_keyboard_reaches_every_action_the_pads_do():
    """The show has to be playable with nothing plugged in, and a key must not
    be able to drift from its pad: both go through perform()."""
    ns = _load_apc()
    keymap, scenes = ns["KEYMAP"], ns["KEY_SCENES"]
    # numbers are the scene switcher, in the pads' reading order
    assert scenes == "1234567890" and len(scenes) >= ns["N_SCENES"] - 1
    # every mapped action is one the surface actually performs
    known = set(ns["LABELS"]) | set(ns["FX_NAMES"]) | {"chaos"}
    assert not {v for v in keymap.values() if v not in known}
    # the performing actions are all reachable
    for action in ("punch", "bigpunch", "refire", "reset", "freeze", "title",
                   "cut", "tap", "mute", "clearfx", "blackout", "strobe"):
        assert action in keymap.values(), action
    # no key is claimed twice, and the DAT parameter lists all of them
    assert len(set(keymap)) == len(keymap)
    assert set(ns["KEYS_PARAM"].split()) == set(keymap) | set(scenes)

    show = _fake_show(11, 8)
    ns2 = _load_apc(show)
    apc = _surface(show)
    # a number cuts to that scene
    assert ns2["on_key"](show, "3", True, apc) == ("scene", 2)
    assert show.par.Scene.val == 2
    assert ns2["on_key"](show, "0", True, apc) == ("scene", 9)
    # a letter performs, and only on the press unless it is a held action
    assert ns2["on_key"](show, "f", True, apc) == "freeze"
    assert show.par.Freeze.val == 1
    assert ns2["on_key"](show, "f", False, apc) is None      # no undo on release
    assert show.par.Freeze.val == 1
    # a held action undoes on the release, exactly as the pad does
    show.op("flow").par.Trail.val = 0.2
    show.par.Scene.val = 2
    assert ns2["on_key"](show, "l", True, apc) == "trailmax"
    assert show.op("flow").par.Trail.val > 0.9
    assert ns2["on_key"](show, "l", False, apc) == "trailmax"
    assert show.op("flow").par.Trail.val == 0.2
    # chaos is a burst on the show, not a scene action
    assert ns2["on_key"](show, "z", True, apc) == "chaos"
    assert show.par.Chaosburst.pulses == 1
    # an unmapped key is silently not ours
    assert ns2["on_key"](show, "q", True, apc) is None
    assert ns2["on_key"](None, "1", True, apc) is None
    # the builder listens for exactly the mapped keys and dispatches to on_key
    src = _src("touchdesigner", "td_build.py")
    assert "_apc.KEYS_PARAM" in src and "apc_mini.on_key(show, k, bool(state)" in src


def test_the_apc_panel_shows_the_real_led_state():
    """A live view has to come from what the surface actually sent, or it is
    just a second guess at the same thing."""
    cb = _src("touchdesigner", "callbacks", "apc_live_top.py")
    assert "apc_mini._LED_STATE" in cb                 # the real send cache
    assert "apc_mini.APC_RGB" in cb                    # the hardware's colours
    assert "_base_frame" in cb                         # the no-controller fallback
    assert "apc_map.json" in cb and "H - (y + h)" in cb   # y-down JSON into a y-up TOP
    # the geometry is exported beside the picture, not re-derived
    tool = _src("tools", "apc_map.py")
    assert "def geometry(" in tool and "json.dump(geometry(" in tool
    geo = os.path.join(ROOT, "docs", "apc_map.json")
    assert os.path.isfile(geo)
    with open(geo) as fh:
        g = json.load(fh)
    ns = _load_apc()
    assert len(g["grid"]) == 64
    assert set(g["round"]) == {str(n) for n in ns["TRACK_BTN"] + ns["SCENE_BTN"]}
    # the boxes sit inside the picture
    for box in list(g["grid"].values()) + list(g["round"].values()):
        x, y, w, h = box
        assert 0 <= x and x + w <= g["width"] and 0 <= y and y + h <= g["height"]
    # the dashboard composites the live pads over the printed map, pinned to it
    src = _src("touchdesigner", "td_build.py")
    dash = src[src.index("def build_dashboard("):src.index("def build_all(")]
    assert '_install_callbacks(live, "apc_live_top.py")' in dash
    assert "_set_res(lit, mw, mh)" in dash


def test_the_layout_is_three_panes_and_never_breaks_a_build():
    layout = _src("touchdesigner", "layout.py")
    assert "def three_panel(" in layout
    assert "NetworkEditor" in layout and layout.count('"Panel"') >= 2
    # several spellings per call, because the pane API differs between builds
    assert '"splitRight", "splitVertical"' in layout
    assert "while len(ui.panes) > 1" in layout      # idempotent, not ever-thinner strips
    src = _src("touchdesigner", "td_build.py")
    assert "layout.three_panel(program=panels.get" in src
    # the panes point at containers: a Panel pane can only show a COMP that has
    # a panel, and PhysicsVJ is a Base COMP, which has none (aiming a pane at
    # it draws an empty grey rectangle)
    assert '"containerCOMP", nm' in src
    dash = src[src.index("def build_dashboard("):src.index("def build_all(")]
    assert '"top", "background", "bgtop"' in dash
    # and the build says out loud what to point a pane at
    assert "to watch the show in a pane" in dash and "has no panel" in dash


def test_nbody_is_luminous_and_feynman_can_be_brightened():
    src = _src("touchdesigner", "td_build.py")
    nbody = src[src.index("def build_nbody("):src.index("def build_particles(")]
    assert '_instanced_geo(c, sim, "halo"' in nbody and 'look="soft"' in nbody
    assert "_render(c, [geo, halo], cam, lights)" in nbody
    assert '"shadowtype",), "soft"' in nbody           # bodies shadow each other
    assert 'op(\'geo\').par.ry' in nbody               # the shell turns with them
    import inspect
    from physics.feynman import FeynmanShow
    assert "gain" in inspect.signature(FeynmanShow.colours).parameters
    cb = _src("touchdesigner", "callbacks", "feynman_chop.py")
    assert 'appendFloat("Brightness"' in cb and "gain=float(" in cb

