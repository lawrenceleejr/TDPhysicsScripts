# Akai APC mini mk2 -> PhysicsVJ performance surface.
#
# This is the brain of the show's controls. It is loaded as the callbacks of an
# anchor Script CHOP (for onSetupParameters/onCook), and its plain functions are
# called by the operators td_build wires up:
#   * a MIDI In DAT  -> on_midi(apc, message, channel, index, value)
#   * Parameter Execute DATs -> repaint(apc) on show changes, reset(apc) on Reset
#   * an Execute DAT (frame start) -> tick(apc): audio-reactive LEDs + animations
#   * the click / space-bar DATs and the show's own pulses -> perform(show, name)
#
# ---------------------------------------------------------------------------
# CONTROL MAP  (factory mode, MIDI channel 1; grid note = row*8 + col, row 0 =
# bottom). `python tools/apc_map.py` draws this map (docs/apc_map.svg).
#
# The 8x8 grid is FOUR QUADRANTS:
#
#   UPPER-LEFT  cols 0-3 rows 4-7   ACTIONS on the live scene ("induce things")
#       row 7:  PUNCH      BIG PUNCH   RE-FIRE     RESET
#       row 6:  PULSE      FREEZE      HOLD        TITLE
#       row 5:  TRAIL MAX  ORBIT FLIP  VARIANT     PALETTE >
#       row 4:  SCENE <    SCENE >     BLACKOUT    STROBE
#     PULSE is a manual beat (its pad also flashes with every detected kick);
#     FREEZE stops every sim; HOLD is the Feynman 'Hold Lit' (Freeze elsewhere);
#     TITLE overlays the scene's physics title (tap = a few seconds, hold =
#     while held); TRAIL MAX and STROBE act while held; VARIANT steps the
#     scene's own menu (initial condition, orbital, form, event order, field).
#
#   UPPER-RIGHT cols 4-7 rows 4-7   WHOLE-SCREEN FX toggles
#       row 7:  INVERT     EDGES       POSTERIZE   PIXELATE
#       row 6:  MIRROR     MONO        SOLARIZE    FISHEYE
#       row 5:  TILES      SHAKE       ASCII       BLUR
#       row 4:  KALEIDO    RGB BOOST   STROBE      NEG ON BEAT
#     SHIFT + any FX pad clears them all. Pads that are on breathe with the beat.
#
#   LOWER-LEFT  cols 0-3 rows 0-3   SCENE QUEUE: the scenes in reading order
#       (top-left pad = scene 0), lit in their palette colour. Press to ARM a
#       scene on deck B (it blinks) and ride the master fader / hit CUT;
#       SHIFT + pad cuts straight to it. The live scene pulses with the music.
#
#   LOWER-RIGHT cols 4-7 rows 0-3   PALETTES + TEMPO / LEVEL tools
#       row 3:  palettes 0-3            row 2: palettes 4-7
#       row 1:  TAP  SYNC  BPM x2  BPM /2       (tempo engine; TAP ticks)
#       row 0:  AUTO RESET  SENS-  SENS+  MUTE  (audio reactor; MUTE = the
#               show's Reactive switch, red while the visuals ignore the music)
#
#   SCENE BUTTONS (round, right column, notes 112..119)  SCENE SELECT
#       button k cuts to scene k (0-7); SHIFT + button k cuts to scene 8+k.
#       Lit = live scene, blinking = armed on deck B. Holding SHIFT shows the
#       shifted layer.
#   TRACK BUTTONS (round, below grid, notes 100..107)
#       TITLE  FREEZE  PUNCH  TAP  CLEAR FX  PALETTE >  CUT  FREERUN
#       + SHIFT: RESET LEDs  RE-FIRE  BIG PUNCH  SYNC  HOLD  VARIANT  SCENE <  SCENE >
#   FADERS (CC 48..55): the live scene's own parameters (FADER_MAP below;
#       1-3 are always Trail / Orbit / Point Size where the scene has them).
#   MASTER FADER (CC 56): Crossfade A/B.
#
# LEDs are sent by difference: every frame `tick` works out what each pad
# should show (base state + audio-reactive layer + running animations) and
# only re-sends pads that changed. reset() forgets that memory first.
import math
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
                 ("sim", "Evolve", 0.0, 1.0), ("sim", "Punchstrength", 0.2, 4.0), None],
    "softbody": [_TRAIL, _ORBIT, _PSIZE, ("sim", "Spin", 0.0, 4.0), ("sim", "Punchstrength", 0.2, 4.0),
                 None, None, None],
    "lhc":      [_TRAIL, _ORBIT, ("geo/sim", "Bfield", 0.5, 6.0), ("geo/sim", "Eventperiod", 1.0, 20.0),
                 ("geo/sim", "Growtime", 0.0, 5.0), ("geo/sim", "Scale", 0.1, 3.0), None, None],
    "opendata": [_TRAIL, _ORBIT, ("geo/sim", "Eventperiod", 0.25, 10.0), ("geo/sim", "Growtime", 0.0, 3.0),
                 ("geo/sim", "Scale", 0.1, 3.0), ("geo/sim", "History", 0, 40), (None, "Hudopacity", 0.0, 1.0), None],
    "rd":       [None, None, None, (None, "Feed", 0.02, 0.06), (None, "Kill", 0.045, 0.07), None, None, None],
    "sdf":      [None, None, None, (None, "Speed", 0.0, 4.0), (None, "Twist", 0.0, 4.0),
                 (None, "Zoom", 0.3, 3.0), (None, "Detail", 1, 4), (None, "Morph", 0.0, 1.0)],
    "pops":     [_TRAIL, _ORBIT, _PSIZE, ("sim", "Speed", 0.0, 5.0), ("sim", "Scale", 0.05, 0.8),
                 ("sim", "Evolve", 0.0, 1.0), ("sim", "Punchstrength", 0.2, 4.0), None],
    "hydrogen": [_TRAIL, _ORBIT, _PSIZE, ("sim", "Speed", 0.0, 4.0), ("sim", "Morphtime", 0.5, 25.0),
                 ("sim", "Depthcue", 0.0, 1.0), ("sim", "Refresh", 0.0, 2.0), None],
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

# Short labels for the map / docs (tools/apc_map.py reads these).
LABELS = {
    "punch": "PUNCH", "bigpunch": "BIG PUNCH", "refire": "RE-FIRE", "reset": "RESET",
    "pulse": "PULSE", "freeze": "FREEZE", "hold": "HOLD", "title": "TITLE",
    "trailmax": "TRAIL MAX", "orbitflip": "ORBIT FLIP", "variant": "VARIANT", "palette": "PALETTE >",
    "sceneprev": "SCENE <", "scenenext": "SCENE >", "blackout": "BLACKOUT", "strobe": "STROBE",
    "tap": "TAP", "sync": "SYNC", "bpmx2": "BPM x2", "bpmhalf": "BPM /2",
    "autoreset": "AUTO RESET", "sensdown": "SENS -", "sensup": "SENS +", "mute": "MUTE",
    "clearfx": "CLEAR FX", "cut": "CUT", "freerun": "FREERUN", "ledreset": "RESET LEDS",
    "Invert": "INVERT", "Edges": "EDGES", "Posterize": "POSTERIZE", "Pixelate": "PIXELATE",
    "Mirror": "MIRROR", "Mono": "MONO", "Solarize": "SOLARIZE", "Fisheye": "FISHEYE",
    "Tiles": "TILES", "Shake": "SHAKE", "Ascii": "ASCII", "Blur": "BLUR",
    "Kaleidofx": "KALEIDO", "Rgbboost": "RGB BOOST", "Strobe": "STROBE", "Negflash": "NEG / BEAT",
}
SCENE_LABELS = {
    "ising": "ISING", "nbody": "N-BODY", "flow": "FLOW", "softbody": "SOFT BODY",
    "lhc": "LHC", "opendata": "OPEN DATA", "rd": "REACT-DIFF", "sdf": "RAYMARCH",
    "pops": "POP STORM", "hydrogen": "BOHMIAN H", "feynman": "FEYNMAN",
}

# ---------------------------------------------------------------------------
# THE KEYBOARD: the same surface without the hardware.
#
# Every action the pads reach has a key, so the show can be built, tested and
# even played on a laptop alone. Number keys are the scene switcher (1-9 then
# 0 for scene 10, matching the pads' reading order), letters are the actions,
# and the bracket keys walk the palettes. Held keys (trail, strobe, title)
# behave as they do on the pads: they act on the down and undo on the up.
KEY_SCENES = "1234567890"          # scene 0..10, in the pads' order
KEYMAP = {
    # performing
    "p": "punch", "P": "bigpunch", "r": "refire", "R": "reset",
    "b": "pulse", "f": "freeze", "h": "hold", "t": "title",
    "l": "trailmax", "o": "orbitflip", "v": "variant",
    "[": "palette", "]": "palette",
    ",": "sceneprev", ".": "scenenext",
    "k": "blackout", "s": "strobe",
    # transport
    "c": "cut", "g": "freerun", "space": "punch",
    # tempo and levels
    "m": "tap", "y": "sync", "u": "bpmx2", "j": "bpmhalf",
    "a": "autoreset", "-": "sensdown", "=": "sensup", "n": "mute",
    # the look
    "x": "clearfx", "z": "chaos",
    "F1": "Invert", "F2": "Edges", "F3": "Posterize", "F4": "Pixelate",
    "F5": "Mirror", "F6": "Mono", "F7": "Solarize", "F8": "Fisheye",
    "F9": "Tiles", "F10": "Shake", "F11": "Ascii", "F12": "Blur",
}
# Every key the Keyboard In DAT has to listen for, as one parameter value.
KEYS_PARAM = " ".join(sorted(set(list(KEY_SCENES) + list(KEYMAP))))


def on_key(t, key, pressed, apc=None):
    """Handle one key from the show's Keyboard In DAT.

    Returns the action it ran (or ``('scene', i)``), or None if the key is not
    mapped -- so the caller can stay quiet about keys that mean nothing here.
    """
    if t is None or not key:
        return None
    if key in KEY_SCENES:
        if not pressed:
            return None
        idx = KEY_SCENES.index(key)
        if idx < N_SCENES:
            _set_menu(t, "Scene", idx)
            if apc is not None:
                st = _st(apc)
                _animate(st, "scene", (3.5, 3.5), _scene_color(t, idx))
                repaint(apc)
            return ("scene", idx)
        return None
    action = KEYMAP.get(key)
    if action is None:
        return None
    if not pressed and action not in MOMENTARY:
        return None                     # only held actions care about the up
    if action == "chaos":
        if pressed:
            _pulse(t, "Chaosburst")
        return action
    perform(t, action, apc, pressed=pressed)
    if apc is not None:
        repaint(apc)
    return action


# --- APC mini mk2 hardware map -------------------------------------------
TRACK_BTN = [100, 101, 102, 103, 104, 105, 106, 107]  # bottom round buttons
SCENE_BTN = [112, 113, 114, 115, 116, 117, 118, 119]  # right column buttons
SHIFT = 122
FADER_CC = [48, 49, 50, 51, 52, 53, 54, 55, 56]       # 9 faders; [8] = master
TRACK_ACTIONS = ["title", "freeze", "punch", "tap", "clearfx", "palette", "cut", "freerun"]
SHIFT_TRACK_ACTIONS = ["ledreset", "refire", "bigpunch", "sync", "hold", "variant", "sceneprev", "scenenext"]
BTN_CUT = TRACK_BTN[6]
BTN_FREERUN = TRACK_BTN[7]
BTN_RESET = TRACK_BTN[0]      # with SHIFT held
BTN_REFIRE = TRACK_BTN[1]     # with SHIFT held

# sendNoteOn(channel, note, velocity): the channel selects the LED behaviour
# (mk2: ch1..7 = 10%..100% solid, 8..11 = pulse, 12..16 = blink).
CH_DIM, CH_MID, CH_BRIGHT, CH_PULSE, CH_BLINK = 2, 4, 7, 10, 14
CH_LEVELS = [2, 3, 4, 5, 6, 7]     # solid brightness steps for reactive pads

# The APC mini mk2's 128 LED colours (velocity -> sRGB), from Akai's protocol
# table. Used to pick, for each show palette, the pad colour closest to that
# palette's most saturated colour -- so a pad glows in the colour you will see.
APC_RGB = [
    0x000000, 0x1E1E1E, 0x7F7F7F, 0xFFFFFF, 0xFF4C4C, 0xFF0000, 0x590000, 0x190000,
    0xFFBD6C, 0xFF5400, 0x591D00, 0x271B00, 0xFFFF4C, 0xFFFF00, 0x595900, 0x191900,
    0x88FF4C, 0x54FF00, 0x1D5900, 0x142B00, 0x4CFF4C, 0x00FF00, 0x005900, 0x001900,
    0x4CFF5E, 0x00FF19, 0x00590D, 0x001902, 0x4CFF88, 0x00FF55, 0x00591D, 0x001F12,
    0x4CFFB7, 0x00FF99, 0x005935, 0x001912, 0x4CC3FF, 0x00A9FF, 0x004152, 0x001019,
    0x4C88FF, 0x0055FF, 0x001D59, 0x000819, 0x4C4CFF, 0x0000FF, 0x000059, 0x000019,
    0x874CFF, 0x5400FF, 0x190064, 0x0F0030, 0xFF4CFF, 0xFF00FF, 0x590059, 0x190019,
    0xFF4C87, 0xFF0054, 0x59001D, 0x220013, 0xFF1500, 0x993500, 0x795100, 0x436400,
    0x033900, 0x005735, 0x00547F, 0x0000FF, 0x00454F, 0x2500CC, 0x7F7F7F, 0x202020,
    0xFF0000, 0xBDFF2D, 0xAFED06, 0x64FF09, 0x108B00, 0x00FF87, 0x00A9FF, 0x002AFF,
    0x3F00FF, 0x7A00FF, 0xB21A7D, 0x402100, 0xFF4A00, 0x88E106, 0x72FF15, 0x00FF00,
    0x3BFF26, 0x59FF71, 0x38FFCC, 0x5B8AFF, 0x3151C6, 0x877FE9, 0xD31DFF, 0xFF005D,
    0xFF7F00, 0xB9B000, 0x90FF00, 0x835D07, 0x392B00, 0x144C10, 0x0D5038, 0x15152A,
    0x16205A, 0x693C1C, 0xA8000A, 0xDE513D, 0xD86A1C, 0xFFE126, 0x9EE12F, 0x67B50F,
    0x1E1E30, 0xDCFF6B, 0x80FFBD, 0x9A99FF, 0x8E66FF, 0x404040, 0x757575, 0xE0FFFF,
    0xA00000, 0x350000, 0x1AD000, 0x074200, 0xB9B000, 0x3F3100, 0xB35F00, 0x4B1502,
]


def _rgb_of(v):
    return ((v >> 16) & 255) / 255.0, ((v >> 8) & 255) / 255.0, (v & 255) / 255.0


def palette_rgb(name):
    """A palette's representative colour: its most saturated stop, weighted a
    little toward the bright end (never the near-black paper the ramps start
    on, never the near-white they finish on)."""
    try:
        import numpy as _np
        t = _np.linspace(0.25, 0.97, 25)
        rgb = palette.colorize(t, name)
        chroma = rgb.max(axis=1) - rgb.min(axis=1)
        score = chroma * (0.6 + 0.4 * rgb.max(axis=1))
        best = rgb[int(_np.argmax(score))]
        return float(best[0]), float(best[1]), float(best[2])
    except Exception:
        return (1.0, 1.0, 1.0)


def nearest_led(rgb):
    """The APC velocity whose LED colour is closest in hue and saturation to
    ``rgb`` (0..1 floats), among the colours bright enough to read on stage.
    Compares normalised colour directions so a dark palette stop still finds
    its hue rather than a grey."""
    r, g, b = rgb
    m = max(r, g, b, 1e-6)
    want = (r / m, g / m, b / m)
    best, best_d = DEFAULT_COLOR, 1e9
    for v, hexv in enumerate(APC_RGB):
        lr, lg, lb = _rgb_of(hexv)
        lm = max(lr, lg, lb)
        if lm < 0.6:
            continue                              # too dim to be a pad colour
        if lm - min(lr, lg, lb) < 0.35:
            continue                              # greys and whites: no hue
        have = (lr / lm, lg / lm, lb / lm)
        # hue/saturation distance, plus a nudge toward the brighter LED when
        # two entries share a hue (the table has dim twins of most colours)
        d = sum((a - c) ** 2 for a, c in zip(want, have)) + 0.15 * (1.0 - lm)
        if d < best_d:
            best, best_d = v, d
    return best


DEFAULT_COLOR = 3          # white
PAL_COLOR = {name: nearest_led(palette_rgb(name)) for name in _PALETTES}
COL_WHITE, COL_RED, COL_YELLOW, COL_GREEN, COL_CYAN, COL_ICE, COL_PURPLE = 3, 5, 13, 21, 37, 41, 49
COL_ACTION, COL_ACTION_ON = COL_WHITE, COL_YELLOW
COL_FX_OFF, COL_FX_ON = COL_PURPLE, COL_GREEN
COL_TOOL, COL_TOOL_ON = COL_CYAN, COL_RED
COL_SCENE_EMPTY = 0
ROUND_OFF, ROUND_ON, ROUND_BLINK = 0, 1, 2

TITLE_TAP_SECONDS = 4.0    # a tapped TITLE stays this long; held stays while held
TITLE_TAP_MAX = 0.35       # a press shorter than this counts as a tap

# Grid animations fired by button presses: name -> (kind, colour or None for
# the live palette colour, seconds). Kinds: ripple (from the pressed pad),
# flash (whole grid, fading), wipe (left->right), wipeback (right->left),
# curtain (top->bottom), sparkle (random twinkle), blackout (grid goes dark
# then returns).
ANIMATIONS = {
    "punch": ("ripple", COL_WHITE, 0.45), "bigpunch": ("flash", COL_WHITE, 0.55),
    "refire": ("wipe", None, 0.4), "reset": ("wipeback", None, 0.4),
    "pulse": ("ripple", COL_YELLOW, 0.3), "freeze": ("flash", COL_ICE, 0.5),
    "title": ("curtain", COL_WHITE, 0.6), "blackout": ("blackout", 0, 0.6),
    "variant": ("ripple", COL_CYAN, 0.4), "palette": ("sparkle", None, 0.45),
    "clearfx": ("wipe", COL_PURPLE, 0.35), "scene": ("ripple", None, 0.5),
    "sceneprev": ("wipeback", None, 0.35), "scenenext": ("wipe", None, 0.35),
    "cut": ("flash", None, 0.4), "sync": ("flash", COL_RED, 0.2),
}
FX_REGION = (4, 7, 4, 7)          # cols 4-7, rows 4-7
GRID_REGION = (0, 7, 0, 7)


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


def _chan(o, name, default=0.0):
    """A CHOP channel's current value (TD Channel or a plain number)."""
    try:
        ch = o[name]
    except Exception:
        return default
    try:
        return float(ch.eval())
    except Exception:
        try:
            return float(ch)
        except Exception:
            return default


def _now():
    try:
        return float(absTime.seconds)  # noqa: F821 (TD global)
    except Exception:
        return 0.0


def _hash(a, b):
    """A cheap deterministic 0..1 for sparkles."""
    x = math.sin(a * 12.9898 + b * 78.233) * 43758.5453
    return x - math.floor(x)


# ---------------------------------------------------------------------------
# Resolving the show + its scenes
# ---------------------------------------------------------------------------
_STATE = {}   # per-surface: shift held, tap times, press times, held actions, anims


def _st(apc):
    try:
        key = apc.path
    except Exception:
        key = id(apc)
    return _STATE.setdefault(key, {"shift": False, "taps": [], "pressed": {}, "held": {},
                                   "anims": [], "base": None})


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


def _scene_color(t, idx):
    p = _scene_palette(t, idx)
    return PAL_COLOR.get(_PALETTES[p], DEFAULT_COLOR) if 0 <= p < N_PAL else DEFAULT_COLOR


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
        if action == "bigpunch":
            _pulse(t, "Chaosburst")      # the frame comes apart with the hit
        if _has(sim, "Punch"):
            if action == "bigpunch":
                _setf(sim, "Punchstrength", 3.5)
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
        if _has(t, "Reactive"):
            _toggle(t, "Reactive")          # the show's audio-reactivity switch
        else:
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


def _pad_of(kind, val):
    """The grid note showing a decoded meaning (None if it has no pad)."""
    for note in range(64):
        if _decode(note) == (kind, val):
            return note
    return None


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
        return _is_muted(t)
    if name == "freerun":
        return _geti(t, "Freerunall", 0)
    return 0


def _is_muted(t):
    """Audio reactivity off: the show's Reactive toggle is down, or the
    Reactor is muted."""
    if _has(t, "Reactive") and not _geti(t, "Reactive", 1):
        return 1
    return _geti(_analyze(t), "Mute", 0)


def _base_frame(apc, t, st):
    """The resting LED state of every pad and button: {note: (vel, channel)}."""
    frame = {}
    live = _live_index(t)
    nxt = _menu_index(t, "Nextscene", -1)
    cf = _getf(t, "Crossfade", 0.0)
    live_pal = _scene_palette(t, live)
    shift = st.get("shift", False)

    for note in range(64):
        kind = _decode(note)
        if kind is None:
            frame[note] = (COL_SCENE_EMPTY, CH_BRIGHT)
            continue
        what, val = kind
        if what == "scene":
            col = _scene_color(t, val)
            if val == live:
                frame[note] = (col, CH_BRIGHT)
            elif val == nxt:
                frame[note] = (col, CH_BLINK)
            else:
                frame[note] = (col, CH_DIM)
        elif what == "palette":
            col = PAL_COLOR.get(_PALETTES[val], DEFAULT_COLOR)
            frame[note] = (col, CH_BRIGHT if val == live_pal else CH_DIM)
        elif what == "action":
            on = _action_state(t, val, st)
            frame[note] = (COL_ACTION_ON if on else COL_ACTION, CH_BRIGHT if on else CH_DIM)
        elif what == "fx":
            on = _geti(t, val, 0)
            frame[note] = (COL_FX_ON if on else COL_FX_OFF, CH_BRIGHT if on else CH_DIM)
        elif what == "tool":
            on = _action_state(t, val, st)
            frame[note] = (COL_TOOL_ON if on else COL_TOOL, CH_BRIGHT if on else CH_DIM)

    # Right column: scene select. Lit = live, blink = armed; SHIFT shows 8+k.
    for k, note in enumerate(SCENE_BTN):
        idx = k + 8 if shift else k
        if idx >= N_SCENES:
            state = ROUND_OFF
        elif idx == live:
            state = ROUND_ON
        elif idx == nxt:
            state = ROUND_BLINK
        else:
            state = ROUND_OFF
        frame[note] = (state, CH_BRIGHT)

    # Bottom row: lit when the action's state is on; Cut lit mid-fade.
    names = SHIFT_TRACK_ACTIONS if shift else TRACK_ACTIONS
    for note, name in zip(TRACK_BTN, names):
        if name == "cut":
            state = ROUND_ON if cf > 0 else ROUND_OFF
        elif name in ("title", "freeze", "blackout", "strobe", "hold", "freerun"):
            state = ROUND_ON if _action_state(t, name, st) else ROUND_OFF
        elif name == "ledreset":
            state = ROUND_BLINK
        else:
            state = ROUND_ON
        frame[note] = (state, CH_BRIGHT)
    return frame


def _reactive(t, frame, st):
    """The audio-reactive layer: the live scene pad breathes with the level and
    jumps on each kick, the PULSE pad flashes on detected kicks, the TAP pad
    ticks with the tempo engine's phase, FX pads that are on breathe with the
    beat, and STROBE strobes the grid while held. Returns the layered frame."""
    a = _analyze(t)
    beat = _chan(a, "beat")
    level = _chan(a, "level")
    muted = _is_muted(t)
    phase = _chan(_tempo(t), "beat")
    out = dict(frame)

    live_pad = _pad_of("scene", _live_index(t))
    if live_pad is not None and not muted:
        vel, _ = frame[live_pad]
        step = min(len(CH_LEVELS) - 1, int(max(level, beat) * len(CH_LEVELS)))
        out[live_pad] = (vel, CH_LEVELS[step] if beat < 0.5 else CH_BRIGHT)

    pulse_pad = _pad_of("action", "pulse")
    if pulse_pad is not None:
        out[pulse_pad] = (COL_YELLOW, CH_BRIGHT) if (beat > 0.3 and not muted) else (COL_ACTION, CH_DIM)

    tap_pad = _pad_of("tool", "tap")
    if tap_pad is not None:
        out[tap_pad] = (COL_RED, CH_BRIGHT) if phase < 0.12 else (COL_TOOL, CH_DIM)

    if not muted:
        for name in FX_NAMES:
            if _geti(t, name, 0):
                pad = _pad_of("fx", name)
                step = min(len(CH_LEVELS) - 1, 2 + int(beat * 3.99))
                out[pad] = (COL_FX_ON, CH_LEVELS[step])

    if _geti(t, "Strobe", 0):
        on = int(_now() * 9.0) % 2 == 0
        for note in range(64):
            vel, ch = out[note]
            out[note] = (vel, CH_BRIGHT) if on else (0, CH_BRIGHT)
    return out


def _animate(st, name, origin=None, color=None):
    """Queue the grid animation for ``name`` (if it has one)."""
    spec = ANIMATIONS.get(name)
    if spec is None:
        return
    kind, col, dur = spec
    if color is not None and col is None:
        col = color
    if col is None:
        col = COL_WHITE
    region = FX_REGION if name == "clearfx" else GRID_REGION
    st.setdefault("anims", []).append({
        "kind": kind, "t0": _now(), "dur": dur, "color": col,
        "origin": origin if origin is not None else (3.5, 3.5), "region": region,
    })


def _anim_pixel(anim, col, row, p):
    """What an animation shows on (col, row) at progress p (0..1), or None."""
    c0, c1, r0, r1 = anim["region"]
    if not (c0 <= col <= c1 and r0 <= row <= r1):
        return None
    kind, color = anim["kind"], anim["color"]
    w = c1 - c0 + 1
    if kind == "ripple":
        oc, orow = anim["origin"]
        d = math.hypot(col - oc, row - orow)
        r = p * 11.0
        if abs(d - r) < 1.0:
            return (color, CH_BRIGHT)
        if 0 < r - d < 2.0:
            return (color, CH_DIM)
        return None
    if kind == "flash":
        return (color, CH_LEVELS[max(0, min(5, int((1.0 - p) * 6)))])
    if kind == "blackout":
        return (0, CH_BRIGHT) if p < 0.7 else None
    if kind in ("wipe", "wipeback"):
        lead = int(p * w)
        c = c0 + (lead if kind == "wipe" else w - 1 - lead)
        if col == c:
            return (color, CH_BRIGHT)
        trail = c - 1 if kind == "wipe" else c + 1
        if col == trail:
            return (color, CH_MID)
        return None
    if kind == "curtain":
        h = r1 - r0 + 1
        lead = r1 - int(p * h)
        if row == lead:
            return (color, CH_BRIGHT)
        if row > lead:
            return (color, CH_DIM)
        return None
    if kind == "sparkle":
        if _hash(col * 8 + row, int(p * 14)) < 0.25:
            return (color, CH_BRIGHT)
        return None
    return None


def _overlay_anims(st, frame):
    """Apply the running animations on top of ``frame``; drop finished ones."""
    anims = st.get("anims", [])
    if not anims:
        return frame
    now = _now()
    keep, out = [], dict(frame)
    for anim in anims:
        p = (now - anim["t0"]) / anim["dur"] if anim["dur"] > 0 else 1.0
        if p >= 1.0:
            continue
        if p < 0:
            p = 0.0
        keep.append(anim)
        for note in range(64):
            px = _anim_pixel(anim, note % 8, note // 8, p)
            if px is not None:
                out[note] = px
    st["anims"] = keep
    return out


def _flush(apc, frame):
    for note, (vel, ch) in frame.items():
        _pad(apc, note, vel, ch)


def repaint(apc):
    """Recompute the resting state from the show and drive every LED (the
    reactive layer + animations on top, so nothing flickers back)."""
    t = _target(apc)
    if t is None:
        _blank(apc)
        return
    st = _st(apc)
    st["base"] = _base_frame(apc, t, st)
    _flush(apc, _overlay_anims(st, _reactive(t, st["base"], st)))


def tick(apc):
    """Per frame (Execute DAT): the audio-reactive layer and the animations.
    Uses the resting state cached by the last repaint, so it costs a few
    channel reads and, thanks to by-difference sending, only the pads that
    actually changed go down the wire."""
    t = _target(apc)
    if t is None:
        return
    st = _st(apc)
    if st.get("base") is None:
        st["base"] = _base_frame(apc, t, st)
    _flush(apc, _overlay_anims(st, _reactive(t, st["base"], st)))


def reset(apc):
    """The reset mechanism: blank every LED, then repaint from scratch."""
    _blank(apc)
    st = _st(apc)
    st["anims"] = []
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
        repaint(apc)                 # the round buttons show the shift layer
        return
    if is_on:
        _on_press(apc, t, st, note)
    else:
        _on_release(apc, t, st, note)


def _action_for(note, shift=False):
    """The action a bottom-row button maps to (None otherwise)."""
    if note in TRACK_BTN:
        names = SHIFT_TRACK_ACTIONS if shift else TRACK_ACTIONS
        return names[TRACK_BTN.index(note)]
    return None


def _on_press(apc, t, st, note):
    shift = st.get("shift", False)
    if t is None:
        if note == BTN_RESET:
            reset(apc)
        return
    st["pressed"][note] = _now()
    col, row = note % 8, note // 8
    kind = _decode(note)
    if kind is not None:
        what, val = kind
        if what == "scene":
            if shift:
                _set_menu(t, "Scene", val)
                _animate(st, "scene", (col, row), _scene_color(t, val))
            else:
                _set_menu(t, "Nextscene", val)
        elif what == "palette":
            holder = _palette_holder(t, _live_index(t))
            if holder is not None:
                _set_menu(holder, "Palette", val)
                _animate(st, "palette", (col, row), PAL_COLOR.get(_PALETTES[val], DEFAULT_COLOR))
        elif what == "fx":
            perform(t, "clearfx" if shift else val, apc)
            if shift:
                _animate(st, "clearfx")
        else:                                    # action / tool
            perform(t, val, apc, pressed=True)
            if val not in ("freeze", "blackout") or _action_state(t, val, st):
                _animate(st, val, (col, row), _scene_color(t, _live_index(t)))
        repaint(apc)
        return
    if note in SCENE_BTN:                        # right column: scene select
        idx = SCENE_BTN.index(note) + (8 if shift else 0)
        if idx < N_SCENES:
            _set_menu(t, "Scene", idx)
            _animate(st, "scene", (7.5, 7 - SCENE_BTN.index(note)), _scene_color(t, idx))
        repaint(apc)
        return
    action = _action_for(note, shift)
    if action is not None:
        perform(t, action, apc, pressed=True)
        if action not in ("freeze", "blackout") or _action_state(t, action, st):
            _animate(st, action, (TRACK_BTN.index(note), -0.5), _scene_color(t, _live_index(t)))
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
        # a shifted press releases the shifted action, too
        name = _action_for(note, True) if _action_for(note, True) in MOMENTARY else _action_for(note, False)
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
