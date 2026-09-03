"""The Feynman field, and the flood that animates it.

The field itself is not generated here. It is exported by tools/export_field.mjs
from the same generator that draws the poster and the website background, so
what loads is already planar, already thinned, and every vertex is already a
legal Standard Model interaction. See the note at the top of that script.

What is here is the part that has to be live, and it is the website's mechanic:

  - One or two walkers, each flooding the mesh from a single vertex by a
    direction-biased Dijkstra. A plain Dijkstra spreads as a disc; multiplying
    each edge's cost by how far it points from a slowly drifting heading makes
    the front travel instead — cheap along the heading, expensive across it —
    so the lit region wanders rather than swelling.
  - Every edge gets an arrival distance out of that. A frame lights only the
    edges whose arrival falls inside (head - tail, head], part-grown at the
    head and fading out at the tail.
  - A line grows out of the vertex the flood reached first, on the poster's
    curve: 1 - (1 - t) ** 5.5, fast out of the vertex and then a long decay.
  - Walkers run out of phase so one is always mid-life while another re-seeds,
    and the field never empties.

No TouchDesigner in here — this is plain Python and numpy, which is what lets
tools/preview.py check it without opening TD.
"""

import json
import math
import random
from heapq import heappush, heappop

import numpy as np

# Types, as the generator names them.
FERMION, BOSON, SCALAR = 0, 1, 2
_TYPE = {"f": FERMION, "b": BOSON, "h": SCALAR}


class Field:
    """A loaded field: vertices, lines, and the polyline each line is drawn as."""

    def __init__(self, path):
        with open(path, "r") as fh:
            raw = json.load(fh)

        self.w = float(raw["w"])
        self.h = float(raw["h"])
        self.seed = raw.get("seed")
        self.scale = float(raw.get("scale", 2.8))

        self.verts = np.asarray(raw["verts"], dtype=np.float32)
        self.is_x = np.asarray(raw["isX"], dtype=np.uint8)

        n = len(raw["edges"])
        self.n_edges = n
        self.ea = np.zeros(n, dtype=np.int32)
        self.eb = np.zeros(n, dtype=np.int32)
        self.etype = np.zeros(n, dtype=np.int32)
        self.escale = np.zeros(n, dtype=np.float32)
        self.elen = np.zeros(n, dtype=np.float32)
        self.polys = []          # per edge: (k, 2) float32, the drawn centreline

        for i, e in enumerate(raw["edges"]):
            self.ea[i] = e["a"]
            self.eb[i] = e["b"]
            self.etype[i] = _TYPE[e["t"]]
            self.escale[i] = e.get("s", self.scale)
            self.elen[i] = e["len"]
            if "pts" in e:
                # a boson: the wave is baked in by the exporter
                self.polys.append(np.asarray(e["pts"], dtype=np.float32))
            else:
                self.polys.append(np.asarray(
                    [self.verts[e["a"]], self.verts[e["b"]]], dtype=np.float32))

        # adjacency, for the flood
        self.inc = [[] for _ in range(len(self.verts))]
        for i in range(n):
            self.inc[int(self.ea[i])].append(i)
            self.inc[int(self.eb[i])].append(i)

    def __repr__(self):
        return (f"<Field {int(self.w)}x{int(self.h)} seed={self.seed} "
                f"{self.n_edges} lines {len(self.verts)} vertices>")


class _Walker:
    __slots__ = ("arrive", "rev", "far", "head")

    def __init__(self, arrive, rev, far, head):
        self.arrive, self.rev, self.far, self.head = arrive, rev, far, head


class Flood:
    """The website's travelling flood, one or two walkers at a time.

    Parameters are the ones worth having on a control panel:

      traverse  seconds for a walker to cross its whole flood — the speed dial
      tail      how much of the flood stays lit behind the head, as a fraction.
                The website takes this from the scroll; here it is a dial, and
                past 1.0 nothing ever fades out.
      fade      how much of the tail is spent fading, rather than held lit
      walkers   how many fronts are travelling at once
    """

    def __init__(self, field, walkers=2, traverse=34.0, tail=0.85, fade=0.34,
                 seed=None):
        self.f = field
        self.traverse = float(traverse)
        self.tail = float(tail)
        self.fade = float(fade)
        self.rng = random.Random(seed)

        n = field.n_edges
        self.grow = np.zeros(n, dtype=np.float32)
        self.tone = np.zeros(n, dtype=np.float32)
        self.rev = np.zeros(n, dtype=np.uint8)
        self.vtone = np.zeros(len(field.verts), dtype=np.float32)

        self.walkers = [self._seed_walker(k / max(1, walkers))
                        for k in range(int(walkers))]
        self.gather()

    # ---- the flood ------------------------------------------------------
    def _flood(self, start):
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
            if d > far:
                far = d
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

    def _seed_walker(self, phase, at=None):
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
    def advance(self, dt):
        """Move every front on, and re-seed any that is spent."""
        for k, w in enumerate(self.walkers):
            w.head += (w.far / self.traverse) * dt
            if w.head - w.far * self.tail > w.far:
                self.walkers[k] = self._seed_walker(0.0)
        self.gather()

    def gather(self):
        """Each line keeps the strongest state across the walkers."""
        f = self.f
        self.grow[:] = 0.0
        self.tone[:] = 0.0
        fade = self.fade
        for w in self.walkers:
            tail = w.far * self.tail
            if tail <= 0:
                continue
            lo = w.head - tail
            live = (w.arrive <= w.head) & (w.arrive >= lo) & np.isfinite(w.arrive)
            if not live.any():
                continue
            behind = (w.head - w.arrive[live]).astype(np.float32)
            # The head end draws part-grown, on the poster's own curve.
            lin = np.minimum(1.0, behind / (f.elen[live] * 2.2))
            grow = np.maximum(0.02, 1.0 - np.power(1.0 - lin, 5.5))
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

    def fill(self):
        """Light the whole field and hold it — the still, for a held look."""
        for w in self.walkers:
            w.head = w.far * 3.0
        keep, self.tail = self.tail, 10.0
        self.gather()
        self.tail = keep
