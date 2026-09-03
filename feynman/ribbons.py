"""Turn the field into geometry a GPU can draw, once.

A line on a canvas is a stroke with a width. A line on a GPU is either a 1px
GL line or it is geometry, and 1px does not hold up projected, so every line
here becomes a ribbon: a triangle strip laid along its centreline, as wide as
the stroke would have been.

Two other things are baked in here rather than left to the shader:

  u    how far along its own line each point sits, 0 to 1 by arc length. The
       shader trims the line to the flood's growth by discarding u > grow, and
       dashes the scalars off the same coordinate.
  eid  which sample of the state CHOP this point reads its growth and tone
       from. Lines index by line; marks index by vertex, offset past the lines,
       so one texture lookup serves both.

The marks are one quad each, not a fan of triangles, with the disc or the cross
cut out of it by the shader. Nothing here knows about TouchDesigner: it hands
back numpy arrays, and td/feynman_geo.py is what pours them into a Script SOP.

Geometry is in the field's own pixel coordinates, y down, so an orthographic
camera the size of the field puts one unit on one pixel at native resolution
and a width of 3 is 3 pixels. Everything downstream scales from there.
"""

import numpy as np

from .field import FERMION, BOSON, SCALAR

# kind, as the shader reads it
KIND_FERMION, KIND_BOSON, KIND_SCALAR, KIND_DOT, KIND_CROSS = 0, 1, 2, 3, 4

# Stroke weights, straight out of design/network.js drawEdge.
_WEIGHT = {FERMION: 1.35, BOSON: 1.15, SCALAR: 1.35}
_KIND = {FERMION: KIND_FERMION, BOSON: KIND_BOSON, SCALAR: KIND_SCALAR}


def _normals(poly):
    """Unit normals at each point of a polyline, averaged across the joint."""
    d = np.zeros_like(poly)
    d[1:-1] = poly[2:] - poly[:-2]
    d[0] = poly[1] - poly[0]
    d[-1] = poly[-1] - poly[-2]
    length = np.hypot(d[:, 0], d[:, 1])
    length[length == 0] = 1.0
    d = d / length[:, None]
    return np.stack([-d[:, 1], d[:, 0]], axis=1)


def build(field, width=1.0, mark_scale=1.0):
    """Ribbons for every line, quads for every mark.

    Returns a dict of arrays plus the primitive index lists:

        P     (n, 3) float32   position, z = 0
        uv    (n, 2) float32   u along the line (x) and across it (y, -1..1);
                               for a mark, the quad's own -1..1 square
        eid   (n,)   float32   sample to read out of the state CHOP
        kind  (n,)   float32   which of the five things this is
        strips list of lists   point indices, one triangle strip per line
        quads  list of lists   point indices, four per mark
    """
    P, uv, eid, kind = [], [], [], []
    strips, quads = [], []

    for i in range(field.n_edges):
        poly = field.polys[i].astype(np.float64)
        if len(poly) < 2:
            continue
        seg = np.hypot(*(poly[1:] - poly[:-1]).T)
        arc = np.concatenate([[0.0], np.cumsum(seg)])
        total = arc[-1] if arc[-1] > 0 else 1.0
        u = arc / total

        s = float(field.escale[i])
        half = 0.5 * _WEIGHT[int(field.etype[i])] * (0.8 + 0.3 * s) * width
        nrm = _normals(poly) * half

        base = len(P)
        strip = []
        for k in range(len(poly)):
            for side in (-1.0, 1.0):
                P.append((poly[k][0] + nrm[k][0] * side,
                          poly[k][1] + nrm[k][1] * side, 0.0))
                uv.append((u[k], side))
                eid.append(float(i))
                kind.append(float(_KIND[int(field.etype[i])]))
            strip.extend([base + 2 * k, base + 2 * k + 1])
        strips.append(strip)

    # One quad per vertex. Radius follows the marks in drawEdge: a node is
    # smaller than the × that replaces it where a line ends in the vacuum.
    s = float(field.scale)
    rr = 1.8 * (0.72 + 0.5 * s) * mark_scale
    q = 3.4 * (0.72 + 0.5 * s) * mark_scale
    for v in range(len(field.verts)):
        is_x = bool(field.is_x[v])
        r = (q if is_x else rr) * 1.25          # a little room for the antialias
        x, y = float(field.verts[v][0]), float(field.verts[v][1])
        base = len(P)
        for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            P.append((x + dx * r, y + dy * r, 0.0))
            uv.append((float(dx), float(dy)))
            eid.append(float(field.n_edges + v))
            kind.append(float(KIND_CROSS if is_x else KIND_DOT))
        quads.append([base, base + 1, base + 2, base + 3])

    return {
        "P": np.asarray(P, dtype=np.float32),
        "uv": np.asarray(uv, dtype=np.float32),
        "eid": np.asarray(eid, dtype=np.float32),
        "kind": np.asarray(kind, dtype=np.float32),
        "strips": strips,
        "quads": quads,
        "n_edges": field.n_edges,
        "n_verts": len(field.verts),
    }


def state_rows(field, flood):
    """The per-frame table the shader looks growth and tone up in.

    One row per line then one per vertex, which is the order build() indexes
    with. Lines carry their growth; marks are never part-drawn, so theirs is 1.
    """
    n = field.n_edges + len(field.verts)
    grow = np.ones(n, dtype=np.float32)
    tone = np.zeros(n, dtype=np.float32)
    grow[:field.n_edges] = flood.grow
    tone[:field.n_edges] = flood.tone
    tone[field.n_edges:] = flood.vtone
    return grow, tone
