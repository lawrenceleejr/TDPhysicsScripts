# TDPhysicsScripts

Feynman-diagram scenes for TouchDesigner, for VJing.

`feynman/` is the field from the US Muon Collider Collaboration's poster and
event site — [hepalumni.muoncollider.us](https://hepalumni.muoncollider.us) —
running live: a planar mesh of fermion, boson and Higgs lines with a travelling
flood lighting it a line at a time.

Every vertex in it is a legal Standard Model interaction. That is not decorative
— **ffV**, **ffh**, **ffVV**, **VVV**, **VVVV**, **hVV**, **hhVV**, **hhh**,
**hhhh** and nothing else, checked on export. A fermion line is continuous, so a
vertex carries 0 or 2 fermion legs and never an odd number. Straight
propagators, a drawn wave for the bosons, dashes for the scalars, a filled node
at every junction and an × wherever a line ends in the vacuum.

## Getting it into TouchDesigner

```
git clone https://github.com/lawrenceleejr/TDPhysicsScripts
```

Open TD, put `td/feynman_build.py` in a Text DAT, right-click → **Run Script**.
It builds `/feynman` as one Base COMP with everything inside and the parameters
worth performing with on its own page. Look at `/feynman/render`.

The paths in the build script are relative to the repo root, so either launch
TD with the repo as the project folder or edit `FIELD` and the DAT file paths to
absolute ones.

**This wiring has not been run.** There is no TouchDesigner in the environment
it was written in, so `td/` is written to the API and read carefully, not
tested. What *is* tested is everything it drives — see **Checking it without
TD** below. Expect to fix a parameter name or two; the troubleshooting section
is the first place to look.

## Performing it

On `/feynman`:

| Parameter | Does | Try |
|---|---|---|
| **Speed** | how fast a front crosses the field | on a fader; 0.2 is a slow bloom, 4 is weather |
| **Tail** | how much stays lit behind the head | 0.2 a bright snake, 0.85 a travelling wash, **past 1.0 the field fills and holds** |
| **Walkers** | fronts travelling at once | 1 reads as a sweep, 2 as breathing, 4 as weather |
| **Line width** | stroke weight | 2–3 for projection, 1 for a screen |
| **Brightness** | overall gain | the one to ride |
| **Dash** | dashes along a Higgs line | 6 is stately, 30 is a dotted rush |
| **Freeze** | holds the picture where it is | for a hard cut |
| **Fill** | lights the whole field at once | a hit on a downbeat |
| **Reseed** | throws the fronts somewhere new | a change of direction |
| **Field file** | which field | switching between `fields/*.json` is a cut to a whole new mesh |

`render` comes out with a transparent background, so it composites over
whatever else is going on.

## How it works

Two halves, split where the work is.

**The field is exported, not generated in Python.** `tools/network.js` is a copy
of the generator behind the poster and the website; `tools/export_field.mjs`
runs it and writes `fields/*.json`. Blue noise at a set spacing → Delaunay, so
the mesh is planar with no crossings → thinned so no vertex is left under three
legs → legs capped and tight angles opened → line types assigned so every
vertex is legal. Reimplementing that in Python would have meant keeping two
copies of some careful rules in step; exporting from the original means TD gets
the audited article, and the export fails loudly if a vertex is not legal.

**The flood is live**, in `feynman/field.py`, and is the website's mechanic:

- One or two walkers, each flooding the mesh from a single vertex by a
  **direction-biased Dijkstra**. A plain Dijkstra spreads as a disc; multiplying
  each edge's cost by how far it points from a slowly drifting heading makes the
  front *travel* — cheap along the heading, expensive across it.
- Every line gets an arrival distance out of that. A frame lights only the lines
  arriving inside `(head - tail, head]`, part-grown at the head and fading at
  the tail.
- A line grows out of the vertex the flood reached first, on the poster's curve:
  `1 - (1 - t) ** 5.5` — fast out of the vertex, then a long decay.
- Walkers run out of phase, so one is always mid-life while another re-seeds and
  the field never empties.

On the GPU: `feynman/ribbons.py` turns each line into a triangle strip carrying
its arc length, and each vertex into one quad. The state CHOP writes two floats
per line per frame — growth and tone — and the shader trims each line to its
growth, dashes the scalars off the same arc length, cuts the disc or the cross
out of a mark's quad, and colours by type. So the geometry is built once and
the per-frame cost is about a thousand floats, not tens of thousands of points.

## Checking it without TD

```
python3 tools/preview.py --contact out/contact.png
python3 tools/preview.py --field fields/9x16.json --frames 10 --tail 0.3
```

This renders frames with pillow instead of a GPU, off the same `feynman/`
code the TD scene runs. It is how the flood, the growth curve, the tone falloff
and the marks were checked: if a frame out of here looks like the website, the
part that matters is right and TD is only being asked to put the same numbers
on a GPU.

## New fields

```
node tools/export_field.mjs --all                    # the set in fields/
node tools/export_field.mjs --w 3840 --h 2160 --seed 12
node tools/export_field.mjs --spacing 90             # sparser mesh
```

Needs Node; nothing at run time does. Density is asked for as a share of the
short side, so a portrait field comes out at the same density as a landscape
one. The default matches the website: points 67px apart at 1080p, scale 2.8.

Shipped: `16x9`, `16x9-b`, `16x9-c` (three different meshes, same size, for
cutting between), `9x16`, `1x1`, `21x9`.

## If it breaks

- **Nothing renders.** Check `/feynman/field_geo` has points — its info popup
  should say tens of thousands. If it is empty, the field file path is wrong:
  `Fieldfile` is relative to the repo root, and TD's idea of that is the project
  folder.
- **Everything renders but nothing animates.** `/feynman/state` should show two
  channels with about a thousand samples. If it shows one sample at zero, its
  `Geo` parameter is not pointing at the Script SOP.
- **Solid blocks instead of lines.** The shader is not getting `au`/`av`, so
  every fragment thinks it is at the centre of its line. Check the Script SOP
  really made those point attributes, and that the GLSL MAT's info DAT does not
  list them as missing.
- **Lines pop in whole rather than growing.** `state_top` is 8-bit, so growth
  quantises to 256 steps and short lines jump. Set its **Data Format** to
  32-bit float.
- **Marks are squares.** `kind` is not arriving, so the quad never gets cut into
  a disc. Same check as above.
- **It builds and then stalls for seconds.** That is the Script SOP appending
  around twenty thousand points one at a time, and it only happens on a rebuild.
  Leave `Fieldfile` and `Line width` alone while performing; both force one.

## Licence and provenance

The generator in `tools/network.js` and the identity it draws are from
[lawrenceleejr/SigmaMuMuEvent](https://github.com/lawrenceleejr/SigmaMuMuEvent).
Re-copy it if it moves on there — `tools/export_field.mjs` is the only thing
that reads it.
