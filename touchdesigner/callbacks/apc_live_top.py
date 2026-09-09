# Script TOP callback - a live view of the APC, painted over the printed map.
#
# What this draws is not a simulation of the surface: it is the LED state the
# controller actually sent, read out of apc_mini's own send cache. So the panel
# and the hardware cannot disagree -- if a pad is wrong on the desk it is wrong
# here too, which is what makes this worth looking at while playing.
#
# With no APC connected (or before one has been painted) it falls back to the
# resting state computed from the show, so the panel is still a live guide when
# the controller is not plugged in and the keyboard is driving everything.
#
# The rectangles come from docs/apc_map.json, written by tools/apc_map.py
# beside the picture this paints over, so the overlay lines up exactly.
import json
import os
import sys

_REPO = r""  # <-- set by td_build (or set the TD_PHYSICS_REPO env var)
if not _REPO:
    _REPO = os.environ.get("TD_PHYSICS_REPO", "")
if _REPO and _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import numpy as np
from touchdesigner.callbacks import apc_mini

_STATE = {}

# How each LED behaviour channel reads on screen. The mk2 uses the note channel
# to pick the behaviour: 1-7 are 10%..100% solid, 8-11 pulse, 12-16 blink.
SOLID_MAX = 7


def _state(scriptOp):
    return _STATE.setdefault(scriptOp.path, {})


def _p(o, name, default):
    par = getattr(o.par, name, None)
    try:
        return par.eval() if par is not None else default
    except Exception:
        return default


def onSetupParameters(scriptOp):
    if hasattr(scriptOp.par, "Surface"):
        return
    page = scriptOp.appendCustomPage("APC Live")
    page.appendStr("Surface", label="APC Surface")[0].val = "/APCShow"
    page.appendStr("Target", label="PhysicsVJ Target")[0].val = "/PhysicsVJ"
    o = page.appendFloat("Opacity", label="Pad Opacity")[0]
    o.val = 0.9
    scriptOp.par.Opacity.normMin, scriptOp.par.Opacity.normMax = 0.0, 1.0


def _geometry(st):
    path = os.path.join(_REPO, "docs", "apc_map.json")
    stamp = None
    try:
        stamp = os.path.getmtime(path)
    except Exception:
        return st.get("geo")
    if st.get("geo_stamp") != stamp:
        try:
            with open(path) as fh:
                st["geo"] = json.load(fh)
            st["geo_stamp"] = stamp
        except Exception:
            st["geo"] = None
    return st.get("geo")


def _led_state(scriptOp):
    """{note: (velocity, channel)} -- what the surface last told each LED."""
    surface = op(_p(scriptOp, "Surface", "/APCShow"))  # noqa: F821 (TD global)
    if surface is not None:
        cache = apc_mini._LED_STATE.get(surface.path)
        if cache:
            return dict(cache)
    # Nothing painted yet: compute the resting picture from the show itself, so
    # the panel is a live guide even with no controller in the room.
    show = op(_p(scriptOp, "Target", "/PhysicsVJ"))  # noqa: F821 (TD global)
    if show is None:
        return {}
    try:
        st = {"shift": False, "held": {}, "anims": [], "taps": [], "pressed": {}}
        return apc_mini._base_frame(surface, show, st)
    except Exception:
        return {}


def _rgb(velocity, channel, opacity):
    """The colour and strength one LED reads as on screen."""
    try:
        packed = apc_mini.APC_RGB[int(velocity) % len(apc_mini.APC_RGB)]
    except Exception:
        return None
    r, g, b = (((packed >> 16) & 255) / 255.0, ((packed >> 8) & 255) / 255.0,
               (packed & 255) / 255.0)
    ch = int(channel)
    if ch <= SOLID_MAX:
        strength = max(ch, 1) / float(SOLID_MAX)          # the dim/bright steps
    else:
        # Pulse and blink behaviours: show them breathing, on the same clock
        # the hardware uses, so a blinking pad blinks here too.
        try:
            t = float(absTime.seconds)  # noqa: F821 (TD global)
        except Exception:
            t = 0.0
        if ch <= 11:
            strength = 0.45 + 0.55 * (0.5 + 0.5 * np.cos(t * 6.0))
        else:
            strength = 1.0 if (int(t * 4.0) % 2 == 0) else 0.15
    return (r, g, b, min(max(strength * opacity, 0.0), 1.0))


def onCook(scriptOp):
    st = _state(scriptOp)
    geo = _geometry(st)
    if not geo:
        scriptOp.clear()
        return
    W, H = int(geo["width"]), int(geo["height"])
    if scriptOp.width != W or scriptOp.height != H:
        # Match the picture exactly, or the overlay lands off the pads.
        try:
            scriptOp.par.outputresolution = "custom"
            scriptOp.par.resolutionw, scriptOp.par.resolutionh = W, H
        except Exception:
            pass

    img = np.zeros((H, W, 4), dtype=np.float32)
    opacity = float(_p(scriptOp, "Opacity", 0.9))
    leds = _led_state(scriptOp)

    for note, (vel, ch) in leds.items():
        if int(vel) <= 0:
            continue                                  # an unlit pad shows the print
        key = str(int(note))
        box = geo["grid"].get(key) or geo["round"].get(key)
        if box is None:
            continue
        col = _rgb(vel, ch, opacity)
        if col is None:
            continue
        x, y, w, h = (int(round(v)) for v in box)
        # The JSON is in the drawing's coordinates (y down); a TOP is y up.
        y0 = max(0, H - (y + h))
        y1 = min(H, H - y)
        x0, x1 = max(0, x), min(W, x + w)
        if x1 <= x0 or y1 <= y0:
            continue
        img[y0:y1, x0:x1, 0] = col[0]
        img[y0:y1, x0:x1, 1] = col[1]
        img[y0:y1, x0:x1, 2] = col[2]
        img[y0:y1, x0:x1, 3] = col[3]

    scriptOp.copyNumpyArray(np.ascontiguousarray(img, dtype=np.float32))


setupParameters = onSetupParameters
cook = onCook
