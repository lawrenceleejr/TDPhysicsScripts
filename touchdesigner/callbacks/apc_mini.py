# Akai APC mini mk2 -> PhysicsVJ show controller.
#
# This is the brain of the APC show surface. It is loaded as the callbacks of
# an anchor Script CHOP (for onSetupParameters/onCook), and its plain functions
# are also called by two sibling DATs that td_build wires up:
#   * a MIDI In DAT  -> on_midi(apc, message, channel, index, value)
#   * a Parameter Execute DAT -> repaint(apc) on show changes, reset(apc) on Reset
#
# All logic lives here so the surface is one file you can read and re-map.
#
# ---------------------------------------------------------------------------
# CONTROL MAP (APC mini mk2, factory/Generic mode, MIDI channel 1)
# ---------------------------------------------------------------------------
#   8x8 RGB GRID (notes 0..63, note = row*8 + col, row 0 = bottom)
#       columns 0..5 = the six scenes; rows 0..6 = the seven palettes.
#       Press pad (col,row): instant-cut to that scene on deck A AND set that
#       scene's palette. Each pad glows in its palette's signature colour; the
#       live scene's column is bright and its active-palette pad pulses.
#   TRACK BUTTONS (round, below grid, notes 100..107)
#       100..105 = arm scene 0..5 onto deck B (Nextscene) -- armed one blinks.
#       106 = Cut (commit the crossfade B->A); lit while a fade is in progress.
#       107 = Freerun All toggle; lit while on.
#   SCENE BUTTONS (round, right column, notes 112..119)
#       112 = Reset the controller (re-handshake + repaint every LED).
#       113 = Re-fire the live scene (new collision / next event / reset).
#   FADERS (CC 48..56, channel 1)
#       fader 9 / master (CC56) = Crossfade (A/B blend).
#       fader 1 (CC48) = live scene Trail   fader 2 (CC49) = live scene Orbit
#       fader 3 (CC50) = live scene Point Size.  Faders 4..8 are free.
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
    _PALETTES = ["inferno", "magma", "plasma", "cyber", "synth", "acid", "ice"]

# Scene COMP names in build_all() order, and each scene's "re-fire" pulse.
SCENE_NAMES = ["ising", "nbody", "flow", "softbody", "lhc", "opendata",
               "rd", "sdf", "pops", "hydrogen"]
REFIRE_PULSE = ["Reset", "Reset", "Reset", "Reset", "Newevent", "Nextevent",
                "Reseed", "Reseed", "Reset", "Reset"]
N_SCENES = len(SCENE_NAMES)
N_PAL = len(_PALETTES)
N_GRID_COLS = 8  # the APC grid is 8 wide; scene 8 lives on a scene button

# --- APC mini mk2 hardware map -------------------------------------------
TRACK_BTN = [100, 101, 102, 103, 104, 105, 106, 107]  # bottom round buttons
SCENE_BTN = [112, 113, 114, 115, 116, 117, 118, 119]  # right column buttons
SHIFT = 122
FADER_CC = [48, 49, 50, 51, 52, 53, 54, 55, 56]       # 9 faders; [8] = master
BTN_CUT = TRACK_BTN[6]
BTN_FREERUN = TRACK_BTN[7]
BTN_RESET = SCENE_BTN[0]
BTN_REFIRE = SCENE_BTN[1]

# sendMIDI('note', channel, note, velocity): the channel selects the LED
# behaviour (mk2: ch1..7 = 10%..100% solid, 8..11 = pulse, 12..16 = blink).
CH_DIM, CH_MID, CH_BRIGHT, CH_PULSE, CH_BLINK = 2, 4, 7, 10, 14

# APC 128-colour palette indices that read closest to each physics palette.
PAL_COLOR = {
    "inferno": 9, "magma": 5, "plasma": 53, "cyber": 37,
    "synth": 49, "acid": 21, "ice": 41,
}
DEFAULT_COLOR = 3  # white
ROUND_OFF, ROUND_ON, ROUND_BLINK = 0, 1, 2


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


# ---------------------------------------------------------------------------
# Resolving the show + its scenes
# ---------------------------------------------------------------------------
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
    c = _scene_comp(t, idx)
    try:
        return c.op("sim") if c is not None else None
    except Exception:
        return None


def _live_index(t):
    return _menu_index(t, "Scene", 0)


def _palette_holder(t, idx):
    """The op carrying a scene's Palette menu: the 'sim' (numpy scenes) or the
    scene COMP itself (GLSL scenes like rd/sdf)."""
    sim = _scene_sim(t, idx)
    if sim is not None and hasattr(sim.par, "Palette"):
        return sim
    return _scene_comp(t, idx)


def _scene_palette(t, idx):
    holder = _palette_holder(t, idx)
    return _menu_index(holder, "Palette", -1) if holder is not None else -1


# ---------------------------------------------------------------------------
# LED output
# ---------------------------------------------------------------------------
def _ledout(apc):
    try:
        return apc.op("ledout")
    except Exception:
        return None


def _pad(apc, note, velocity, channel=CH_BRIGHT):
    o = _ledout(apc)
    if o is None:
        return
    try:
        o.sendMIDI("note", channel, int(note), int(velocity))
    except Exception:
        pass


def _round(apc, note, state):
    """Light a single-colour round button: 0 off, 1 on, 2 blink."""
    _pad(apc, note, int(state), CH_BRIGHT)


def _grid_note(col, row):
    return row * 8 + col


def _blank(apc):
    """Turn every LED off -- the first half of a clean resync."""
    for n in range(64):
        _pad(apc, n, 0, CH_BRIGHT)
    for n in TRACK_BTN + SCENE_BTN:
        _round(apc, n, ROUND_OFF)


# ---------------------------------------------------------------------------
# Public: repaint / reset
# ---------------------------------------------------------------------------
def repaint(apc):
    """Drive every LED to reflect the current show state."""
    t = _target(apc)
    if t is None:
        _blank(apc)
        return
    live = _live_index(t)
    nxt = _menu_index(t, "Nextscene", -1)
    cf = _getf(t, "Crossfade", 0.0)
    freerun = _geti(t, "Freerunall", 0)

    # Grid: scenes (cols) x palettes (rows). The grid is 8 wide; scene 8+ live
    # on scene buttons (see below).
    grid_scenes = min(N_SCENES, N_GRID_COLS)
    for col in range(8):
        is_live = (col == live)
        cur_pal = _scene_palette(t, col) if col < grid_scenes else -1
        for row in range(8):
            note = _grid_note(col, row)
            if col < grid_scenes and row < N_PAL:
                vel = PAL_COLOR.get(_PALETTES[row], DEFAULT_COLOR)
                if is_live and row == cur_pal:
                    _pad(apc, note, vel, CH_PULSE)
                elif is_live:
                    _pad(apc, note, vel, CH_BRIGHT)
                else:
                    _pad(apc, note, vel, CH_DIM)
            else:
                _pad(apc, note, 0, CH_BRIGHT)

    # Track buttons: deck B arming + transport.
    for col in range(6):
        _round(apc, TRACK_BTN[col], ROUND_BLINK if col == nxt else ROUND_OFF)
    _round(apc, BTN_CUT, ROUND_ON if cf > 0 else ROUND_OFF)
    _round(apc, BTN_FREERUN, ROUND_ON if freerun else ROUND_OFF)

    # Scene buttons: utility indicators (so you can always find them).
    for n in SCENE_BTN:
        _round(apc, n, ROUND_OFF)
    _round(apc, BTN_RESET, ROUND_ON)
    _round(apc, BTN_REFIRE, ROUND_ON)
    # Any scenes past the 8-wide grid get a dedicated launch button.
    for extra in range(N_GRID_COLS, N_SCENES):
        btn = SCENE_BTN[2 + (extra - N_GRID_COLS)]
        _round(apc, btn, ROUND_BLINK if live == extra else ROUND_ON)


def reset(apc):
    """The reset mechanism: blank every LED, then repaint from scratch.

    Use this when the APC powers on dark, is hot-plugged, or its LEDs drift out
    of sync with the show. It re-establishes the full lighting state in one go.
    """
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
    if "control" in msg:
        _on_fader(apc, t, int(index), int(value))
        return
    if "note on" in msg and int(value) > 0:
        _on_note(apc, t, int(index))


def _on_note(apc, t, note):
    if t is None:
        return
    grid_scenes = min(N_SCENES, N_GRID_COLS)
    if 0 <= note <= 63:
        col, row = note % 8, note // 8
        if col < grid_scenes and row < N_PAL:
            _set_menu(t, "Scene", col)              # instant cut to deck A
            holder = _palette_holder(t, col)
            if holder is not None:
                _set_menu(holder, "Palette", row)   # and pick the palette
        repaint(apc)
        return
    if note in TRACK_BTN:
        i = TRACK_BTN.index(note)
        if i < grid_scenes:
            _set_menu(t, "Nextscene", i)            # arm deck B
        elif note == BTN_CUT:
            _pulse(t, "Cut")                        # commit the crossfade
        elif note == BTN_FREERUN:
            _toggle(t, "Freerunall")
        repaint(apc)
        return
    if note in SCENE_BTN:
        if note == BTN_RESET:
            reset(apc)
            return
        if note == BTN_REFIRE:
            _refire(t)
        else:
            # Launch buttons for any scenes past the 8-wide grid.
            i = SCENE_BTN.index(note)
            extra = N_GRID_COLS + (i - 2)
            if 2 <= i and N_GRID_COLS <= extra < N_SCENES:
                _set_menu(t, "Scene", extra)
        repaint(apc)


def _on_fader(apc, t, cc, value):
    v = max(0.0, min(1.0, value / 127.0))
    if cc == FADER_CC[8]:                           # master -> crossfade
        if t is not None:
            _setf(t, "Crossfade", v)
        repaint(apc)
        return
    if t is None:
        return
    live = _live_index(t)
    comp = _scene_comp(t, live)
    sim = _scene_sim(t, live)
    if cc == FADER_CC[0] and comp is not None:
        _setf(comp, "Trail", v * 0.99)
    elif cc == FADER_CC[1] and comp is not None:
        _setf(comp, "Orbit", (v * 2.0 - 1.0) * 45.0)
    elif cc == FADER_CC[2] and sim is not None:
        _setf(sim, "Pointsize", v * 0.2)


def _refire(t):
    """Re-trigger the live scene's signature event (re-collide / next event /
    reseed). The pulse lives on the 'sim' (numpy scenes) or the COMP (GLSL)."""
    idx = _live_index(t)
    if not (0 <= idx < N_SCENES):
        return
    name = REFIRE_PULSE[idx]
    sim = _scene_sim(t, idx)
    if sim is not None and hasattr(sim.par, name):
        _pulse(sim, name)
    else:
        _pulse(_scene_comp(t, idx), name)


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
