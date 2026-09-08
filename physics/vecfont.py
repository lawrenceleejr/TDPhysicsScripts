"""A stroke font: characters as polylines, for HUD text drawn as geometry.

Text in a 3D scene is usually a texture on a quad. Here the labels are lines,
like the tracks they annotate: they render through the same camera, bloom
through the same glow pass, and never resample or go soft when the camera
pushes in. That is what makes an instrument readout look drawn rather than
pasted on.

Each glyph is a list of polylines over the unit box: x in [0, ADVANCE - GAP],
y in [0, 1], baseline at y = 0, cap height at y = 1. ``strokes(text)`` lays a
string out along +x and returns the polylines in text-space units, where one
unit is the cap height.
"""
from __future__ import annotations

import numpy as np

ADVANCE = 0.62      # pen advance per character, in cap heights
GAP = 0.14          # of that, the space between glyphs
W = ADVANCE - GAP   # glyph box width

# Handy fractions of the glyph box.
_L, _R = 0.0, W
_M = W / 2.0
_T, _B = 1.0, 0.0
_H = 0.5            # x-bar height
_Q = 0.72           # upper joint (as on R, P, B)

GLYPHS: dict[str, list[list[tuple[float, float]]]] = {
    " ": [],
    "0": [[(_L, 0.16), (_L, 0.84), (_M, _T), (_R, 0.84), (_R, 0.16), (_M, _B), (_L, 0.16)],
          [(_L, 0.16), (_R, 0.84)]],
    "1": [[(_L + 0.06, 0.78), (_M, _T), (_M, _B)], [(_L, _B), (_R, _B)]],
    "2": [[(_L, 0.82), (_M, _T), (_R, 0.8), (_R, 0.62), (_L, _B), (_R, _B)]],
    "3": [[(_L, _T), (_R, _T), (_M, 0.56), (_R, 0.42), (_R, 0.14), (_M, _B), (_L, 0.08)],
          [(_M, 0.56), (_R, 0.56)]],
    "4": [[(_R, _B), (_R, _T), (_L, 0.34), (_R, 0.34)]],
    "5": [[(_R, _T), (_L, _T), (_L, 0.58), (_M, 0.62), (_R, 0.46), (_R, 0.16), (_M, _B), (_L, 0.06)]],
    "6": [[(_R, 0.86), (_M, _T), (_L, 0.78), (_L, 0.18), (_M, _B), (_R, 0.16), (_R, 0.4),
           (_M, 0.5), (_L, 0.4)]],
    "7": [[(_L, _T), (_R, _T), (_M - 0.04, _B)]],
    "8": [[(_M, 0.54), (_L, 0.66), (_L, 0.86), (_M, _T), (_R, 0.86), (_R, 0.66), (_M, 0.54),
           (_L, 0.38), (_L, 0.14), (_M, _B), (_R, 0.14), (_R, 0.38), (_M, 0.54)]],
    "9": [[(_L, 0.14), (_M, _B), (_R, 0.22), (_R, 0.82), (_M, _T), (_L, 0.82), (_L, 0.6),
           (_M, 0.5), (_R, 0.6)]],
    "A": [[(_L, _B), (_M, _T), (_R, _B)], [(_L + 0.08, 0.3), (_R - 0.08, 0.3)]],
    "B": [[(_L, _B), (_L, _T), (_R - 0.06, 0.86), (_M, _H), (_L, _H)],
          [(_M, _H), (_R, 0.3), (_M, _B), (_L, _B)]],
    "C": [[(_R, 0.84), (_M, _T), (_L, 0.78), (_L, 0.22), (_M, _B), (_R, 0.16)]],
    "D": [[(_L, _B), (_L, _T), (_M, _T), (_R, 0.76), (_R, 0.24), (_M, _B), (_L, _B)]],
    "E": [[(_R, _T), (_L, _T), (_L, _B), (_R, _B)], [(_L, _H), (_R - 0.06, _H)]],
    "F": [[(_R, _T), (_L, _T), (_L, _B)], [(_L, _H), (_R - 0.08, _H)]],
    "G": [[(_R, 0.84), (_M, _T), (_L, 0.78), (_L, 0.22), (_M, _B), (_R, 0.2), (_R, 0.44),
           (_M, 0.44)]],
    "H": [[(_L, _T), (_L, _B)], [(_R, _T), (_R, _B)], [(_L, _H), (_R, _H)]],
    "I": [[(_M, _T), (_M, _B)], [(_L + 0.06, _T), (_R - 0.06, _T)],
          [(_L + 0.06, _B), (_R - 0.06, _B)]],
    "J": [[(_R, _T), (_R, 0.18), (_M, _B), (_L, 0.14)]],
    "K": [[(_L, _T), (_L, _B)], [(_R, _T), (_L, 0.44)], [(_L + 0.1, 0.52), (_R, _B)]],
    "L": [[(_L, _T), (_L, _B), (_R, _B)]],
    "M": [[(_L, _B), (_L, _T), (_M, 0.56), (_R, _T), (_R, _B)]],
    "N": [[(_L, _B), (_L, _T), (_R, _B), (_R, _T)]],
    "O": [[(_L, 0.2), (_M, _B), (_R, 0.2), (_R, 0.8), (_M, _T), (_L, 0.8), (_L, 0.2)]],
    "P": [[(_L, _B), (_L, _T), (_R, 0.86), (_R, _Q), (_M - 0.02, 0.56), (_L, 0.56)]],
    "Q": [[(_L, 0.2), (_M, _B), (_R, 0.2), (_R, 0.8), (_M, _T), (_L, 0.8), (_L, 0.2)],
          [(_M, 0.24), (_R, _B)]],
    "R": [[(_L, _B), (_L, _T), (_R, 0.86), (_R, _Q), (_M - 0.02, 0.56), (_L, 0.56)],
          [(_M - 0.02, 0.56), (_R, _B)]],
    "S": [[(_R, 0.86), (_M, _T), (_L, 0.84), (_L, 0.62), (_R, 0.42), (_R, 0.16),
           (_M, _B), (_L, 0.1)]],
    "T": [[(_L, _T), (_R, _T)], [(_M, _T), (_M, _B)]],
    "U": [[(_L, _T), (_L, 0.18), (_M, _B), (_R, 0.18), (_R, _T)]],
    "V": [[(_L, _T), (_M, _B), (_R, _T)]],
    "W": [[(_L, _T), (_L + 0.08, _B), (_M, 0.5), (_R - 0.08, _B), (_R, _T)]],
    "X": [[(_L, _T), (_R, _B)], [(_R, _T), (_L, _B)]],
    "Y": [[(_L, _T), (_M, 0.5), (_R, _T)], [(_M, 0.5), (_M, _B)]],
    "Z": [[(_L, _T), (_R, _T), (_L, _B), (_R, _B)]],
    ".": [[(_M - 0.03, _B), (_M + 0.03, _B)]],
    ",": [[(_M + 0.02, 0.08), (_M - 0.04, -0.08)]],
    "-": [[(_L, _H), (_R, _H)]],
    "+": [[(_L, _H), (_R, _H)], [(_M, _H - W / 2), (_M, _H + W / 2)]],
    "/": [[(_L, _B), (_R, _T)]],
    ":": [[(_M - 0.03, 0.28), (_M + 0.03, 0.28)], [(_M - 0.03, 0.68), (_M + 0.03, 0.68)]],
    "=": [[(_L, 0.36), (_R, 0.36)], [(_L, 0.64), (_R, 0.64)]],
    "[": [[(_R, _T), (_L + 0.04, _T), (_L + 0.04, _B), (_R, _B)]],
    "]": [[(_L, _T), (_R - 0.04, _T), (_R - 0.04, _B), (_L, _B)]],
    "<": [[(_R, _T), (_L, _H), (_R, _B)]],
    ">": [[(_L, _T), (_R, _H), (_L, _B)]],
    "*": [[(_M, _H - 0.16), (_M, _H + 0.16)], [(_L + 0.04, _H - 0.1), (_R - 0.04, _H + 0.1)],
          [(_L + 0.04, _H + 0.1), (_R - 0.04, _H - 0.1)]],
    "%": [[(_L, _B), (_R, _T)], [(_L, 0.82), (_L + 0.1, _T)], [(_R - 0.1, 0.18), (_R, _B)]],
}


def text_width(text: str) -> float:
    """The advance width of ``text``, in cap heights."""
    return len(text) * ADVANCE


def strokes(text: str, height: float = 1.0, origin=(0.0, 0.0)) -> list[np.ndarray]:
    """``text`` as a list of (k, 2) float32 polylines.

    Unknown characters are skipped (they advance the pen, so columns of
    readout text stay aligned whatever is in them).
    """
    out: list[np.ndarray] = []
    ox, oy = float(origin[0]), float(origin[1])
    for i, ch in enumerate(text.upper()):
        glyph = GLYPHS.get(ch)
        if not glyph:
            continue
        dx = ox + i * ADVANCE * height
        for poly in glyph:
            pts = np.asarray(poly, dtype=np.float32) * height
            pts[:, 0] += dx
            pts[:, 1] += oy
            out.append(pts)
    return out
