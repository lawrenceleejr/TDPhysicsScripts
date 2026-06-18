# TDPhysicsScripts

A library of **live physics simulations for [TouchDesigner](https://derivative.ca)** —
built for VJ / DJ sets. Everything is dark-background, neon, glowing, and
designed so you can **pre-build every scene and flip between them instantly**.

Ten scenes, spanning physics — half pure-numpy sims, half compiled-GLSL/GPU:

| # | Scene | What it is |
|---|-------|-----------|
| 0 | **Ising** | A 2D Ising model evolving near its critical temperature — breathing magnetic domains with glowing domain walls. |
| 1 | **N-Body** | Gravitational N-body: colliding galaxies, a rotating disk, or a star cluster. Glowing points coloured by speed. |
| 2 | **Flow** | Divergence-free **curl-noise** turbulence — tens of thousands of particles swirling like smoke. |
| 3 | **Soft Body** | A shape-matched **soft body** that rotates and wobbles like jelly. |
| 4 | **LHC Tracks** | Synthetic collider events: charged tracks spiralling in a magnetic field, colour-coded by momentum, re-firing every few seconds. |
| 5 | **Open Data** | **Real CMS dimuon open data** — each event drawn as two muon tracks, coloured by invariant mass (you're literally rendering the J/ψ, Υ and Z). |
| 6 | **React-Diff** | GPU **Gray-Scott reaction-diffusion** (feedback GLSL TOP) — organic spots/stripes/mitosis that bloom and dissolve with the music. |
| 7 | **Raymarch SDF** | A compiled-shader **raymarched signed-distance field** — morphing metaballs that twist to the bass and orbit on the bar. |
| 8 | **POP Storm** | A **GPU particle storm** built with TouchDesigner's POP family (falls back to high-count curl-noise on older builds): huge numbers of particles driven by radial + turbulent forces, rim-lit by a compiled glow material. |
| 9 | **Bohmian H** | **Pilot-wave (de Broglie–Bohm) electrons in hydrogen orbitals.** A cloud sampled from \|ψ\|² flows along the guidance velocity **v = Im(∇ψ/ψ)** — electrons in m≠0 orbitals circulate the z-axis into glowing rings, real/m=0 orbitals sit nearly still, and superpositions slosh. Pick the orbital/superposition on the sim. |

Scenes 6–9 are GPU/shader-based; the whole show is **audio-reactive and
tempo-synced** (see below).

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
2. *(Optional)* **Download real LHC data** for the Open Data scene:
   ```bash
   python3 data/fetch_opendata.py
   ```
   If you skip this, the scene uses the bundled synthetic sample (which still
   shows the J/ψ, Υ and Z peaks).
3. **In TouchDesigner**, create a **Text DAT** and paste:
   ```python
   import sys
   sys.path.insert(0, r"/full/path/to/TDPhysicsScripts")   # <-- edit this
   from touchdesigner import td_build
   td_build.build_all(op('/'))
   ```
   Right-click the DAT → **Run**. A `PhysicsVJ` component appears.
4. Open `PhysicsVJ`, view its **`out`** TOP (drag to a viewer or go to Perform
   mode), and change the **`Scene`** parameter to switch visuals — or set
   **`Nextscene`** and ride **`Crossfade`** to blend between two.

> Prefer not to edit paths? Set the environment variable
> `TD_PHYSICS_REPO=/full/path/to/TDPhysicsScripts` and use the ready-made
> launchers in `touchdesigner/builders/` — load one into a Text DAT, turn on
> *Sync to File*, and Run. They auto-locate the repo.

### Command-line workflow (no copy-paste loop)

A `.toe` is a binary only TouchDesigner can write, so there's a **one-time**
in-app step; after that, rebuilding is a single shell command.

1. **Once:** in TouchDesigner, run `touchdesigner/builders/make_bootstrap.py`
   in a Text DAT. It adds an Execute DAT that builds the show on every launch
   and saves `physicsvj.toe` at the repo root.
2. **From then on**, from a terminal:
   ```bash
   ./tools/run_td.sh           # git pull, open TD, rebuild from latest files
   ./tools/run_td.sh --check   # pull, rebuild headless, print build_report.txt, quit
   ```
   Each launch is a fresh process, so it always picks up the newest code — no
   module-cache dance. `--check` writes/prints **`build_report.txt`** (TD version,
   the `[td_build]` log, and every operator error) — paste that one report when
   something looks off and it pinpoints the exact node. Override the TD binary
   with `TOUCHDESIGNER_APP=...` if it's not at the default macOS path.

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
tuned to glow on black.

---

## Audio-reactive & tempo-synced

`build_all` drops two engine components beside the scenes:

**`Reactor`** — the DJ feed. An **Audio Device In CHOP** (pick your interface
on its `source` node) → a numpy analyser that emits smoothed, normalised
control channels: `bass`, `mid`, `high`, `level`, `beat` (adaptive onset
detector), `bpm`. It also builds **waveform** and **spectrum** row textures for
the visualiser. The DSP core lives in `physics/audio.py` (pure numpy,
unit-tested).

**`Tempo`** — a tempo engine that **follows an incoming MIDI beat clock**
(24 PPQN) via a MIDI In DAT, or **free-runs on a manual BPM** when no clock is
present. Outputs `bpm`, `beat`/`bar` phase ramps, a beat-locked `sine` LFO and
a `pulse` trigger. Set the MIDI device id on the `Tempo/clockin` node (match
TD's MIDI Device Mapper). Manual BPM always works, so visuals lock even with no
clock plugged in.

Both feed the show automatically: a tasteful set of scene parameters (Ising
temperature, N-Body gravity, Flow speed/evolve, soft-body spin) are bound to
the audio so the visuals **evolve on their own**, and every GLSL scene + the
post chain take audio/tempo uniforms. (The bindings deliberately avoid the
parameters the APC faders own, so you keep manual control of those.)

**Creative waveform visualiser** — toggle **`Wavevis`** on `PhysicsVJ` to
composite a GLSL oscilloscope + radial spectrum "iris" over the live scene.

## GPU, shaders & POPs

- **Compiled GLSL everywhere it counts.** Reaction-diffusion, the raymarched
  SDF, the waveform overlay and the master **post-FX** chain (beat punch,
  chromatic aberration, optional kaleidoscope, scanline shimmer, vignette) are
  all GLSL TOPs. Shader source lives in `touchdesigner/shaders/` — readable,
  hot-swappable `.frag`/`.vert` files, not buried in nodes.
- **`Look` page** on `PhysicsVJ`: `Kaleido`, `RGB Shift`, `Beat Punch`.
- **POP Storm.** Scene 8 uses TouchDesigner's **POP** family (GPU-resident 3D
  operators) for very large, organic, force-driven particle counts, rim-lit by
  a compiled glow material (`glow_mat.vert`/`.pixel`) under a 3-point light rig.
  POPs need **TouchDesigner 2023.30000+** (officially 2024+); on older builds
  the scene automatically falls back to a high-count curl-noise particle system
  so it always renders.

> Heads-up: the shader/POP layer is built to standard TD conventions but hasn't
> been run on hardware here — see the note at the end of this section in
> `docs/ARCHITECTURE.md` for the handful of version-sensitive spots to glance at
> on first load (GLSL-TOP uniform slots, POP operator/parameter names, MIDI
> realtime-clock delivery).

---

## Run the whole show from an APC mini mk2

`build_all` also drops an **`APCShow`** component that turns an
[Akai APC mini mk2](https://www.akaipro.com/apc-mini-mk2) into a hands-on
control surface for the entire set — and lights its RGB pads to mirror the live
state. (It's built automatically; pass `apc=False` to `build_all`, or run
`td_build.build_apc(op('/'))` / the `build_apcshow.py` launcher to add it to an
existing `PhysicsVJ`.)

**Set up the device:** open TouchDesigner's **MIDI Device Mapper**, map your APC
mini mk2 to a device id, and set the same id in `APCShow`'s **`Device`**
parameter (default `1`). Point **`Target`** at your show (default `../PhysicsVJ`).

**The control map** (factory mode, MIDI channel 1):

| Control | Does |
|---------|------|
| **8×8 grid** | Columns 0–7 = the first eight scenes, rows 0–6 = the seven palettes. Press a pad to **instant-cut** to that scene *and* set its palette. Pads glow in each palette's signature colour; the live scene's column is bright and its active-palette pad **pulses**. |
| **Track buttons 1–6** (below grid) | Arm a scene onto **deck B** (`Nextscene`) — the armed one blinks. |
| **Track button 7** | **Cut** — commit the crossfade B→A (lit while a fade is in progress). |
| **Track button 8** | **Freerun All** toggle (lit while on). |
| **Scene button 1** (top-right) | **Reset** the controller — re-handshake and repaint every LED. |
| **Scene button 2** | **Re-fire** the live scene (new collision / next event / reseed). |
| **Scene buttons 3–4** | Launch scenes past the 8-wide grid (**POP Storm** = 8, **Bohmian H** = 9). |
| **Master fader (9)** | **Crossfade** A/B. |
| **Faders 1 / 2 / 3** | Live scene **Trail / Orbit / Point Size**. Faders 4–8 are free. |

**The reset mechanism.** APC controllers come up dark, can be hot-plugged, and
their LEDs drift out of sync if the show is also driven from the mouse. The
top-right **Reset** pad (and the `Reset` parameter) blanks every LED and
repaints the full state in one shot — your safety net mid-set. Changing the
`Device` id re-routes MIDI and resyncs automatically.

---

## Performance

Defaults target 60 fps for a single active scene on a modern GPU. Tune the
`Count`/`Bodies`/`Particles` parameters if needed. Measured solver cost
(numpy, single core):

| Scene | Default size | ~Solver cost | Notes |
|-------|-------------|-------------|-------|
| Ising | 256² | ~2 ms | 512² ≈ 8 ms |
| N-Body | 600 bodies | ~12 ms | O(N²); 400–700 is the sweet spot. Softening raised to 0.12/0.15 so mergers stay stable for a whole set |
| Flow | 20 000 particles | ~9 ms | grid-accelerated curl noise; the field rebuilds every other frame |
| Soft Body | 6 000 particles | ~2 ms | unconditionally-stable position-based shape matching |
| LHC / Open Data | — | ~0 ms | only rebuilds geometry during the grow reveal |
| React-Diff / SDF | GPU | — | feedback / raymarch GLSL TOPs; cost is on the GPU |
| POP Storm / Bohmian H | GPU / CPU-seeded | — | POPs on the GPU; Bohmian electrons integrated on CPU (~1.8 ms/20k) |

Because only the active scene cooks, running all ten in parallel is free until
you turn on `Freerun All` or crossfade between scenes.

**The look pipeline.** Each scene gets a *selective* bloom (a brightness
highpass before the blur, so only bright neon sources glow rather than a hazy
full-frame smear), then the master GLSL post chain applies beat-driven exposure,
**ACES filmic tonemapping** (HDR neon rolls off to white instead of clipping
flat on drops), chromatic aberration, optional kaleidoscope, vignette and
dithering (to kill 8-bit banding). The raymarched SDF scene adds soft shadows +
ambient occlusion so the forms read as sculpted, not flat.

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
physics/            numpy simulation + DSP cores (no TouchDesigner dependency)
  ising.py          2D Ising model (vectorised checkerboard Metropolis)
  nbody.py          softened leapfrog N-body + galaxy/disk/cluster setups
  particles.py      curl-noise flow + shape-matched soft body + Perlin noise
  lhc_tracks.py     helical charged-track generator
  opendata.py       CMS dimuon loader + synthetic generator + event show
  hydrogen.py       hydrogen orbitals + de Broglie-Bohm guidance dynamics
  audio.py          audio analyser + beat tracker + MIDI-clock tempo follower
  palette.py        neon colormaps (glow-on-black)
touchdesigner/
  td_build.py       run inside TD to assemble scenes
  callbacks/        Script TOP/CHOP/SOP callback sources (embedded by td_build)
  shaders/          GLSL shader source (.frag/.vert) for the GPU scenes + FX
  builders/         one-click launcher scripts (load into a Text DAT, Run)
data/
  dimuon_sample.csv bundled synthetic dimuon data (offline fallback)
  fetch_opendata.py downloads the real CMS dataset
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
