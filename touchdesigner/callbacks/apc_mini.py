# Akai APC mini mk2 -> PhysicsVJ performance surface.
#
# This is the brain of the show's controls. It is loaded as the callbacks of an
# anchor Script CHOP (for onSetupParameters/onCook), and its plain functions are
# called by the operators td_build wires up:
#   * a MIDI In DAT  -> on_midi(apc, message, channel, index, value)
#   * Parameter Execute DATs -> repaint(apc) on show changes, reset(apc) on Reset
#   * the click / space-bar DATs and the show's own pulses -> perform(show, name)
#
# ---------------------------------------------------------------------------
# CONTROL MAP  (factory mode, MIDI channel 1; grid note = row*8 + col, row 0 =
# bottom). The 8x8 grid is FOUR QUADRANTS:
#
#   UPPER-LEFT  cols 0-3 rows 4-7   ACTIONS on the live scene ("induce things")
#       row 7:  PUNCH      BIG PUNCH   RE-FIRE     RESET
#       row 6:  PULSE      FREEZE      HOLD        TITLE
#       row 5:  TRAIL MAX  ORBIT FLIP  VARIANT     PALETTE >
#       row 4:  SCENE <    SCENE >     BLACKOUT    STROBE
#     PULSE is a manual beat; FREEZE stops every sim; HOLD is the Feynman
#     'Hold Lit' (Freeze elsewhere); TITLE overlays the scene's physics title
#     (tap = a few seconds, hold = while held); TRAIL MAX and STROBE act while
#     held; VARIANT steps the scene's own menu (initial condition, orbital,
#     form, event order, field).
#
#   UPPER-RIGHT cols 4-7 rows 4-7   WHOLE-SCREEN FX toggles
#       row 7:  INVERT     EDGES       POSTERIZE   PIXELATE
#       row 6:  MIRROR     MONO        SOLARIZE    FISHEYE
#       row 5:  TILES      SHAKE       ASCII       BLUR
#       row 4:  KALEIDO    RGB BOOST   STROBE      NEG ON BEAT
#     SHIFT + any FX pad clears them all.
#
#   LOWER-LEFT  cols 0-3 rows 0-3   SCENES (top-left pad = scene 0, reading
#       order), cut on deck A. SHIFT + pad arms it on deck B instead.
#
#   LOWER-RIGHT cols 4-7 rows 0-3   PALETTES + TEMPO / LEVEL tools
#       row 3:  palettes 0-3            row 2: palettes 4-7
#       row 1:  TAP  SYNC  BPM x2  BPM /2       (tempo engine)
#       row 0:  AUTO RESET  SENS-  SENS+  MUTE  (audio reactor)
#
#   TRACK BUTTONS (round, below grid, notes 100..107)
#       TITLE  FREEZE  PUNCH  TAP  CLEAR FX  PALETTE >  CUT  FREERUN
#   SCENE BUTTONS (round, right column, notes 112..119)
#       RESET LEDs  RE-FIRE  SCENE <  SCENE >  BLACKOUT  STROBE  BIG PUNCH  TITLE
#   FADERS (CC 48..55): the live scene's own parameters (FADER_MAP below;
#       1-3 are always Trail / Orbit / Point Size where the scene has them).
#   MASTER FADER (CC 56): Crossfade A/B.
#
# LEDs are sent by difference: repaint() remembers what each pad was last told
# and only re-sends pads that changed. reset() forgets that memory first.
import sys, os

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

try:
    from physics import palette
    _PALETTES = list(palette.PALETTE_NAMES)
except Exception:
    _PALETTES = ["inferno", "magma", "plasma", "cyber", "synth", "acid", "ice", "sigma"]

# Scene COMP names in build_all() order, and each scene's "re-fire" pulse.
SCENE_NAMES = ["ising", "nbody", "flow", "softbody", "lhc", "opendata",
               "rd", "sdf", "pops", "hydrogen", "feynman"]
REFIRE_PULSE = ["Reset", "Reset", "Punch", "Punch", "Newevent", "Nextevent",
                "Reseed", "Reseed", "Punch", "Reset", "Reseed"]
N_SCENES = len(SCENE_NAMES)
N_PAL = len(_PALETTES)
N_GRID_COLS = 8

# The scene's own menu that VARIANT steps: (op inside the scene or None for the
# COMP itself, parameter).
VARIANT_MENU = {
    "nbody": ("sim", "Initial"), "hydrogen": ("sim", "Orbital"),
    "sdf": (None, "Shape"), "opendata": ("sim", "Order"),
    "feynman": ("geo/lines", "Field"), "lhc": None, "ising": None,
    "flow": None, "softbody": None, "rd": None, "pops": None,
}

# Faders 1-8 per scene: (op inside the scene or None for the COMP, par, lo, hi).
# The first three are Trail / Orbit / Point Size wherever the scene has them.
_TRAIL = (None, "Trail", 0.0, 0.99)
_ORBIT = (None, "Orbit", -45.0, 45.0)
_PSIZE = ("sim", "Pointsize", 0.005, 0.2)
FADER_MAP = {
    "ising":    [None, None, None, ("sim", "Temperature", 1.5, 3.5), ("sim", "Wallglow", 0.0, 2.0),
                 ("sim", "Sweeps", 1, 6), None, None],
    "nbody":    [_TRAIL, _ORBIT, _PSIZE, ("sim", "Gravity", 0.0, 3.0), ("sim", "Timestep", 0.0005, 0.02),
                 ("sim", "Softening", 0.02, 0.6), ("sim", "Substeps", 1, 6), None],
    "flow":     [_TRAIL, _ORBIT, _PSIZE, ("sim", "Speed", 0.0, 5.0), ("sim", "Scale", 0.05, 0.8),
                 ("sim", "Evolve", 0.0, 1.0), ("sim", "Punchstrength", 0.2, 3.0), None],
    "softbody": [_TRAIL, _ORBIT, _PSIZE, ("sim", "Spin", 0.0, 4.0), ("sim", "Punchstrength", 0.2, 3.0),
                 None, None, None],
    "lhc":      [_TRAIL, _ORBIT, ("geo/sim", "Bfield", 0.5, 6.0), ("geo/sim", "Eventperiod", 1.0, 20.0),
                 ("geo/sim", "Growtime", 0.0, 5.0), ("geo/sim", "Scale", 0.1, 3.0), None, None],
    "opendata": [_TRAIL, _ORBIT, ("geo/sim", "Eventperiod", 0.25, 10.0), ("geo/sim", "Growtime", 0.0, 3.0),
                 ("geo/sim", "Scale", 0.1, 3.0), ("geo/sim", "History", 0, 40), (None, "Hudopacity", 0.0, 1.0), None],
    "rd":       [None, None, None, (None, "Feed", 0.02, 0.06), (None, "Kill", 0.045, 0.07), None, None, None],
    "sdf":      [None, None, None, (None, "Speed", 0.0, 4.0), (None, "Twist", 0.0, 4.0),
                 (None, "Zoom", 0.3, 3.0), (None, "Detail", 1, 4), (None, "Morph", 0.0, 1.0)],
    "pops":     [_TRAIL, _ORBIT, _PSIZE, ("sim", "Speed", 0.0, 5.0), ("sim", "Scale", 0.05, 0.8),
                 ("sim", "Evolve", 0.0, 1.0), ("sim", "Punchstrength", 0.2, 3.0), None],
    "hydrogen": [_TRAIL, _ORBIT, _PSIZE, ("sim", "Speed", 0.0, 12.0), ("sim", "Refresh", 0.0, 4.0),
                 ("sim", "Substeps", 1, 6), None, None],
    "feynman":  [_TRAIL, _ORBIT, ("state", "Tail", 0.05, 1.2), ("state", "Traverse", 4.0, 180.0),
                 ("state", "Growlen", 1.0, 12.0), ("state", "Fade", 0.05, 1.0), ("state", "Walkers", 1, 6), None],
}

# Whole-screen FX toggles on PhysicsVJ (the post shader reads them).
FX_NAMES = ["Invert", "Edges", "Posterize", "Pixelate",
            "Mirror", "Mono", "Solarize", "Fisheye",
            "Tiles", "Shake", "Ascii", "Blur",
            "Kaleidofx", "Rgbboost", "Strobe", "Negflash"]

# Upper-left actions, top row first, four per row (cols 0..3).
ACTION_GRID = [
    ["punch", "bigpunch", "refire", "reset"],
    ["pulse", "freeze", "hold", "title"],
    ["trailmax", "orbitflip", "variant", "palette"],
    ["sceneprev", "scenenext", "blackout", "strobe"],
]
# Lower-right tools, rows 1 and 0 (rows 3 and 2 are the palettes).
TOOL_ROW1 = ["tap", "sync", "bpmx2", "bpmhalf"]
TOOL_ROW0 = ["autoreset", "sensdown", "sensup", "mute"]
MOMENTARY = {"trailmax", "strobe", "title"}     # act on press, undo on release

# --- APC mini mk2 hardware map -------------------------------------------
TRACK_BTN = [100, 101, 102, 103, 104, 105, 106, 107]  # bottom round buttons
SCENE_BTN = [112, 113, 114, 115, 116, 117, 118, 119]  # right column buttons
SHIFT = 122
FADER_CC = [48, 49, 50, 51, 52, 53, 54, 55, 56]       # 9 faders; [8] = master
TRACK_ACTIONS = ["title", "freeze", "punch", "tap", "clearfx", "palette", "cut", "freerun"]
SCENE_ACTIONS = ["ledreset", "refire", "sceneprev", "scenenext", "blackout", "strobe", "bigpunch", "title"]
BTN_CUT = TRACK_BTN[6]
BTN_FREERUN = TRACK_BTN[7]
BTN_RESET = SCENE_BTN[0]
BTN_REFIRE = SCENE_BTN[1]

# sendNoteOn(channel, note, velocity): the channel selects the LED behaviour
# (mk2: ch1..7 = 10%..100% solid, 8..11 = pulse, 12..16 = blink).
CH_DIM, CH_MID, CH_BRIGHT, CH_PULSE, CH_BLINK = 2, 4, 7, 10, 14

# APC 128-colour palette indices.
PAL_COLOR = {
    "inferno": 9, "magma": 5, "plasma": 53, "cyber": 37,
    "synth": 49, "acid": 21, "ice": 41, "sigma": 60,
}
DEFAULT_COLOR = 3          # white
COL_ACTION, COL_ACTION_ON = 3, 13      # white / yellow
COL_FX_OFF, COL_FX_ON = 49, 21         # purple / green
COL_TOOL, COL_TOOL_ON = 37, 5          # cyan / red
COL_SCENE_EMPTY = 0
ROUND_OFF, ROUND_ON, ROUND_BLINK = 0, 1, 2

TITLE_TAP_SECONDS = 4.0    # a tapped TITLE stays this long; held stays while held
TITLE_TAP_MAX = 0.35       # a press shorter than this counts as a tap


# ---------------------------------------------------------------------------
# Parameter helpers (never raise -- matches the rest of the TD layer)
# ---------------------------------------------------------------------------
def _menu_index(o, name, default=0):
    try:
        return int(getattr(o.par, name).menuIndex)
    except Exception:
        return default


def _set_menu(o, name, idx):
    try:
        getattr(o.par, name).menuIndex = int(idx)
    except Exception:
        try:
            getattr(o.par, name).val = int(idx)
        except Exception:
            pass


def _menu_len(o, name):
    try:
        return len(getattr(o.par, name).menuNames)
    except Exception:
        return 0


def _getf(o, name, default=0.0):
    try:
        return float(getattr(o.par, name).eval())
    except Exception:
        return default


def _setf(o, name, v):
    try:
        getattr(o.par, name).val = v
    except Exception:
        pass


def _geti(o, name, default=0):
    try:
        return int(getattr(o.par, name).eval())
    except Exception:
        return default


def _toggle(o, name):
    try:
        getattr(o.par, name).val = 0 if int(getattr(o.par, name).eval()) else 1
    except Exception:
        pass


def _pulse(o, name):
    try:
        getattr(o.par, name).pulse()
    except Exception:
        pass


def _has(o, name):
    try:
        return o is not None and hasattr(o.par, name)
    except Exception:
        return False


def _now():
    try:
        return float(absTime.seconds)  # noqa: F821 (TD global)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Resolving the show + its scenes
# ---------------------------------------------------------------------------
_STATE = {}   # per-surface: shift held, tap times, press times, held actions


def _st(apc):
    try:
        key = apc.path
    except Exception:
        key = id(apc)
    return _STATE.setdefault(key, {"shift": False, "taps": [], "pressed": {}, "held": {}})


def _target(apc):
    """The PhysicsVJ component this surface drives (None if unset/missing)."""
    try:
        path = apc.par.Target.eval()
    except Exception:
        path = ""
    if not path:
        return None
    try:
        return op(path)  # noqa: F821 (TD global)
    except Exception:
        return None


def _scene_comp(t, idx):
    if t is None or not (0 <= idx < N_SCENES):
        return None
    try:
        return t.op(SCENE_NAMES[idx])
    except Exception:
        return None


def _scene_sim(t, idx):
    """The Script OP that carries a scene's live parameters: 'sim' for most
    scenes (inside 'geo' for the polyline scenes), 'state' for Feynman."""
    c = _scene_comp(t, idx)
    if c is None:
        return None
    try:
        return c.op("sim") or c.op("geo/sim") or c.op("state")
    except Exception:
        return None


def _scene_op(t, idx, where):
    """An op inside scene ``idx`` by relative path, or the scene COMP for None."""
    c = _scene_comp(t, idx)
    if c is None:
        return None
    if where is None:
        return c
    try:
        return c.op(where)
    except Exception:
        return None


def _live_index(t):
    return _menu_index(t, "Scene", 0)


def _palette_holder(t, idx):
    """The op carrying a scene's Palette menu: the 'sim' (numpy scenes) or the
    scene COMP itself (GLSL scenes like rd/sdf)."""
    sim = _scene_sim(t, idx)
    if sim is not None and _has(sim, "Palette"):
        return sim
    return _scene_comp(t, idx)


def _scene_palette(t, idx):
    holder = _palette_holder(t, idx)
    return _menu_index(holder, "Palette", -1) if holder is not None else -1


def _analyze(t):
    try:
        return t.op("Reactor/analyze")
    except Exception:
        return None


def _tempo(t):
    try:
        return t.op("Tempo/tempo")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Actions on the show / the live scene
# ---------------------------------------------------------------------------
def perform(t, action, apc=None, pressed=True):
    """Do one named action on the show ``t`` (the PhysicsVJ COMP).

    This is the single entry point for every control: APC pads, the round
    buttons, the click / space-bar DATs and the show's own pulses all end up
    here. ``pressed`` is False on the release of a momentary action.
    """
    if t is None:
        return
    live = _live_index(t)
    sim = _scene_sim(t, live)
    comp = _scene_comp(t, live)
    st = _st(apc) if apc is not None else {"held": {}, "taps": []}

    if action in ("punch", "bigpunch"):
        if _has(sim, "Punch"):
            if action == "bigpunch":
                _setf(sim, "Punchstrength", 3.0)
            _pulse(sim, "Punch")
        else:
            _refire(t)
    elif action == "refire":
        _refire(t)
    elif action == "reset":
        for o in (sim, comp):
            if _has(o, "Reset"):
                _pulse(o, "Reset")
                break
    elif action == "pulse":
        _pulse(_analyze(t), "Hit")
    elif action == "freeze":
        _toggle(t, "Freeze")
    elif action == "hold":
        if _has(sim, "Hold"):
            _toggle(sim, "Hold")
        else:
            _toggle(t, "Freeze")
    elif action == "title":
        _title(t, st, pressed)
    elif action == "trailmax":
        if pressed:
            if comp is not None and _has(comp, "Trail"):
                st["held"]["trail"] = (comp, _getf(comp, "Trail"))
                _setf(comp, "Trail", 0.975)
        else:
            held = st["held"].pop("trail", None)
            if held:
                _setf(held[0], "Trail", held[1])
    elif action == "orbitflip":
        if _has(comp, "Orbit"):
            _setf(comp, "Orbit", -_getf(comp, "Orbit"))
    elif action == "variant":
        spec = VARIANT_MENU.get(SCENE_NAMES[live] if 0 <= live < N_SCENES else "")
        if spec:
            o = _scene_op(t, live, spec[0])
            n = _menu_len(o, spec[1])
            if n:
                _set_menu(o, spec[1], (_menu_index(o, spec[1]) + 1) % n)
    elif action == "palette":
        holder = _palette_holder(t, live)
        if holder is not None:
            _set_menu(holder, "Palette", (_scene_palette(t, live) + 1) % N_PAL)
    elif action == "sceneprev":
        _set_menu(t, "Scene", (live - 1) % N_SCENES)
    elif action == "scenenext":
        _set_menu(t, "Scene", (live + 1) % N_SCENES)
    elif action == "blackout":
        _toggle(t, "Blackout")
    elif action == "strobe":
        _setf(t, "Strobe", 1 if pressed else 0)
    elif action in FX_NAMES:
        _toggle(t, action)
    elif action == "clearfx":
        for fx in FX_NAMES:
            _setf(t, fx, 0)
    elif action == "cut":
        _pulse(t, "Cut")
    elif action == "freerun":
        _toggle(t, "Freerunall")
    elif action == "tap":
        _tap(t, st)
    elif action == "sync":
        _pulse(_tempo(t), "Resetphase")
        _pulse(_analyze(t), "Hit")
    elif action in ("bpmx2", "bpmhalf"):
        tempo = _tempo(t)
        bpm = _getf(tempo, "Bpm", 120.0) * (2.0 if action == "bpmx2" else 0.5)
        _setf(tempo, "Bpm", min(max(bpm, 40.0), 300.0))
    elif action == "autoreset":
        _pulse(_analyze(t), "Resetlevels")
    elif action in ("sensdown", "sensup"):
        a = _analyze(t)
        v = _getf(a, "Beatsens", 1.6) + (0.15 if action == "sensup" else -0.15)
        _setf(a, "Beatsens", min(max(v, 1.05), 3.0))
    elif action == "mute":
        _toggle(_analyze(t), "Mute")
    elif action == "ledreset" and apc is not None:
        reset(apc)


def _refire(t):
    """Re-trigger the live scene's signature event (re-collide / next event /
    reseed / punch). The pulse lives on the 'sim' or on the COMP."""
    idx = _live_index(t)
    if not (0 <= idx < N_SCENES):
        return
    name = REFIRE_PULSE[idx]
    sim = _scene_sim(t, idx)
    if _has(sim, name):
        _pulse(sim, name)
    else:
        _pulse(_scene_comp(t, idx), name)


def _title(t, st, pressed):
    """Show the live scene's title: while held, or TITLE_TAP_SECONDS on a tap.
    The overlay reads 'title_t0' / 'title_toff' from the show's storage."""
    now = _now()
    try:
        if pressed:
            st["title_t0"] = now
            t.store("title_t0", now)
            t.store("title_toff", 1e9)              # hold until told otherwise
        else:
            t0 = st.get("title_t0", now)
            toff = now + TITLE_TAP_SECONDS if (now - t0) < TITLE_TAP_MAX else now
            t.store("title_toff", max(toff, t0 + 0.6))
    except Exception:
        pass


def title_pulse(t, seconds=TITLE_TAP_SECONDS):
    """The show's own 'Title' pulse: show the title for ``seconds``."""
    now = _now()
    try:
        t.store("title_t0", now)
        t.store("title_toff", now + float(seconds))
    except Exception:
        pass


def _tap(t, st):
    """Tap tempo: the median gap of the last taps sets the tempo engine's
    manual BPM and re-anchors its phase on this tap."""
    now = _now()
    taps = [x for x in st.get("taps", []) if now - x < 3.0]
    taps.append(now)
    st["taps"] = taps[-8:]
    tempo = _tempo(t)
    _pulse(tempo, "Resetphase")
    if len(taps) >= 2:
        gaps = sorted(b - a for a, b in zip(taps, taps[1:]))
        med = gaps[len(gaps) // 2]
        if med > 0.2:
            _setf(tempo, "Bpm", min(max(60.0 / med, 40.0), 300.0))


# ---------------------------------------------------------------------------
# Grid geometry
# ---------------------------------------------------------------------------
def _grid_note(col, row):
    return row * 8 + col


def _scene_at(col, row):
    """Lower-left quadrant: scene index for (col 0..3, row 0..3), reading order."""
    idx = (3 - row) * 4 + col
    return idx if idx < N_SCENES else None


def _scene_pad(idx):
    return _grid_note(idx % 4, 3 - idx // 4)


def _decode(note):
    """What a grid note means: ('scene', i) | ('palette', i) | ('action', name)
    | ('fx', name) | ('tool', name) | None."""
    if not (0 <= note <= 63):
        return None
    col, row = note % 8, note // 8
    if col < 4 and row < 4:
        i = _scene_at(col, row)
        return ("scene", i) if i is not None else None
    if col >= 4 and row < 4:
        c = col - 4
        if row == 3:
            return ("palette", c) if c < N_PAL else None
        if row == 2:
            return ("palette", 4 + c) if 4 + c < N_PAL else None
        if row == 1:
            return ("tool", TOOL_ROW1[c])
        return ("tool", TOOL_ROW0[c])
    if col < 4:                                  # upper-left: actions
        return ("action", ACTION_GRID[7 - row][col])
    return ("fx", FX_NAMES[(7 - row) * 4 + (col - 4)])


# ---------------------------------------------------------------------------
# LED output
# ---------------------------------------------------------------------------
def _ledout(apc):
    try:
        return apc.op("ledout")
    except Exception:
        return None


_LED_STATE = {}   # {apc.path: {note: (velocity, channel)}}


def _led_cache(apc):
    try:
        key = apc.path
    except Exception:
        key = id(apc)
    return _LED_STATE.setdefault(key, {})


def _pad(apc, note, velocity, channel=CH_BRIGHT, force=False):
    """Light one pad -- only if that differs from what it was last sent."""
    cache = _led_cache(apc)
    want = (int(velocity), int(channel))
    if not force and cache.get(int(note)) == want:
        return
    o = _ledout(apc)
    if o is None:
        return
    try:
        if hasattr(o, "sendNoteOn"):
            o.sendNoteOn(int(channel), int(note), int(velocity))
        else:
            o.sendMIDI("note", int(channel), int(note), int(velocity))
        cache[int(note)] = want
    except Exception:
        pass


def _round(apc, note, state):
    """Light a single-colour round button: 0 off, 1 on, 2 blink."""
    _pad(apc, note, int(state), CH_BRIGHT)


def _blank(apc):
    """Turn every LED off, unconditionally -- the first half of a resync."""
    _led_cache(apc).clear()
    for n in range(64):
        _pad(apc, n, 0, CH_BRIGHT, force=True)
    for n in TRACK_BTN + SCENE_BTN:
        _pad(apc, n, ROUND_OFF, CH_BRIGHT, force=True)


def _action_state(t, name, st):
    """Whether an action pad should read as 'on'."""
    if name == "freeze":
        return _geti(t, "Freeze", 0)
    if name == "blackout":
        return _geti(t, "Blackout", 0)
    if name == "strobe":
        return _geti(t, "Strobe", 0)
    if name == "hold":
        sim = _scene_sim(t, _live_index(t))
        return _geti(sim, "Hold", 0) if _has(sim, "Hold") else _geti(t, "Freeze", 0)
    if name == "title":
        try:
            return 1 if t.fetch("title_toff", -1e9) > _now() else 0
        except Exception:
            return 0
    if name == "trailmax":
        return 1 if "trail" in st.get("held", {}) else 0
    if name == "mute":
        return _geti(_analyze(t), "Mute", 0)
    return 0


def repaint(apc):
    """Drive every LED to reflect the current show state."""
    t = _target(apc)
    if t is None:
        _blank(apc)
        return
    st = _st(apc)
    live = _live_index(t)
    nxt = _menu_index(t, "Nextscene", -1)
    cf = _getf(t, "Crossfade", 0.0)
    live_pal = _scene_palette(t, live)

    for note in range(64):
        kind = _decode(note)
        if kind is None:
            _pad(apc, note, COL_SCENE_EMPTY, CH_BRIGHT)
            continue
        what, val = kind
        if what == "scene":
            col = PAL_COLOR.get(_PALETTES[_scene_palette(t, val)] if 0 <= _scene_palette(t, val) < N_PAL else "", DEFAULT_COLOR)
            if val == live:
                _pad(apc, note, col, CH_PULSE)
            elif val == nxt:
                _pad(apc, note, col, CH_BLINK)
            else:
                _pad(apc, note, col, CH_DIM)
        elif what == "palette":
            col = PAL_COLOR.get(_PALETTES[val], DEFAULT_COLOR)
            _pad(apc, note, col, CH_BRIGHT if val == live_pal else CH_DIM)
        elif what == "action":
            on = _action_state(t, val, st)
            _pad(apc, note, COL_ACTION_ON if on else COL_ACTION, CH_BRIGHT if on else CH_DIM)
        elif what == "fx":
            on = _geti(t, val, 0)
            _pad(apc, note, COL_FX_ON if on else COL_FX_OFF, CH_BRIGHT if on else CH_DIM)
        elif what == "tool":
            on = _action_state(t, val, st)
            _pad(apc, note, COL_TOOL_ON if on else COL_TOOL, CH_BRIGHT if on else CH_DIM)

    # Round buttons: lit when their state is on; Cut lit mid-fade.
    for note, name in zip(TRACK_BTN, TRACK_ACTIONS):
        if name == "cut":
            _round(apc, note, ROUND_ON if cf > 0 else ROUND_OFF)
        elif name == "freerun":
            _round(apc, note, ROUND_ON if _geti(t, "Freerunall", 0) else ROUND_OFF)
        elif name in ("title", "freeze", "blackout", "strobe"):
            _round(apc, note, ROUND_ON if _action_state(t, name, st) else ROUND_OFF)
        else:
            _round(apc, note, ROUND_ON)
    for note, name in zip(SCENE_BTN, SCENE_ACTIONS):
        if name in ("title", "freeze", "blackout", "strobe"):
            _round(apc, note, ROUND_ON if _action_state(t, name, st) else ROUND_OFF)
        else:
            _round(apc, note, ROUND_ON)


def reset(apc):
    """The reset mechanism: blank every LED, then repaint from scratch."""
    _blank(apc)
    repaint(apc)


# ---------------------------------------------------------------------------
# Public: incoming MIDI
# ---------------------------------------------------------------------------
def on_midi(apc, message, channel, index, value):
    """Handle one MIDI message from the APC. ``message`` is TD's text type
    ('Note On', 'Note Off', 'Control Change', ...)."""
    msg = (message or "").lower()
    t = _target(apc)
    st = _st(apc)
    note, value = int(index), int(value)
    if "control" in msg:
        _on_fader(apc, t, note, value)
        return
    if "note" not in msg:
        return
    is_on = "note on" in msg and value > 0
    if note == SHIFT:
        st["shift"] = is_on
        return
    if is_on:
        _on_press(apc, t, st, note)
    else:
        _on_release(apc, t, st, note)


def _action_for(note):
    """The action a non-grid button maps to (None for grid notes)."""
    if note in TRACK_BTN:
        return TRACK_ACTIONS[TRACK_BTN.index(note)]
    if note in SCENE_BTN:
        return SCENE_ACTIONS[SCENE_BTN.index(note)]
    return None


def _on_press(apc, t, st, note):
    if t is None:
        if note == BTN_RESET:
            reset(apc)
        return
    st["pressed"][note] = _now()
    kind = _decode(note)
    if kind is not None:
        what, val = kind
        if what == "scene":
            _set_menu(t, "Nextscene" if st.get("shift") else "Scene", val)
        elif what == "palette":
            holder = _palette_holder(t, _live_index(t))
            if holder is not None:
                _set_menu(holder, "Palette", val)
        elif what == "fx":
            perform(t, "clearfx" if st.get("shift") else val, apc)
        else:                                    # action / tool
            perform(t, val, apc, pressed=True)
        repaint(apc)
        return
    action = _action_for(note)
    if action is not None:
        perform(t, action, apc, pressed=True)
        repaint(apc)


def _on_release(apc, t, st, note):
    st["pressed"].pop(note, None)
    if t is None:
        return
    kind = _decode(note)
    name = None
    if kind is not None and kind[0] in ("action", "tool"):
        name = kind[1]
    else:
        name = _action_for(note)
    if name in MOMENTARY:
        perform(t, name, apc, pressed=False)
        repaint(apc)


def _on_fader(apc, t, cc, value):
    v = max(0.0, min(1.0, value / 127.0))
    if cc == FADER_CC[8]:                           # master -> crossfade
        if t is not None:
            _setf(t, "Crossfade", v)
        repaint(apc)
        return
    if t is None or cc not in FADER_CC:
        return
    live = _live_index(t)
    if not (0 <= live < N_SCENES):
        return
    spec = FADER_MAP.get(SCENE_NAMES[live], [None] * 8)[FADER_CC.index(cc)]
    if spec is None:
        return
    where, par, lo, hi = spec
    o = _scene_op(t, live, where)
    if o is None:
        return
    val = lo + v * (hi - lo)
    if isinstance(lo, int) and isinstance(hi, int):
        val = int(round(val))
    _setf(o, par, val)


# ---------------------------------------------------------------------------
# Script CHOP anchor hooks (so this file matches the callback contract)
# ---------------------------------------------------------------------------
def onSetupParameters(scriptOp):
    """Build the show-surface parameters on the anchor's parent COMP."""
    apc = scriptOp.parent()
    if hasattr(apc.par, "Target"):
        return  # idempotent
    page = apc.appendCustomPage("APC Show")
    page.appendStr("Target", label="PhysicsVJ Target")[0].val = "../PhysicsVJ"
    d = page.appendInt("Device", label="MIDI Device ID")[0]
    d.val = 1
    try:
        apc.par.Device.normMin, apc.par.Device.normMax = 1, 8
        apc.par.Device.clampMin = True
    except Exception:
        pass
    page.appendPulse("Reset", label="Reset Controller (resync LEDs)")


def onCook(scriptOp):
    # The anchor does no per-frame work; the surface is event-driven.
    scriptOp.clear()


setupParameters = onSetupParameters
cook = onCook
