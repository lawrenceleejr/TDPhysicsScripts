#!/usr/bin/env python3
"""Render a field to PNG, from the same arrays TouchDesigner is handed.

    python3 data/feynman/preview.py                        # one frame
    python3 data/feynman/preview.py --frames 6 --every 90  # a contact sheet
    python3 data/feynman/preview.py --field 9x16 --tail 0.6

This is not a mock-up of the scene: it walks ``FeynmanShow.polys`` and
``FeynmanShow.colours()``, which is exactly what the Script SOP appends and
what the Script CHOP carries, so what comes out is what TD will draw, minus
the bloom. It is how the flood, the growth curve, the fade and the marks get
checked without opening TouchDesigner.

Needs pillow (``pip install -r requirements-dev.txt``).
"""

from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from physics.feynman import FeynmanShow            # noqa: E402

PAPER = (20, 19, 18)


def render(show, w, h, width=2):
    img = Image.new("RGB", (w, h), PAPER)
    dr = ImageDraw.Draw(img, "RGBA")
    rgba = show.colours()
    world_h = show.world * h / w
    n = 0
    for poly in show.polys:
        for k in range(len(poly) - 1):
            a, b = rgba[n + k], rgba[n + k + 1]
            al = min(float(a[3]), float(b[3]))
            if al <= 0.01:
                continue
            c = (a[:3] + b[:3]) * 0.5
            xy = [((float(poly[j][0]) / show.world + 0.5) * w,
                   (0.5 - float(poly[j][1]) / world_h) * h) for j in (k, k + 1)]
            dr.line(xy, width=width, fill=(int(c[0] * 255), int(c[1] * 255),
                                           int(c[2] * 255), int(al * 255)))
        n += len(poly)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="16x9")
    ap.add_argument("--out", default="out/feynman.png")
    ap.add_argument("--w", type=int, default=1600)
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--every", type=int, default=60, help="frames between shots")
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--walkers", type=int, default=3)
    ap.add_argument("--traverse", type=float, default=30.0)
    ap.add_argument("--tail", type=float, default=0.3)
    ap.add_argument("--fade", type=float, default=0.5)
    ap.add_argument("--palette", default="sigma")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--fill", action="store_true", help="light it all and hold")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, f"{args.field}.json")
    show = FeynmanShow(path, palette=args.palette, walkers=args.walkers,
                       traverse=args.traverse, tail=args.tail, fade=args.fade,
                       seed=args.seed)
    h = int(round(args.w * show.field.h / show.field.w))
    print(show.field, f"-> {len(show.polys)} polylines, {show.n_points} points")

    if args.fill:
        show.flood.fill()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    shots = []
    for i in range(max(1, args.frames)):
        if i or not args.fill:
            for _ in range(args.every if i else args.every):
                show.step(1.0 / args.fps)
        shots.append(render(show, args.w, h))
        print(f"  frame {i}: lit share {show.flood.lit_share * 100:.0f}%")

    if len(shots) == 1:
        shots[0].save(args.out)
        print(f"  -> {args.out}")
        return
    cols = 2 if len(shots) <= 4 else 3
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * args.w // 2, rows * h // 2), PAPER)
    for i, s in enumerate(shots):
        sheet.paste(s.resize((args.w // 2, h // 2)),
                    ((i % cols) * args.w // 2, (i // cols) * h // 2))
    sheet.save(args.out)
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
