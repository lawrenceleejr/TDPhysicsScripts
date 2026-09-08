"""Instrument annotation for a 3D scene: dog-leg leaders and vector labels.

The look this builds is a detector readout: each track gets a tick at a point
on it, a leader that kinks once and runs out to the side, and a block of
readout text at the end of it. The leaders are the part that has to be right --
an annotation that does not obviously point at its own track is worse than no
annotation -- so anchors are the real track points and the label column is
packed so no two labels overlap.

Everything here is plain numpy over world coordinates, so it is unit-tested
rather than eyeballed in TouchDesigner. The scene's camera looks down +z, and
the labels are laid out in the z = ``plane`` plane so they face it squarely
while the geometry they annotate rotates underneath.
"""
from __future__ import annotations

import numpy as np

from .vecfont import strokes, text_width

TICK = 0.10          # half-size of the anchor tick, in world units
ELBOW = 0.55         # length of the diagonal run out of the anchor
ROW = 0.60           # vertical pitch of the label column
TEXT_H = 0.26        # cap height of the readout text


def rotate_y(points: np.ndarray, degrees: float) -> np.ndarray:
    """Spin points about the y axis, the way a scene's Orbit control does.

    The annotated geometry turns under the labels, so an anchor has to be the
    *turned* position of its track point or the leader drifts off the track.
    """
    a = np.radians(float(degrees))
    ca, sa = np.cos(a), np.sin(a)
    p = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    out = np.empty_like(p)
    out[:, 0] = ca * p[:, 0] + sa * p[:, 2]
    out[:, 1] = p[:, 1]
    out[:, 2] = -sa * p[:, 0] + ca * p[:, 2]
    return out


def _column(anchors: np.ndarray, half_width: float, row: float):
    """Place one label per anchor: a side, and a y that nothing else uses.

    Each label goes out to whichever side its anchor already leans, and the
    labels on a side are pushed apart onto a fixed pitch, nearest-anchor
    first, so the column reads in order and never collides.
    """
    order = np.argsort(-anchors[:, 1])          # top of the frame first
    placed: dict[int, tuple[float, float]] = {}
    taken: dict[int, list[float]] = {-1: [], 1: []}
    for i in order:
        side = -1 if anchors[i, 0] < 0 else 1
        y = float(anchors[i, 1])
        used = taken[side]
        while any(abs(y - v) < row * 0.98 for v in used):
            y -= row                            # slide down to a free slot
        used.append(y)
        placed[int(i)] = (float(side), y)
    return placed


def leaders(anchors, labels, half_width: float = 4.2, plane: float = 0.0,
            tick: float = TICK, elbow: float = ELBOW, row: float = ROW,
            text_h: float = TEXT_H):
    """Build the annotation geometry for a set of anchors.

    ``anchors`` is (n, 3) world points (already turned into view orientation),
    ``labels`` the matching strings. Returns ``(polylines, kinds)``: a list of
    (k, 3) float32 arrays and, for each, ``"tick"``, ``"lead"`` or ``"text"``
    so the caller can colour the readout differently from the leader.
    """
    anchors = np.asarray(anchors, dtype=np.float32).reshape(-1, 3)
    if len(anchors) == 0:
        return [], []
    placed = _column(anchors, half_width, row)
    polys: list[np.ndarray] = []
    kinds: list[str] = []

    def add(pts2d, kind, z=plane):
        arr = np.zeros((len(pts2d), 3), dtype=np.float32)
        for k, (px, py) in enumerate(pts2d):
            arr[k, 0], arr[k, 1], arr[k, 2] = px, py, z
        polys.append(arr)
        kinds.append(kind)

    for i in range(len(anchors)):
        ax, ay, az = (float(v) for v in anchors[i])
        side, ly = placed[int(i)]
        text = str(labels[i]) if i < len(labels) else ""
        width = text_width(text) * text_h

        # The tick sits on the track itself, in the track's own plane.
        polys.append(np.asarray([[ax - tick, ay, az], [ax + tick, ay, az]], dtype=np.float32))
        kinds.append("tick")
        polys.append(np.asarray([[ax, ay - tick, az], [ax, ay + tick, az]], dtype=np.float32))
        kinds.append("tick")

        # Dog leg: out of the anchor at 45 degrees, then a level run to the
        # label, then a short underline the text sits on.
        ex = ax + side * elbow
        ey = ay + (elbow if ly >= ay else -elbow)
        end_x = side * half_width
        if side > 0:
            text_x = end_x + 0.12
            rule = [(end_x, ly), (end_x + width + 0.24, ly)]
        else:
            text_x = end_x - 0.12 - width
            rule = [(end_x, ly), (end_x - width - 0.24, ly)]
        add([(ax, ay), (ex, ey), (end_x, ly)], "lead")
        add(rule, "lead")
        for poly in strokes(text, height=text_h, origin=(text_x, ly + 0.10)):
            add([(float(px), float(py)) for px, py in poly], "text")
    return polys, kinds


def track_labels(tracks, mass=None, prefix="TRK"):
    """Readout lines for a list of tracks: the numbers a detector display shows.

    ``tracks`` are objects with ``pt`` and ``charge`` (the Track from
    ``lhc_tracks``); ``mass`` adds an event line when given.
    """
    out = []
    for i, tr in enumerate(tracks):
        q = "+" if int(getattr(tr, "charge", 1)) > 0 else "-"
        pt = float(getattr(tr, "pt", 0.0))
        out.append(f"{prefix}{i + 1:02d} PT {pt:5.1f} GEV Q{q}")
    if mass is not None:
        out.append(f"M {float(mass):6.2f} GEV")
    return out
