# TDPhysicsScripts

A library of **live physics simulations for [TouchDesigner](https://derivative.ca)** —
built for VJ / DJ sets. Everything is dark-background, neon, glowing, and
designed so you can **pre-build every scene and flip between them instantly**.

Seven scenes, spanning physics:

| # | Scene | What it is |
|---|-------|-----------|
| 0 | **Ising** | A 2D Ising model evolving near its critical temperature — breathing magnetic domains with glowing domain walls. |
| 1 | **N-Body** | Gravitational N-body: colliding galaxies, a rotating disk, or a star cluster. Glowing points coloured by speed. |
| 2 | **Flow** | Divergence-free **curl-noise** turbulence — tens of thousands of particles swirling like smoke. |
| 3 | **Soft Body** | A shape-matched **soft body** that rotates and wobbles like jelly. |
| 4 | **LHC Tracks** | Synthetic collider events: charged tracks spiralling in a magnetic field, colour-coded by momentum, re-firing every few seconds. |
| 5 | **Open Data** | **Real CMS dimuon open data** — each event drawn as two muon tracks, coloured by invariant mass (you're literally rendering the J/ψ, Υ and Z). |
| 6 | **Feynman** | A field of Feynman diagram lines — fermions, bosons and Higgs, every vertex a legal Standard Model interaction — with a front travelling through it, drawing each line out of the vertex it reaches and letting the wake fade. |

---

## How it works

Each simulation is split in two:

* **`physics/`** — pure-numpy simulation *cores*. No TouchDesigner dependency,
  so they're fast, testable, and run unchanged inside TD's bundled Python.
* **`touchdesigner/`** — a thin adapter layer: Script TOP/CHOP/SOP **callbacks**
  plus **`td_build.py`**, which assembles entire scene networks (instancing,
  camera, render, bloom, controls) for you in one click.

You don't wire nodes by hand. You run one build script and get a finished,
parameterised, good-looking component.

### Built for live performance

* **Pre-built scenes, instant switching.** `build_all()` creates a `PhysicsVJ`
  component with all six scenes side-by-side and a `Scene` selector. Switching
  is just a parameter change — no rebuild, no hitch.
* **Only the visible scene cooks.** Each scene has an `Active` flag and a
  frame-start cook driver; idle scenes cost ~0 CPU. Flip **`Freerun All`** on
  to evolve every scene simultaneously when you want true parallelism.
* **Fast GPU handoff.** All simulation → GPU transfer uses `copyNumpyArray`
  (~0.03 ms/frame), and the heavy solvers are vectorised for 60 fps at sane
  particle counts (see [Performance](#performance)).

---

## Quick start

**Requirements:** TouchDesigner 2022+ (ships its own Python 3.11 + numpy — no
install needed to *use* these scripts).

1. **Clone the repo** somewhere on the machine running TouchDesigner:
   ```bash
   git clone <this-repo> TDPhysicsScripts
   ```
2. *(Optional)* **Export more Feynman fields** — six ship with the repo, and
   the generator behind the poster they come from is included:
   ```bash
   node data/feynman/export_field.mjs --w 2560 --h 1080 --seed 3
   ```
   It refuses to write a field with an illegal vertex in it. To look at one
   without opening TouchDesigner:
   ```bash
   python3 data/feynman/preview.py --frames 6 --every 75
   ```
3. *(Optional)* **Download real LHC data** for the Open Data scene:
   ```bash
   python3 data/fetch_opendata.py
   ```
   If you skip this, the scene uses the bundled synthetic sample (which still
   shows the J/ψ, Υ and Z peaks).
4. **In TouchDesigner**, create a **Text DAT** and paste:
   ```python
   import sys
   sys.path.insert(0, r"/full/path/to/TDPhysicsScripts")   # <-- edit this
   from touchdesigner import td_build
   td_build.build_all(op('/'))
   ```
   Right-click the DAT → **Run**. A `PhysicsVJ` component appears.
5. Open `PhysicsVJ`, view its **`out`** TOP (drag to a viewer or go to Perform
   mode), and change the **`Scene`** parameter to switch visuals — or set
   **`Nextscene`** and ride **`Crossfade`** to blend between two.

> Prefer not to edit paths? Set the environment variable
> `TD_PHYSICS_REPO=/full/path/to/TDPhysicsScripts` and use the ready-made
> launchers in `touchdesigner/builders/` — load one into a Text DAT, turn on
> *Sync to File*, and Run. They auto-locate the repo.

To build a single scene instead of all of them:
```python
td_build.build_nbody(op('/'))        # just the galaxies
td_build.build_particles(op('/'), mode='softbody')
```

---

## The controls

Every scene exposes a custom parameter page. Highlights:

**Ising** — `Temperature` (try 2.0–2.4 for the best domains; 2.269 is critical),
`Lattice Size`, `Sweeps/Frame`, `Domain Wall Glow`, `Palette`, `Reset`.

**N-Body** — `Initial Condition` (Colliding galaxies / Rotating disk / Star
cluster), `Bodies`, `G`, `Time Step`, `Softening`, `Substeps/Frame`,
`Point Size`, `Palette`, `Reset / New System`.

**Flow / Soft Body** (same callback, `Mode` switch) — `Particles`, `Flow Speed`,
`Noise Scale`, `Evolve Rate`, `Soft Body Spin`, `Point Size`, `Palette`, `Reset`.

**LHC Tracks** — `B Field (T)`, `Seconds/Event`, `Grow Time`, `World Scale`,
`Palette`, `New Collision`.

**Feynman** — two pages, because the geometry and the animation are separate
operators. On `geo/lines`: `Field` (six shipped fields — three 16:9, plus 21:9,
9:16 and square), `World Width`, `Vertex Marks`, `Rebuild`. On `state`:
`Line Lifetime` (the dial that matters — how long a line stays lit, as a share
of one traverse; 0.3 keeps the pattern turning over, 1.0 fills the frame and
holds it), `Seconds / Traverse`, `Fade Share`, `Fronts`, `Palette`, `Hold Lit`
(light everything and freeze, for a still), `New Fronts`.

**Open Data** — `Seconds/Event`, `Grow Time`, `World Scale`, `Event Order`
(by mass / random / sequential), `Palette`, `Next Event`. It also gets a
built-in **invariant-mass HUD**: a translucent log-scale histogram of the whole
dataset (J/ψ, Υ and Z marked, left→right) with a live marker on the event being
drawn. `HUD Opacity` fades it in/out; `Mass Min/Max` (on the HUD's own page)
zoom the axis.

**Motion trails** — every geometry scene (N-Body, Flow, Soft Body, LHC, Open
Data) has a `Trail (feedback)` control (0–0.99): a feedback loop that leaves
glowing tails behind moving elements. At 0 it's a clean, zero-cost passthrough;
N-Body and the particle scenes ship with it on for an instant VJ look.

**`PhysicsVJ` top level** — a DJ-style A/B crossfader:
`Scene` (deck A / instant cut), `Nextscene` (deck B), `Crossfade` (0 = A,
1 = B), a `Cut To B` button to commit a transition, and `Freerun All`. Snap
`Crossfade` for a hard cut or ride it for a smooth blend; only the decks that
actually contribute to the mix cook (so it's a single live sim except
mid-fade). Each scene also has an `Orbit` (deg/sec) camera-spin control.

Palettes: `inferno`, `magma`, `plasma`, `cyber`, `synth`, `acid`, `ice` — all
tuned to glow on black — plus `sigma`, the USMCC identity (cream linework,
vermillion scalars) that the Feynman field ships in.

---

## Performance

Defaults target 60 fps for a single active scene on a modern GPU. Tune the
`Count`/`Bodies`/`Particles` parameters if needed. Measured solver cost
(numpy, single core):

| Scene | Default size | ~Solver cost | Notes |
|-------|-------------|-------------|-------|
| Ising | 256² | ~2 ms | 512² ≈ 8 ms |
| N-Body | 600 bodies | ~12 ms | O(N²); 400–700 is the sweet spot |
| Flow | 20 000 particles | ~13 ms | grid-accelerated curl noise; scales to 40k+ |
| Soft Body | 6 000 particles | ~2 ms | very cheap |
| LHC / Open Data | — | ~0 ms | only rebuilds geometry during the grow reveal |
| Feynman | 615 lines / 12.5k points | ~1 ms | geometry built once; per frame is one numpy pass over the points |

Because only the active scene cooks, running "all seven in parallel" is free
until you turn on `Freerun All` or crossfade between scenes.

---

## Testing

The physics cores are covered by a numpy-only test suite (conservation laws,
divergence-free flow, resonance peaks, etc.):

```bash
pip install -r requirements-dev.txt
pytest -q
```

---

## Project layout

```
physics/            numpy simulation cores (no TouchDesigner dependency)
  ising.py          2D Ising model (vectorised checkerboard Metropolis)
  nbody.py          softened leapfrog N-body + galaxy/disk/cluster setups
  particles.py      curl-noise flow + shape-matched soft body + Perlin noise
  lhc_tracks.py     helical charged-track generator
  opendata.py       CMS dimuon loader + synthetic generator + event show
  feynman.py        Feynman field loader + travelling flood + point colours
  palette.py        neon colormaps (glow-on-black)
touchdesigner/
  td_build.py       run inside TD to assemble scenes
  callbacks/        Script TOP/CHOP/SOP callback sources (embedded by td_build)
  builders/         one-click launcher scripts (load into a Text DAT, Run)
data/
  dimuon_sample.csv bundled synthetic dimuon data (offline fallback)
  fetch_opendata.py downloads the real CMS dataset
  feynman/          exported Feynman fields, the generator that makes them
                    (export_field.mjs + network.js) and preview.py, which
                    renders a field to PNG without opening TouchDesigner
tests/              numpy test suite + static checks of the TD layer
docs/ARCHITECTURE.md  deeper design notes + manual wiring fallback
```

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the design rationale
and a step-by-step **manual wiring guide** if a builder ever trips on a
TouchDesigner version difference.

---

## Credits & license

Open data: **CMS Collaboration**, CERN Open Data Portal
([record 545](https://opendata.cern.ch/record/545)), released CC0.

This project is released under the MIT License (see `LICENSE`).
