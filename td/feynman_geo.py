"""Script SOP: the field as geometry, built once.

Drop this in a Script SOP's callbacks DAT. It reads a field out of fields/,
turns every line into a ribbon and every vertex into a mark quad, and hands
the lot to the SOP with the attributes the shader wants:

    au      how far along its own line the point sits, 0 to 1
    av      which side of the centreline it is on, -1 or 1
    eid     which row of the state CHOP to read growth and tone from
    kind    0 fermion, 1 boson, 2 scalar, 3 node, 4 vacuum end

All four are plain float point attributes rather than texture coordinates,
because a GLSL MAT reads those by name with no argument about what uv[0] means.

Building is slow — tens of thousands of points appended one at a time through
the Python API — so it happens on the first cook and whenever a parameter that
changes the geometry changes, and not otherwise. Nothing per frame goes through
here; that is the state CHOP's job, and it writes two floats per line.

Custom parameters to put on the Script SOP (Component Editor, page 'Field'):

    Fieldfile   File    fields/16x9.json
    Width       Float   1        line weight multiplier
    Markscale   Float   1        node and × size multiplier
    Rebuild     Pulse            forget the cache and build again
"""

import os
import sys

# The repo root, so `feynman` imports whether or not TD's search path knows it.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from feynman.field import Field          # noqa: E402
from feynman.ribbons import build        # noqa: E402

_cache = {"key": None, "geo": None, "field": None}


def _key(scriptOp):
    return (scriptOp.par.Fieldfile.eval(),
            round(float(scriptOp.par.Width.eval()), 4),
            round(float(scriptOp.par.Markscale.eval()), 4))


def field_of(scriptOp):
    """The loaded Field, so the state CHOP and anything else can share it."""
    _ensure(scriptOp)
    return _cache["field"]


def _ensure(scriptOp):
    key = _key(scriptOp)
    if _cache["key"] == key and _cache["geo"] is not None:
        return False
    path = scriptOp.par.Fieldfile.eval()
    if not os.path.isabs(path):
        path = os.path.join(_ROOT, path)
    field = Field(path)
    _cache["field"] = field
    _cache["geo"] = build(field,
                          width=float(scriptOp.par.Width.eval()),
                          mark_scale=float(scriptOp.par.Markscale.eval()))
    _cache["key"] = key
    return True


def onPulse(par):
    if par.name == "Rebuild":
        _cache["key"] = None
        par.owner.cook(force=True)


def cook(scriptOp):
    rebuilt = _ensure(scriptOp)
    geo = _cache["geo"]
    field = _cache["field"]

    scriptOp.clear()
    scriptOp.store("n_edges", geo["n_edges"])
    scriptOp.store("n_verts", geo["n_verts"])
    scriptOp.store("field_w", field.w)
    scriptOp.store("field_h", field.h)

    for name in ("au", "av", "eid", "kind"):
        scriptOp.createPointAttribute(name, 0.0)

    P, uv, eid, kind = geo["P"], geo["uv"], geo["eid"], geo["kind"]

    # The field is in pixels with y down; TD is y up and likes the middle at
    # the origin, so it is shifted and flipped here rather than in the shader.
    cx, cy = field.w * 0.5, field.h * 0.5
    for i in range(len(P)):
        pt = scriptOp.appendPoint()
        pt.x = float(P[i][0]) - cx
        pt.y = cy - float(P[i][1])
        pt.z = 0.0
        pt.au = float(uv[i][0])
        pt.av = float(uv[i][1])
        pt.eid = float(eid[i])
        pt.kind = float(kind[i])

    # A ribbon is a triangle strip; a mark is one quad.
    for strip in geo["strips"]:
        prim = scriptOp.appendPoly(len(strip), closed=False, addPoints=False)
        for j, pi in enumerate(strip):
            prim[j].point = scriptOp.points[pi]
    for quad in geo["quads"]:
        prim = scriptOp.appendPoly(4, closed=True, addPoints=False)
        for j, pi in enumerate(quad):
            prim[j].point = scriptOp.points[pi]

    if rebuilt:
        print(f"[feynman] built {len(P)} points, {len(geo['strips'])} lines, "
              f"{len(geo['quads'])} marks from {os.path.basename(str(path_of(scriptOp)))}")


def path_of(scriptOp):
    return scriptOp.par.Fieldfile.eval()
