"""Neon / dark-friendly color palettes for the physics visuals.

Pure-numpy colormaps designed to glow on a black background (low values
start near-black, high values push into saturated neon / white). Used both
by the simulation cores (to colour particles/tracks) and by the Script TOP
side (to colourise scalar fields such as the Ising lattice).

No matplotlib dependency so this runs unchanged inside TouchDesigner's
bundled Python.
"""

from __future__ import annotations

import numpy as np

# Each palette is a list of (position_in_0_1, (r, g, b)) control points.
# They are intentionally dark at the low end for nice glow-on-black behaviour.
PALETTES: dict[str, list[tuple[float, tuple[float, float, float]]]] = {
    # Classic matplotlib-ish "hot plasma" ramps, hand-tuned.
    "inferno": [
        (0.00, (0.001, 0.000, 0.014)),
        (0.25, (0.282, 0.067, 0.380)),
        (0.50, (0.733, 0.216, 0.330)),
        (0.75, (0.984, 0.553, 0.039)),
        (1.00, (0.988, 0.998, 0.645)),
    ],
    "magma": [
        (0.00, (0.001, 0.000, 0.016)),
        (0.25, (0.231, 0.060, 0.439)),
        (0.50, (0.549, 0.161, 0.506)),
        (0.75, (0.937, 0.408, 0.396)),
        (1.00, (0.988, 0.992, 0.749)),
    ],
    "plasma": [
        (0.00, (0.050, 0.030, 0.200)),
        (0.30, (0.420, 0.000, 0.660)),
        (0.60, (0.850, 0.200, 0.500)),
        (0.80, (0.980, 0.550, 0.250)),
        (1.00, (0.950, 0.990, 0.130)),
    ],
    # Synthwave / cyberpunk gradients.
    "cyber": [  # black -> electric blue -> cyan -> white
        (0.00, (0.000, 0.000, 0.000)),
        (0.40, (0.000, 0.090, 0.300)),
        (0.65, (0.000, 0.560, 0.900)),
        (0.85, (0.200, 1.000, 1.000)),
        (1.00, (0.900, 1.000, 1.000)),
    ],
    "synth": [  # black -> deep purple -> magenta -> hot pink -> white
        (0.00, (0.020, 0.000, 0.050)),
        (0.30, (0.300, 0.000, 0.500)),
        (0.60, (0.900, 0.000, 0.700)),
        (0.85, (1.000, 0.200, 0.900)),
        (1.00, (1.000, 0.900, 1.000)),
    ],
    "acid": [  # black -> deep green -> lime -> pale yellow
        (0.00, (0.000, 0.020, 0.000)),
        (0.40, (0.000, 0.300, 0.100)),
        (0.70, (0.400, 1.000, 0.000)),
        (1.00, (0.900, 1.000, 0.600)),
    ],
    "ice": [  # black -> deep blue -> sky -> white
        (0.00, (0.000, 0.000, 0.020)),
        (0.50, (0.000, 0.200, 0.500)),
        (0.80, (0.300, 0.700, 1.000)),
        (1.00, (0.950, 0.980, 1.000)),
    ],
}

DEFAULT_PALETTE = "inferno"

# Stable ordering used by menu-style parameters in TouchDesigner.
PALETTE_NAMES = ["inferno", "magma", "plasma", "cyber", "synth", "acid", "ice"]


def _control_points(name: str):
    pts = PALETTES.get(name, PALETTES[DEFAULT_PALETTE])
    xs = np.array([p[0] for p in pts], dtype=np.float32)
    cols = np.array([p[1] for p in pts], dtype=np.float32)  # (k, 3)
    return xs, cols


def lut(name: str = DEFAULT_PALETTE, n: int = 256) -> np.ndarray:
    """Return an ``(n, 3)`` float32 lookup table for ``name`` in [0, 1]."""
    xs, cols = _control_points(name)
    t = np.linspace(0.0, 1.0, int(n), dtype=np.float32)
    out = np.empty((int(n), 3), dtype=np.float32)
    for c in range(3):
        out[:, c] = np.interp(t, xs, cols[:, c])
    return out


def colorize(values01, name: str = DEFAULT_PALETTE) -> np.ndarray:
    """Map scalar values in [0, 1] to RGB.

    Accepts any array shape; returns the same shape with a trailing size-3
    axis (e.g. ``(N,) -> (N, 3)`` or ``(H, W) -> (H, W, 3)``). Output is
    float32 in [0, 1].
    """
    xs, cols = _control_points(name)
    v = np.clip(np.asarray(values01, dtype=np.float32), 0.0, 1.0)
    flat = v.ravel()
    out = np.empty((flat.size, 3), dtype=np.float32)
    for c in range(3):
        out[:, c] = np.interp(flat, xs, cols[:, c])
    return out.reshape(v.shape + (3,))


def normalize(values, lo=None, hi=None, gamma: float = 1.0) -> np.ndarray:
    """Scale ``values`` into [0, 1] using (lo, hi), with optional gamma.

    ``lo``/``hi`` default to the data min/max. ``gamma`` < 1 brightens the
    low end (useful for speed/energy fields that are heavily skewed).
    """
    v = np.asarray(values, dtype=np.float32)
    if lo is None:
        lo = float(np.min(v)) if v.size else 0.0
    if hi is None:
        hi = float(np.max(v)) if v.size else 1.0
    span = hi - lo
    if span <= 1e-12:
        out = np.zeros_like(v)
    else:
        out = np.clip((v - lo) / span, 0.0, 1.0)
    if gamma != 1.0:
        out = out ** float(gamma)
    return out
