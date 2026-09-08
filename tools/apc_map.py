#!/usr/bin/env python3
"""Draw the APC mini mk2 control map straight from the controller's tables.

    python tools/apc_map.py            # writes docs/apc_map.svg
    python tools/apc_map.py out.svg    # or wherever

The layout, labels and quadrant colours all come from
touchdesigner/callbacks/apc_mini.py, so the picture cannot drift from the
code. Tufte rules: the data (what each control does) is the ink; the hardware
is drawn as the faintest outline that still reads as an APC; colour is spent
only where it means something (the LED colour each quadrant actually shows).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_controller():
    """Execute apc_mini.py as the tests do (no TD globals needed for tables)."""
    path = os.path.join(ROOT, "touchdesigner", "callbacks", "apc_mini.py")
    with open(path) as fh:
        src = fh.read().replace('_REPO = r""', '_REPO = r"%s"' % ROOT, 1)
    ns = {"__name__": "apc_mini_map", "op": (lambda p: None)}
    exec(compile(src, "apc_mini.py", "exec"), ns)
    return ns


# Dark mode, like the show itself: the pads are the LED colour each quadrant
# glows, on near-black; ink is the LED colour, fills are that colour dimmed.
BG, TEXT, MUTED = "#121216", "#ecebe6", "#8f8d86"
INK = {
    "action": "#c9c9c9", "fx": "#b48ee0", "scene": "#e08a58",
    "palette": "#d9a450", "tool": "#5cc2cf", "round": "#9a9a9a", "empty": "#2a2a30",
}
FILL = {
    "action": "#26262b", "fx": "#241d33", "scene": "#2d1f17",
    "palette": "#2a2416", "tool": "#14282c", "round": "#1a1a1f", "empty": "#17171b",
}
PAL_HEX = {
    "inferno": "#f0842a", "magma": "#c9273f", "plasma": "#d64fa3", "cyber": "#1fb3c9",
    "synth": "#7a4fa8", "acid": "#3fb64a", "ice": "#5aa7e0", "sigma": "#c94a2a",
}

PAD, GAP = 72, 8
ORIGIN_X, ORIGIN_Y = 36, 80
FONT = "'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif"


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(x, y, s, size=10.5, weight=500, fill=TEXT, anchor="middle", extra=""):
    return ('<text x="%.1f" y="%.1f" font-size="%.1f" font-weight="%d" fill="%s" '
            'text-anchor="%s" font-family="%s" %s>%s</text>'
            % (x, y, size, weight, fill, anchor, FONT, extra, _esc(s)))


def _pad_svg(x, y, kind, top, bottom=None, dot=None):
    out = ['<rect x="%.1f" y="%.1f" width="%d" height="%d" rx="6" fill="%s" stroke="%s" stroke-width="1"/>'
           % (x, y, PAD, PAD, FILL[kind], INK[kind])]
    if dot:
        out.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s"/>' % (x + PAD - 11, y + 11, dot))
    cy = y + PAD / 2 + (4 if bottom is None else -2)
    out.append(_text(x + PAD / 2, cy, top, 11 if len(top) <= 9 else 9.5, 600, TEXT))
    if bottom:
        out.append(_text(x + PAD / 2, cy + 14, bottom, 8.5, 400, INK[kind]))
    return "\n".join(out)


def _round_svg(cx, cy, label, sub=None, blink=False):
    out = ['<circle cx="%.1f" cy="%.1f" r="14" fill="%s" stroke="%s" stroke-width="1"%s/>'
           % (cx, cy, FILL["round"], INK["round"], ' stroke-dasharray="3 2"' if blink else "")]
    out.append(_text(cx, cy + 32, label, 8.5, 600, TEXT))
    if sub:
        out.append(_text(cx, cy + 43, sub, 7.5, 400, INK["round"]))
    return "\n".join(out)


def render_svg(ns):
    scene_names = ns["SCENE_NAMES"]
    scene_labels = ns["SCENE_LABELS"]
    labels = ns["LABELS"]
    palettes = ns["_PALETTES"]
    decode = ns["_decode"]

    grid_w = 8 * PAD + 7 * GAP
    right_x = ORIGIN_X + grid_w + 34                 # right column of round buttons
    width = right_x + 120
    bottom_y = ORIGIN_Y + grid_w + 34                # bottom row of round buttons
    fader_y = bottom_y + 70
    height = fader_y + 150

    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
             % (width, height, width, height),
             '<rect width="100%%" height="100%%" fill="%s"/>' % BG]

    # Title line.
    parts.append(_text(ORIGIN_X, 30, "APC mini mk2  ·  PhysicsVJ control map", 15, 600, TEXT, "start"))
    parts.append(_text(ORIGIN_X, 46, "grid note = row × 8 + col, row 0 at the bottom · factory mode, MIDI channel 1",
                       9.5, 400, MUTED, "start"))

    # The 8x8 grid.
    for note in range(64):
        col, row = note % 8, note // 8
        x = ORIGIN_X + col * (PAD + GAP)
        y = ORIGIN_Y + (7 - row) * (PAD + GAP)
        kind = decode(note)
        if kind is None:
            parts.append(_pad_svg(x, y, "empty", ""))
            continue
        what, val = kind
        if what == "scene":
            name = scene_names[val]
            parts.append(_pad_svg(x, y, "scene", scene_labels.get(name, name.upper()), "scene %d" % val))
        elif what == "palette":
            parts.append(_pad_svg(x, y, "palette", palettes[val], "palette %d" % val, dot=PAL_HEX.get(palettes[val])))
        elif what == "action":
            sub = "held" if val in ns["MOMENTARY"] else None
            parts.append(_pad_svg(x, y, "action", labels[val], sub))
        elif what == "fx":
            parts.append(_pad_svg(x, y, "fx", labels[val], "toggle"))
        else:
            parts.append(_pad_svg(x, y, "tool", labels[val]))

    # Quadrant captions (in the LED colour each quadrant shows).
    mid = ORIGIN_X + grid_w / 2
    qy_top = ORIGIN_Y - 8
    parts.append(_text(ORIGIN_X + (4 * PAD + 3 * GAP) / 2, qy_top, "ACTIONS · white, yellow when on", 9, 600, INK["action"]))
    parts.append(_text(mid + (4 * PAD + 3 * GAP) / 2 + GAP / 2, qy_top, "WHOLE-SCREEN FX · purple, green when on  (shift + pad = clear all)", 9, 600, INK["fx"]))
    qy_bot = ORIGIN_Y + grid_w + 14
    parts.append(_text(ORIGIN_X + (4 * PAD + 3 * GAP) / 2, qy_bot, "SCENE QUEUE · palette colour · press = arm deck B, shift = cut", 9, 600, INK["scene"]))
    parts.append(_text(mid + (4 * PAD + 3 * GAP) / 2 + GAP / 2, qy_bot, "PALETTES · TEMPO (row 1) · LEVELS (row 0) · cyan, red when on", 9, 600, INK["tool"]))

    # Right column: scene select.
    parts.append(_text(right_x + 40, ORIGIN_Y - 8, "SCENE SELECT", 9, 600, INK["round"]))
    for k, note in enumerate(ns["SCENE_BTN"]):
        cy = ORIGIN_Y + k * (PAD + GAP) + PAD / 2
        top = scene_labels.get(scene_names[k], "") if k < len(scene_names) else "—"
        shifted = 8 + k
        sub = ("shift: " + scene_labels.get(scene_names[shifted], "")) if shifted < len(scene_names) else "shift: —"
        parts.append(_round_svg(right_x + 40, cy - 10, top, sub))

    # Bottom row: track buttons + shift.
    for k, note in enumerate(ns["TRACK_BTN"]):
        cx = ORIGIN_X + k * (PAD + GAP) + PAD / 2
        a, s = ns["TRACK_ACTIONS"][k], ns["SHIFT_TRACK_ACTIONS"][k]
        parts.append(_round_svg(cx, bottom_y, labels[a], "shift: " + labels[s]))
    parts.append(_round_svg(right_x + 40, bottom_y, "SHIFT", "hold for 2nd layer"))

    # Faders.
    fx0 = ORIGIN_X + PAD / 2
    for k in range(9):
        cx = fx0 + k * (PAD + GAP)
        parts.append('<rect x="%.1f" y="%d" width="8" height="70" rx="3" fill="#1e1e23" stroke="#6a6a70"/>' % (cx - 4, fader_y))
        parts.append('<rect x="%.1f" y="%d" width="22" height="9" rx="2" fill="#d0d0d0"/>' % (cx - 11, fader_y + 26 + (k % 3) * 9))
        if k < 3:
            lab = ["TRAIL", "ORBIT", "POINT SIZE"][k]
        elif k < 8:
            lab = "scene dial %d" % (k + 1)
        else:
            lab = "CROSSFADE A/B"
        parts.append(_text(cx, fader_y + 88, lab, 8.5, 600 if k in (0, 1, 2, 8) else 400, TEXT))
    parts.append(_text(fx0, fader_y + 104, "faders 1–3: trail / orbit / point size where the scene has them; 4–8: the live scene's physics (FADER_MAP in apc_mini.py)",
                       8.5, 400, MUTED, "start"))
    parts.append(_text(fx0, fader_y + 118, "LEDs: the live scene pad breathes with the level and jumps on kicks · PULSE flashes on detected kicks · TAP ticks with the tempo · FX pads breathe when on",
                       8.5, 400, MUTED, "start"))
    parts.append(_text(fx0, fader_y + 132, "Press animations: punch ripples, big punch flashes, re-fire / reset wipe, title curtains down, palette sparkles, scene cuts ripple in the new palette colour",
                       8.5, 400, MUTED, "start"))
    parts.append("</svg>")
    return "\n".join(parts)


def main(argv):
    out = argv[1] if len(argv) > 1 else os.path.join(ROOT, "docs", "apc_map.svg")
    svg = render_svg(load_controller())
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as fh:
        fh.write(svg)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv)
