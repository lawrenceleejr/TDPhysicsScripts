"""The Feynman field: a planar mesh of Standard Model lines, lit by a flood.

This is the artwork behind the USMCC poster and the event site, running live.
A field of fermion, boson and Higgs lines fills the frame, every vertex of it
a legal interaction, and a travelling front lights it a line at a time: each
line grows out of the vertex the front reached first, holds, and fades out
behind.

The field itself is not generated here. ``data/export_field.mjs`` runs the
same generator that draws the poster and the website and writes the result to
``data/feynman_*.json``, refusing to write a field with an illegal vertex in
it. Porting that generator — a Delaunay mesh, the thinning that keeps every
vertex above two legs, the leg-angle hygiene, the type assignment — would
have meant keeping two copies of some careful rules in step for no gain.

What is here is the part that has to be live, and it is the website's own
mechanic:

  * One front per walker, flooding the mesh from a single vertex by a
    direction-biased Dijkstra. A plain Dijkstra spreads as a disc; multiplying
    each edge's cost by how far it points away from a slowly drifting heading
    makes the front travel instead — cheap along the heading, expensive across
    it — so the lit region wanders rather than swelling.
  * Every line gets an arrival distance out of that, and a frame lights the
    lines whose arrival falls inside ``(head - tail, head]``: part-grown at
    the head, fading at the tail.
  * A line grows over several of its own lengths of front travel (``grow_len``)
    on an ease-in curve, so it creeps out of its vertex rather than popping.
  * Walkers run out of phase, spread over the whole cycle rather than over one
    traverse, so one is always mid-life while another re-seeds and the field
    neither empties nor fills up and sits.

No TouchDesigner in here: plain Python and numpy, which is what lets
``tests/test_physics.py`` check it without opening TD.
"""

from __future__ import annotations

import json
import math
import random
from heapq import heappush, heappop

import numpy as np

from .palette import DEFAULT_PALETTE, lut

# The three kinds of line, as the generator names them.
FERMION, BOSON, SCALAR = 0, 1, 2
_TYPE = {"f": FERMION, "b": BOSON, "h": SCALAR}

# Where each kind of line reads the palette. Any palette then gives three
# distinguishable line colours; on "sigma" these land on cream, bone and
# vermillion, which is the identity the artwork is drawn in.
TONE_AT = (0.93, 0.70, 0.45)

# Every legal Standard Model vertex, as the exporter's audit has them. Kept
# here so a field can be checked after loading as well as before writing.
LEGAL_VERTICES = frozenset(
    ["bff", "bbff", "ffh", "bbb", "bbbb", "bbh", "bbhh", "hhh", "hhhh"]
)


SCALAR = 2                 # etype index of an 'h' (Higgs / scalar) line
SEGMENT_LEN = 3.0          # field units per drawn point on a straight line
DASH_FIELD_UNITS = 8.0     # target dash pitch, in the field's own units
DASH_GAP = 0.6             # gap length as a share of a dash


def subdivide(a, b, length_field_units: float):
    """A straight segment as a chain of points, one every SEGMENT_LEN units.

    A two-point line has nowhere for a growing front to be: it is either
    absent or fully drawn (with a brightness ramp), which is why short legs
    appeared to pop into existence while the bosons -- whose wave the exporter
    bakes as many points -- crept in properly. Subdividing gives every line
    the geometry to be drawn along.
    """
    n = max(2, int(round(length_field_units / SEGMENT_LEN)) + 1)
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    return (np.asarray(a, dtype=np.float32)[None, :] * (1.0 - t)
            + np.asarray(b, dtype=np.float32)[None, :] * t)


def dash_spans(length_field_units: float):
    """Dash a segment of the given length: (start, end) fractions along it.

    An odd number of dashes so both ends are ink (the line meets its vertices),
    gaps DASH_GAP of a dash, at least three dashes so even a stub reads as
    dashed rather than as a dot.
    """
    n = max(3, int(round(length_field_units / DASH_FIELD_UNITS)))
    if n % 2 == 0:
        n += 1
    d = 1.0 / (n + DASH_GAP * (n - 1))
    return [(k * d * (1.0 + DASH_GAP), k * d * (1.0 + DASH_GAP) + d) for k in range(n)]


class Field:
    """A loaded field: vertices, lines, and the polyline each line is drawn as."""

    def __init__(self, path: str):
        with open(path, "r") as fh:
            raw = json.load(fh)

        self.w = float(raw["w"])
        self.h = float(raw["h"])
        self.seed = raw.get("seed")
        self.scale = float(raw.get("scale", 2.8))

        self.verts = np.asarray(raw["verts"], dtype=np.float32)
        self.is_x = np.asarray(raw["isX"], dtype=np.uint8)
        self.bend = np.asarray(raw.get("bend", np.zeros(len(self.verts))), dtype=np.uint8)

        n = len(raw["edges"])
        self.n_edges = n
        self.ea = np.zeros(n, dtype=np.int32)
        self.eb = np.zeros(n, dtype=np.int32)
        self.etype = np.zeros(n, dtype=np.int32)
        self.elen = np.zeros(n, dtype=np.float32)
        self.polys: list[np.ndarray] = []      # per line: (k, 2), the drawn centreline

        for i, e in enumerate(raw["edges"]):
            self.ea[i] = e["a"]
            self.eb[i] = e["b"]
            self.etype[i] = _TYPE[e["t"]]
            self.elen[i] = e["len"]
            if "pts" in e:
                # a boson: the exporter bakes its wave in
                self.polys.append(np.asarray(e["pts"], dtype=np.float32))
            else:
                self.polys.append(
                    np.asarray([self.verts[e["a"]], self.verts[e["b"]]], dtype=np.float32)
                )

        # adjacency, for the flood
        self.inc: list[list[int]] = [[] for _ in range(len(self.verts))]
        for i in range(n):
            self.inc[int(self.ea[i])].append(i)
            self.inc[int(self.eb[i])].append(i)

    def __repr__(self) -> str:
        return (f"<Field {int(self.w)}x{int(self.h)} seed={self.seed} "
                f"{self.n_edges} lines {len(self.verts)} vertices>")

    def vertex_legs(self) -> list[str]:
        """The kinds of line meeting each vertex, as a sorted string."""
        names = "fbh"
        legs: list[list[str]] = [[] for _ in range(len(self.verts))]
        for i in range(self.n_edges):
            t = names[int(self.etype[i])]
            legs[int(self.ea[i])].append(t)
            legs[int(self.eb[i])].append(t)
        return ["".join(sorted(v)) for v in legs]

    def illegal_vertices(self) -> list[int]:
        """Vertices that are not a legal interaction.

        One leg is an external line and two legs of one kind is a kink in a
        single propagator, so neither is an interaction and neither is judged.
        """
        bad = []
        for v, legs in enumerate(self.vertex_legs()):
            if len(legs) <= 1:
                continue
            if len(legs) == 2 and legs[0] == legs[1]:
                continue
            if legs not in LEGAL_VERTICES:
                bad.append(v)
        return bad


class _Walker:
    __slots__ = ("arrive", "rev", "far", "head")

    def __init__(self, arrive, rev, far, head):
        self.arrive, self.rev, self.far, self.head = arrive, rev, far, head


class Flood:
    """The travelling flood over a field: one front per walker.

    The dials worth having on a control panel:

      traverse  seconds for a front to cross its whole flood — the speed
      tail      how much of the flood stays lit behind the head, as a fraction
                of it. This is the lifetime of a line: ``tail * traverse``
                seconds. Long, and the field fills up and sits there; short,
                and the pattern is always turning over.
      fade      how much of the tail is spent fading out rather than held lit
      walkers   how many fronts travel at once. More of them fill in the
                troughs a short tail leaves between one front and the next.
      grow_len  how far the front travels, in multiples of a line's own length,
                while that line grows to full: the creep. 2 pops lines out;
                6-10 lets each one be watched drawing itself.
    """

    def __init__(self, field: Field, walkers: int = 3, traverse: float = 45.0,
                 tail: float = 0.4, fade: float = 0.5, grow_len: float = 6.0,
                 seed: int | None = None):
        self.f = field
        self.traverse = float(traverse)
        self.tail = float(tail)
        self.fade = float(fade)
        self.grow_len = float(grow_len)
        self.rng = random.Random(seed)

        n = field.n_edges
        self.grow = np.zeros(n, dtype=np.float32)
        self.tone = np.zeros(n, dtype=np.float32)
        self.rev = np.zeros(n, dtype=np.uint8)
        self.vtone = np.zeros(len(field.verts), dtype=np.float32)
        self.reseed(int(walkers))

    def reseed(self, walkers: int | None = None) -> None:
        """Start over with fresh fronts, spread over the whole cycle.

        A front's cycle is ``1 + tail`` traverses long, not one, so phases at
        ``k / walkers`` bunch them into the first part of it and leave a
        trough at the end. Spreading over the cycle is what keeps the lit
        share steady.
        """
        if walkers is not None:
            self.n_walkers = max(1, int(walkers))
        cycle = 1.0 + self.tail
        self.walkers = [self._seed_walker(k / self.n_walkers * cycle)
                        for k in range(self.n_walkers)]
        self.gather()

    # ---- the flood ------------------------------------------------------
    def _flood(self, start: int):
        f = self.f
        n = f.n_edges
        arrive = np.full(n, np.inf, dtype=np.float64)
        rev = np.zeros(n, dtype=np.uint8)
        seen = np.full(len(f.verts), np.inf, dtype=np.float64)

        th0 = self.rng.random() * math.tau
        l1 = 900 + self.rng.random() * 1400
        l2 = 320 + self.rng.random() * 500
        p1 = self.rng.random() * math.tau
        p2 = self.rng.random() * math.tau

        def heading(d):
            return th0 + 1.15 * math.sin(d / l1 + p1) + 0.5 * math.sin(d / l2 + p2)

        seen[start] = 0.0
        heap = [(0.0, int(start))]
        far = 0.0
        while heap:
            d, u = heappop(heap)
            if d > seen[u] + 1e-9:
                continue
            far = max(far, d)
            hd = heading(d)
            for ei in f.inc[u]:
                a, b = int(f.ea[ei]), int(f.eb[ei])
                o = b if a == u else a
                dx = f.verts[o][0] - f.verts[u][0]
                dy = f.verts[o][1] - f.verts[u][1]
                phi = math.atan2(dy, dx)
                nd = d + f.elen[ei] * (1.0 + 2.6 * (1.0 - math.cos(phi - hd)))
                if nd < arrive[ei]:
                    arrive[ei] = nd
                    rev[ei] = 1 if b == u else 0
                if nd < seen[o] - 1e-9:
                    seen[o] = nd
                    heappush(heap, (nd, o))
        return arrive, rev, (far or 1.0)

    def _seed_walker(self, phase: float, at=None) -> _Walker:
        f = self.f
        if at is None:
            live = [i for i in range(len(f.verts)) if f.inc[i]]
            start = self.rng.choice(live) if live else 0
        else:
            d = np.hypot(f.verts[:, 0] - at[0], f.verts[:, 1] - at[1])
            start = int(np.argmin(d))
        arrive, rev, far = self._flood(start)
        return _Walker(arrive, rev, far, phase * far)

    # ---- per frame ------------------------------------------------------
    def step(self, dt: float) -> None:
        """Move every front on, and re-seed any that is spent."""
        for k, w in enumerate(self.walkers):
            w.head += (w.far / self.traverse) * dt
            if w.head - w.far * self.tail > w.far:
                self.walkers[k] = self._seed_walker(0.0)
        self.gather()

    def gather(self) -> None:
        """Each line keeps the strongest state across the fronts."""
        f = self.f
        self.grow[:] = 0.0
        self.tone[:] = 0.0
        fade = max(1e-3, self.fade)
        for w in self.walkers:
            tail = w.far * self.tail
            if tail <= 0:
                continue
            lo = w.head - tail
            live = (w.arrive <= w.head) & (w.arrive >= lo) & np.isfinite(w.arrive)
            if not live.any():
                continue
            behind = (w.head - w.arrive[live]).astype(np.float32)
            lin = np.minimum(1.0, behind / (f.elen[live] * max(0.1, self.grow_len)))
            # Ease-in, then steady: the tip leaves the vertex gently and creeps
            # along at a near-constant pace (smoothstep's early, slow half then
            # its linear middle) instead of shooting out and stalling.
            grow = np.maximum(0.02, lin * lin * (3.0 - 2.0 * lin))
            age = behind / tail                      # 0 at the head, 1 at the tail
            tone = np.where(age > 1.0 - fade,
                            np.maximum(0.0, (1.0 - age) / fade), 1.0)
            idx = np.nonzero(live)[0]
            take = grow > self.grow[idx]
            self.grow[idx[take]] = grow[take]
            self.rev[idx[take]] = w.rev[idx[take]]
            self.tone[idx] = np.maximum(self.tone[idx], tone)

        # A vertex is lit as strongly as the strongest line touching it, which
        # is what keeps one mark per vertex rather than one per line.
        self.vtone[:] = 0.0
        np.maximum.at(self.vtone, self.f.ea, self.tone)
        np.maximum.at(self.vtone, self.f.eb, self.tone)

    def fill(self) -> None:
        """Light the whole field and hold it — the still, for a held look."""
        for w in self.walkers:
            w.head = w.far * 3.0
        keep, self.tail = self.tail, 10.0
        self.gather()
        self.tail = keep

    @property
    def lit_share(self) -> float:
        """The fraction of the field currently carrying any light."""
        return float((self.tone > 0.01).mean())


class FeynmanShow:
    """A field, its flood, and the two arrays TouchDesigner asks for.

    The split is what makes this run at frame rate. The geometry never
    changes: every line and every vertex mark is built once, as polylines in a
    fixed order, and handed to a Script SOP. What changes each frame is one
    colour and one alpha per point, which is a numpy expression over the whole
    field at once — no Python loop over ten thousand points, which is what a
    Script SOP rebuilding itself every frame would cost.

    A line is trimmed to its growth by alpha rather than by geometry: points
    past the growing tip go to zero. Between the last visible point and the
    first hidden one the renderer interpolates, so the tip comes out soft over
    the three-pixel spacing of the sampled wave, which is what it looks like
    drawn.

    Everything comes out in world units: the field centred on the origin,
    ``world`` units wide, y up.
    """

    def __init__(self, path: str, world: float = 16.0, palette: str = "sigma",
                 marks: bool = True, **flood):
        self.field = Field(path)
        self.flood = Flood(self.field, **flood)
        self.world = float(world)
        self.want_marks = bool(marks)
        self.palette = palette or DEFAULT_PALETTE
        self._lut = lut(self.palette, 256)
        self._build()

    # ---- geometry, once -------------------------------------------------
    def _build(self) -> None:
        f = self.field
        s = self.world / f.w
        self.unit = s
        cx, cy = f.w / 2.0, f.h / 2.0

        def place(xy):
            out = np.zeros((len(xy), 3), dtype=np.float32)
            out[:, 0] = (xy[:, 0] - cx) * s
            out[:, 1] = (cy - xy[:, 1]) * s          # the field is drawn y-down
            return out

        polys: list[np.ndarray] = []
        # Which line or vertex each point belongs to, and how far along its own
        # line it sits, measured from end a. Both are what the per-frame
        # expression indexes with.
        owner: list[np.ndarray] = []
        along: list[np.ndarray] = []

        for i in range(f.n_edges):
            pts = place(f.polys[i])
            raw_len = float(f.elen[i])
            if len(pts) == 2 and int(f.etype[i]) != SCALAR:
                # A straight propagator: give it points to grow along.
                pts = subdivide(pts[0], pts[1], raw_len)
                polys.append(pts)
                owner.append(np.full(len(pts), i, dtype=np.int32))
                along.append(np.linspace(0.0, 1.0, len(pts), dtype=np.float32))
                continue
            if int(f.etype[i]) == SCALAR and len(pts) == 2:
                # A scalar propagator is drawn dashed (the Feynman convention
                # for a Higgs line): several short polylines along the same
                # segment, each carrying its own 'along' span so the flood
                # still lights the line progressively from either end.
                for a0, a1 in dash_spans(raw_len):
                    p0 = pts[0] + (pts[1] - pts[0]) * a0
                    p1 = pts[0] + (pts[1] - pts[0]) * a1
                    seg = subdivide(p0, p1, raw_len * (a1 - a0))
                    polys.append(seg)
                    owner.append(np.full(len(seg), i, dtype=np.int32))
                    along.append(np.linspace(a0, a1, len(seg), dtype=np.float32))
                continue
            polys.append(pts)
            owner.append(np.full(len(pts), i, dtype=np.int32))
            along.append(np.linspace(0.0, 1.0, len(pts), dtype=np.float32))

        self.n_line_points = sum(len(p) for p in polys)
        self.mark_at: list[int] = []                  # the vertex each mark shows

        if self.want_marks:
            r = s * 3.0
            q = r * 1.9
            for v in range(len(f.verts)):
                if len(f.bend) > v and f.bend[v]:
                    continue                          # a kink is not an interaction
                x = (f.verts[v][0] - cx) * s
                y = (cy - f.verts[v][1]) * s
                if f.is_x[v]:
                    strokes = [[(x - q, y - q), (x + q, y + q)],
                               [(x + q, y - q), (x - q, y + q)]]
                else:
                    strokes = [[(x - r, y - r), (x + r, y - r), (x + r, y + r),
                                (x - r, y + r), (x - r, y - r)]]
                for st in strokes:
                    pts = np.zeros((len(st), 3), dtype=np.float32)
                    for k, (px, py) in enumerate(st):
                        pts[k, 0], pts[k, 1] = px, py
                    polys.append(pts)
                    owner.append(np.full(len(pts), -1 - v, dtype=np.int32))
                    along.append(np.zeros(len(pts), dtype=np.float32))
                    self.mark_at.append(v)

        self.polys = polys
        self._owner = np.concatenate(owner) if owner else np.zeros(0, dtype=np.int32)
        self._along = np.concatenate(along) if along else np.zeros(0, dtype=np.float32)
        self._is_line = self._owner >= 0
        self._line_of = np.where(self._is_line, self._owner, 0)
        self._vert_of = np.where(self._is_line, 0, -1 - self._owner)
        self.n_points = len(self._owner)
        self._rgba = np.zeros((self.n_points, 4), dtype=np.float32)
        self._type_rgb = np.asarray(
            [self._lut[int(np.clip(t * 255, 0, 255))] for t in TONE_AT],
            dtype=np.float32)

    # ---- the dials ------------------------------------------------------
    def set_world(self, world: float) -> None:
        if abs(float(world) - self.world) > 1e-6:
            self.world = float(world)
            self._build()

    def set_marks(self, on: bool) -> None:
        if bool(on) != self.want_marks:
            self.want_marks = bool(on)
            self._build()

    def set_palette(self, name: str) -> None:
        if name and name != self.palette:
            self.palette = name
            self._lut = lut(name, 256)
            self._type_rgb = np.asarray(
                [self._lut[int(np.clip(t * 255, 0, 255))] for t in TONE_AT],
                dtype=np.float32)

    # ---- per frame ------------------------------------------------------
    def step(self, dt: float) -> None:
        self.flood.step(dt)

    def colours(self, cut: float = 0.02, gain: float = 1.0) -> np.ndarray:
        """(n_points, 4) float32: the colour and alpha of every point, now.

        The order is the order ``self.polys`` is in, so a Script SOP that
        appends those polylines and a Script CHOP that carries these channels
        line up point for point.

        ``gain`` scales the ink. The tone a line carries is its age in the
        flood, which is what makes the field breathe, so brightness is applied
        on top of it rather than by flattening it: above 1 the dim end of the
        fade lifts clear of the black without the lit end blowing out, because
        the result is clamped.
        """
        f, fl = self.field, self.flood
        out = self._rgba

        tone = fl.tone[self._line_of]
        rgb = self._type_rgb[f.etype[self._line_of]]
        # Along the line from the end the front arrived at, so a line reversed
        # by the flood grows from its other end.
        rev = fl.rev[self._line_of].astype(bool)
        u = np.where(rev, 1.0 - self._along, self._along)
        lit = (u <= fl.grow[self._line_of]) & (tone > cut)

        vt = fl.vtone[self._vert_of]
        mrgb = self._type_rgb[FERMION]

        out[:, :3] = np.where(self._is_line[:, None], rgb, mrgb[None, :])
        out[:, :3] *= np.where(self._is_line, tone, vt)[:, None]
        if gain != 1.0:
            np.multiply(out[:, :3], float(gain), out=out[:, :3])
            np.clip(out[:, :3], 0.0, 1.0, out=out[:, :3])
        out[:, 3] = np.where(self._is_line, lit, vt > cut).astype(np.float32)
        return out
