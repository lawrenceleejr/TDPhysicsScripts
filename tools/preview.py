#!/usr/bin/env python3
"""Render frames of the field to PNG, without TouchDesigner.

    python3 tools/preview.py                          # 6 frames of fields/16x9.json
    python3 tools/preview.py --field fields/9x16.json --frames 10
    python3 tools/preview.py --contact out/contact.png

The point of this is that feynman/field.py can be checked on its own. The
TouchDesigner side is wiring — a Script SOP, a Script CHOP, a shader — and it
cannot run here; the flood, the growth curve, the tone falloff and the marks
can, and this is what draws them. If a frame out of here looks like the website
then the part that matters is right, and TD is only being asked to put the same
numbers on a GPU.

Needs pillow and numpy.
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from feynman.field import Field, Flood, FERMION, BOSON, SCALAR   # noqa: E402

# The identity, read as a negative: near-black stock, warm bone ink, one hotter
# vermillion for the scalars.
PAPER = (20, 19, 18)
INK = (239, 233, 218)
ACCENT = (255, 82, 48)

# Per type: colour, alpha and weight, exactly as design/network.js strokes them.
STYLE = {
    FERMION: (INK, 0.88, 1.35),
    BOSON:   (INK, 0.72, 1.15),
    SCALAR:  (ACCENT, 0.85, 1.35),
}


def trim(poly, frac):
    """The first `frac` of a polyline, by segment count, as drawEdge strokes it."""
    total = len(poly) - 1
    reach = total * max(0.015, min(1.0, frac))
    whole = int(np.floor(reach))
    out = [tuple(poly[0])]
    for i in range(1, min(whole, total) + 1):
        out.append(tuple(poly[i]))
    if whole < total:
        u = reach - whole
        p, q = poly[whole], poly[whole + 1]
        out.append((p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u))
    return out


def dashes(pts, on):
    """Split a polyline into dashed runs, `on` units on and `on` units off."""
    runs, cur, carried, drawing = [], [pts[0]], 0.0, True
    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        seg = float(np.hypot(x1 - x0, y1 - y0))
        t = 0.0
        while seg - t > 1e-6:
            room = on - carried
            step = min(room, seg - t)
            t += step
            carried += step
            px = x0 + (x1 - x0) * (t / seg)
            py = y0 + (y1 - y0) * (t / seg)
            if drawing:
                cur.append((px, py))
            if carried >= on - 1e-9:
                if drawing and len(cur) > 1:
                    runs.append(cur)
                drawing = not drawing
                cur = [(px, py)]
                carried = 0.0
    if drawing and len(cur) > 1:
        runs.append(cur)
    return runs


def draw(field, flood, scale=1.0):
    W, H = int(field.w * scale), int(field.h * scale)
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img, "RGBA")

    def mix(rgb, a):
        return (rgb[0], rgb[1], rgb[2], int(max(0, min(1, a)) * 255))

    for i in range(field.n_edges):
        tone = float(flood.tone[i])
        grow = float(flood.grow[i])
        if tone < 0.02 or grow <= 0.0:
            continue
        poly = field.polys[i]
        if flood.rev[i]:
            poly = poly[::-1]
        pts = trim(poly, grow)
        if len(pts) < 2:
            continue
        pts = [(x * scale, y * scale) for x, y in pts]
        rgb, alpha, weight = STYLE[int(field.etype[i])]
        s = float(field.escale[i])
        lw = max(1, int(round(weight * (0.8 + 0.3 * s) * scale)))
        col = mix(rgb, alpha * tone)
        if int(field.etype[i]) == SCALAR:
            for run in dashes(pts, 6.5 * s * scale):
                d.line(run, fill=col, width=lw, joint="curve")
        else:
            d.line(pts, fill=col, width=lw, joint="curve")

    # One mark per vertex: a filled node at a junction, an × where a line ends
    # in the vacuum.
    s = float(field.scale)
    rr = 1.8 * (0.72 + 0.5 * s) * scale
    q = 3.4 * (0.72 + 0.5 * s) * scale
    lw = max(1, int(round(1.3 * (0.8 + 0.3 * s) * scale)))
    for v in range(len(field.verts)):
        tone = float(flood.vtone[v])
        if tone < 0.02:
            continue
        x, y = float(field.verts[v][0]) * scale, float(field.verts[v][1]) * scale
        if field.is_x[v]:
            d.line([(x - q, y - q), (x + q, y + q)], fill=mix(INK, 0.88 * tone), width=lw)
            d.line([(x + q, y - q), (x - q, y + q)], fill=mix(INK, 0.88 * tone), width=lw)
        else:
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=mix(INK, 0.92 * tone))
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="fields/16x9.json")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--dt", type=float, default=2.5, help="seconds between frames")
    ap.add_argument("--traverse", type=float, default=34.0)
    ap.add_argument("--tail", type=float, default=0.85)
    ap.add_argument("--walkers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--scale", type=float, default=0.75)
    ap.add_argument("--out", default="out")
    ap.add_argument("--contact", default=None, help="also write a contact sheet here")
    args = ap.parse_args()

    field = Field(args.field)
    flood = Flood(field, walkers=args.walkers, traverse=args.traverse,
                  tail=args.tail, seed=args.seed)
    print(f"  {field}")
    os.makedirs(args.out, exist_ok=True)

    shots = []
    for n in range(args.frames):
        img = draw(field, flood, args.scale)
        path = os.path.join(args.out, f"frame_{n:02d}.png")
        img.save(path)
        lit = int((flood.tone > 0.02).sum())
        growing = int(((flood.grow > 0) & (flood.grow < 0.999)).sum())
        print(f"   frame {n:2}  t={n * args.dt:5.1f}s  "
              f"{lit:4} of {field.n_edges} lines lit, {growing:3} still growing"
              f"  -> {path}")
        shots.append(img)
        flood.advance(args.dt)

    if args.contact and shots:
        cols = 3
        rows = (len(shots) + cols - 1) // cols
        w, h = shots[0].size
        sheet = Image.new("RGB", (w * cols, h * rows), PAPER)
        for i, im in enumerate(shots):
            sheet.paste(im, ((i % cols) * w, (i // cols) * h))
        os.makedirs(os.path.dirname(args.contact) or ".", exist_ok=True)
        sheet.save(args.contact)
        print(f"  contact sheet -> {args.contact}")


if __name__ == "__main__":
    main()
