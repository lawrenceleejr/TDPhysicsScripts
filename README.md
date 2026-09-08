# TDPhysicsScripts

[![tests](https://github.com/lawrenceleejr/TDPhysicsScripts/actions/workflows/ci.yml/badge.svg)](https://github.com/lawrenceleejr/TDPhysicsScripts/actions/workflows/ci.yml)

A library of **live physics simulations for [TouchDesigner](https://derivative.ca)** —
built for VJ / DJ sets. Everything is dark-background, neon, glowing, and
designed so you can **pre-build every scene and flip between them instantly**.

Eleven scenes, spanning physics — half pure-numpy sims, half compiled-GLSL/GPU:

| # | Scene | What it is |
|---|-------|-----------|
| 0 | **Ising** | A 2D Ising model evolving near its critical temperature — breathing magnetic domains with glowing domain walls. |
| 1 | **N-Body** | Gravitational N-body: colliding galaxies, a rotating disk, or a star cluster. PBR spheres under a low-key rig and a studio environment light, with a soft shadow, contact occlusion and depth of field, coloured by speed. |
| 2 | **Flow** | Divergence-free **curl-noise** turbulence — tens of thousands of lit particles swirling like smoke, sinking into haze with depth. **Punch** it for an outward blast. |
| 3 | **Soft Body** | A shape-matched **soft body** that rotates and wobbles like jelly on a dark glossy stage, its shadow landing beneath it. **Punch** it and a shock wave barrels through. |
| 4 | **LHC Tracks** | Synthetic collider events: charged tracks spiralling in a magnetic field, colour-coded by momentum, re-firing every few seconds. |
| 5 | **Open Data** | **Real CMS dimuon open data** — each event drawn as two muon tracks, coloured by invariant mass (you're literally rendering the J/ψ, Υ and Z). |
| 6 | **React-Diff** | GPU **Gray-Scott reaction-diffusion** (feedback GLSL TOP) — organic spots/stripes/mitosis that bloom and dissolve with the music. |
| 7 | **Raymarch SDF** | A compiled-shader **raymarched signed-distance field** with four forms — metaballs, a gyroid lattice, a kaleidoscopic fractal, a torus knot — and `Speed`, `Twist`, `Zoom`, `Detail`, `Morph` dials; soft shadows and ambient occlusion, twisting to the bass. |
| 8 | **Storm** | A dense **particle storm**: 40 000 curl-noise particles rendered as an additive cloud with long trails. (TouchDesigner's POP family is wired as an opt-in path.) **Punch** for a blast. |
| 9 | **Bohmian H** | **Pilot-wave (de Broglie–Bohm) electrons in hydrogen orbitals.** A cloud sampled from \|ψ\|² flows along the guidance velocity **v = Im(∇ψ/ψ)** — rendered as tens of thousands of tiny additive points under wide bloom and long trails, so m≠0 orbitals draw glowing rings, real/m=0 orbitals sit nearly still, and superpositions slosh. Pick the orbital/superposition on the sim. |
| 10 | **Feynman** | A field of Feynman diagram lines — fermions, bosons and Higgs, every vertex a legal Standard Model interaction — with a front travelling through it, drawing each line out of the vertex it reaches and letting the wake fade. |

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
  component with all eleven scenes side-by-side and a `Scene` selector.
  Switching is just a parameter change — no rebuild, no hitch.
* **Only the visible scene cooks.** Each scene has an `Active` flag and a
  frame-start cook driver; idle scenes cost ~0 CPU. The armed deck-B scene is
  free too until you actually start the crossfade (an idle deck points at the
  live scene, so nothing renders twice). Flip **`Freerun All`** on to evolve
  every scene simultaneously when you want true parallelism.
* **Fast GPU handoff.** All simulation → GPU transfer uses `copyNumpyArray`
  (~0.03 ms/frame), and the heavy solvers are vectorised for 60 fps at sane
  particle counts (see [Performance](#performance)).

---

## Quick start

**Requirements:** TouchDesigner 2022+ on macOS or Windows. TD ships its own
Python (3.9 in 2022, 3.11 from 2023) with numpy — there is **nothing to
install** to use these scripts inside TD.

1. **Clone the repo** somewhere on the machine running TouchDesigner:
   ```bash
   git clone https://github.com/lawrenceleejr/TDPhysicsScripts.git
   cd TDPhysicsScripts && pip install -r requirements-dev.txt && pytest -q   # optional: check the maths on this machine
   ```
2. **One time, in TouchDesigner:** create a Text DAT, set its *File* to
   `touchdesigner/builders/make_bootstrap.py`, turn *Sync to File* on, and
   right-click → **Run**. That builds the whole show now *and* saves a tiny
   `physicsvj.toe` at the repo root whose only job is to rebuild the show from
   the current files every time it opens.
3. **From then on, from a terminal:**
   ```bash
   ./tools/run_td.sh           # git pull, open TD, rebuild from latest files
   ./tools/run_td.sh --check   # pull, rebuild headless, print build_report.txt, quit
   ```
4. **Drive it.** The controls are *parameters on the `PhysicsVJ` node* at the
   root of the network (the launcher leaves it selected for you): click
   `PhysicsVJ` once and its parameter dialog appears top-right, or press
   **P** with it selected. On its **PhysicsVJ** page, **`Scene`** is a menu —
   pick a scene to cut to it; set **`Nextscene`** and drag **`Crossfade`** to
   blend between two. Its **`out`** TOP is the master picture: middle-click
   the node to view it, or go to Perform mode.

Prefer to skip the launcher? Paste this into a Text DAT and Run it:
```python
import sys
sys.path.insert(0, r"/full/path/to/TDPhysicsScripts")   # <-- edit this
from touchdesigner import td_build
td_build.build_all(op('/'))
```
Or set the environment variable `TD_PHYSICS_REPO=/full/path/to/TDPhysicsScripts`
and use any launcher in `touchdesigner/builders/` (load into a Text DAT, turn
on *Sync to File*, Run) — they auto-locate the repo.

**Optional extras**

* **Real LHC data** for the Open Data scene (otherwise the bundled synthetic
  sample is used, which still shows the J/ψ, Υ and Z peaks):
  ```bash
  python3 data/fetch_opendata.py
  ```
* **More Feynman fields** — six ship with the repo; the generator behind the
  poster they come from is included and refuses to write a field with an
  illegal vertex in it. Preview one without opening TouchDesigner:
  ```bash
  node data/feynman/export_field.mjs --w 2560 --h 1080 --seed 3
  python3 data/feynman/preview.py --frames 6 --every 75
  ```

### Command-line workflow

A `.toe` is a binary only TouchDesigner can write, so step 2 above is the one
in-app step; after that, rebuilding is a single shell command and every launch
is a fresh process, so it always picks up the newest code — no module-cache
dance.

```bash
./tools/run_td.sh                    # open TD with the full show
./tools/run_td.sh --check            # headless: build, write + print build_report.txt, quit
./tools/run_td.sh --scene nbody      # build just one scene (any td_build.build_<name>)
./tools/run_td.sh --check --scene feynman
```

`--check` writes **`build_report.txt`** (TD version, the `[td_build]` log, and
every operator error or warning) and exits **0** if the build finished
cleanly, **2** if it raised or stalled, **3** if TD never ran the bootstrap —
so it works from a script or a pre-show checklist. Paste that one report when
something looks off and it pinpoints the exact node.

The launcher finds TouchDesigner in `/Applications` on macOS and under
`Program Files\Derivative` when run from Git Bash on Windows; override with
`TOUCHDESIGNER_APP=/path/to/binary`. `PHYSICSVJ_NO_PULL=1` skips the
`git pull`.

To build a single scene instead of all of them:
```python
td_build.build_nbody(op('/'))        # just the galaxies
td_build.build_particles(op('/'), mode='softbody')
```

---

## The controls

Every scene exposes a custom parameter page. Highlights:

**Ising** — `Temperature` (try 2.0–2.4 for the best domains; 2.269 is critical),
`Lattice Size`, `Sweeps/Frame`, `Domain Wall Glow`, `Invert Domains` (on by
default: which spin direction is the lit one), `Palette`, `Reset`.

**N-Body** — `Initial Condition` (Colliding galaxies / Rotating disk / Star
cluster), `Bodies`, `G`, `Time Step`, `Softening`, `Substeps/Frame`,
`Point Size`, `Palette`, `Reset / New System`. Lit by **one soft-edged
spotlight and nothing else** — no ambient, no fill, no environment — so a body
is only visible while it is inside the beam, and the beam sweeps slowly so
bodies drift through it on their own.

**Flow / Soft Body / Storm** (same callback, `Mode` switch) — `Particles`,
`Flow Speed`, `Noise Scale`, `Evolve Rate`, `Soft Body Spin`, `Point Size`,
`Palette`, `Punch Strength` (1.8 by default, up to 4), `Punch`, `Reset`.
**Punch** is the hit: a shock wave rolling through the soft body, an outward
blast through the flow and the storm. On the soft body it is heavy — the
wavefront displaces by more than a radius and decays slowly, so it visibly
barrels through and the body wobbles back into shape after it. It fires from the sim's pulse, the APC's re-fire button, a **left
click anywhere** or the **space bar** (toggle `Click / Space = Punch` on
`PhysicsVJ`), or `PhysicsVJ`'s own `Punch` pulse.

**Reaction-Diffusion** — `Feed Rate`, `Kill Rate`, `Invert Field` (on by
default: the background becomes the lit body and the spots the holes in it),
`Palette`, `Reseed`.

**Raymarch SDF** — `Form` (metaballs / gyroid lattice / IFS fractal / torus
knot; `Next Form` steps through them), `Speed`, `Twist`, `Zoom`, `Detail`,
`Morph`, `Palette`.

**LHC Tracks** — `B Field (T)`, `Seconds/Event`, `Grow Time`, `World Scale`,
`Palette`, `New Collision`. It carries a **detector readout**: the
highest-momentum tracks get a tick, a dog-leg leader out to a packed label
column, and a line of numbers (`TRK01 PT  42.5 GEV Q+`) under an event header.
The labels are drawn as *lines* in a stroke font (`physics/vecfont.py`), so
they render through the same camera and bloom as the tracks and stay crisp at
any zoom; they live in their own un-orbited geometry, so they stay upright
while the event turns and each leader stays pinned to its track. On the
readout's page: `Labelled Tracks`, `Label Column`, `Readout Size`, `Show
Readout`.

**Feynman** — scalar (Higgs) lines are drawn **dashed**, per convention;
fermions solid, bosons wavy. Two pages, because the geometry and the animation
are separate operators. On `geo/lines`: `Field` (six shipped fields — three 16:9, plus 21:9,
9:16 and square), `World Width`, `Vertex Marks`, `Rebuild`. On `state`:
`Line Lifetime` (the dial that matters — how long a line stays lit, as a share
of one traverse; 0.4 keeps the pattern turning over, 1.0 fills the frame and
holds it), `Seconds / Traverse` (45 by default: slow), `Growth (line lengths)`
(the creep — how far the front travels while a line draws itself; 6 lets each
line be watched growing, 2 pops them out), `Fade Share`, `Fronts`, `Palette`,
`Hold Lit` (light everything and freeze, for a still), `New Fronts`.

**Open Data** — `Seconds/Event`, `Grow Time`, `World Scale`, `Event Order`
(by mass / random / sequential), `Events Kept` (earlier events stay on screen,
dimming with age, so the frame reads as an event display), `Palette`, `Next
Event`. It also gets a
built-in **invariant-mass HUD**: a translucent log-scale histogram of the whole
dataset (J/ψ, Υ and Z marked, left→right) with a live marker on the event being
drawn. `HUD Opacity` fades it in/out; `Mass Min/Max` (on the HUD's own page)
zoom the axis.

**Bohmian H** — `Orbital / Superposition`, `Flow Speed` (1.1 by default — the
guidance flow is slow and legible now), `Orbital Morph (s)` (8 s: choosing a
new orbital does not cut, the cloud is handed a coherent superposition of
where it was and where it is going and the weight crosses over, so it
reshapes itself through real hydrogen states), `Depth Cue` (brightness and
point size follow depth, which is what gives the additive cloud its volume),
`Respawn / sec`, `Substeps/Frame`, `Palette`.

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
Performance controls on the same page: `Punch` (hit the live scene), `Freeze`
(stop every sim on its current frame), `Blackout`, **`Audio Reactive`** (off =
the visuals ignore the music entirely; the APC's Mute pad flips it) with
`Reactive Amount` (0.6 by default — subtle; 1 is the full light show), and
**`Title`** — the live
scene's physics title (**GRAVITY**, **PHASE TRANSITION**, **PROTON
COLLISION**, …) soaks onto the screen in big caps like ink on wet paper, holds
for `Title Hold` seconds, then fades. The **T** key does the same. An **`FX`**
page holds sixteen whole-screen effect toggles (see the APC map below).

Palettes: `inferno`, `magma`, `plasma`, `cyber`, `synth`, `acid`, `ice` — all
tuned to glow on black — plus `sigma`, the USMCC identity (cream linework,
vermillion scalars) that the Feynman field ships in.

---

## Audio-reactive & tempo-synced

`build_all` drops two engine components beside the scenes:

**`Reactor`** — the DJ feed. An **Audio Device In CHOP** (pick your interface
on its `source` node) → a numpy analyser that emits control channels: `bass`,
`mid`, `high`, `level` (smoothed bands), `beat` (a flash on each kick with a
smooth exponential tail), `kick` (how hard the last kick hit), `pulse` (a
smooth 0–1 pulsation peaking on each predicted beat — bind this for breathing
rather than flashing) and `bpm`. **Nothing to trim:** `Auto Levels` (on by
default) normalises every band by its own slow-decaying peak, so a quiet feed
and a hot one drive the show identically, and the **kick tracker** finds EDM
kicks on its own — spectral-flux onsets in the low band against an adaptive
threshold, a refractory period, and tempo gating (90–180 BPM, folded) that
rejects off-beat rumble while locking to the kick's period. `Reset Levels`
forgets the history (new set, new room), `Hit` is a manual beat, `Mute` stops
the show reacting. The DSP core lives in `physics/audio.py` (pure numpy,
unit-tested against a synthetic 128 BPM kick track: recall > 85 %, phantom
beats < 15 %, tempo within ±4 BPM). It also builds **waveform** and
**spectrum** row textures for the visualiser.

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

- **Lit like a render, not a viewport.** Every rasterised 3D scene (N-Body,
  Flow, Soft Body, LHC, Open Data) is finished by a **cinema pass**
  (`cinema.frag`): screen-space **ambient occlusion** where bodies meet,
  thin-lens **depth of field** about a `Focus` plane, and a cold atmospheric
  **haze** with distance. The lit scenes use a **PBR material** under a
  **low-key rig** — a strong warm key with a soft shadow, a fill kept cold and
  dim, a hard rim that pulses with the beat, a coloured practical that slowly
  circles the scene — plus an **Environment Light** fed by a procedural HDR
  studio (`studio_env.frag`: a big warm softbox, a cool strip light behind, a
  faint floor bounce) so glossy bodies reflect real-looking softbox highlights.
  The soft body sits on a dark glossy stage that catches the shadow. Each scene
  has `Cinema`, `Focus`, `Depth of Field` and `Haze` on its VJ page; `Cinema`
  off leaves the raw render, so a shader that fails to compile on a new build
  can never black out a scene.
- **Compiled GLSL everywhere it counts.** Reaction-diffusion, the raymarched
  SDF, the waveform overlay and the master **post-FX** chain (beat punch,
  chromatic aberration, optional kaleidoscope, scanline shimmer, vignette) are
  all GLSL TOPs. Shader source lives in `touchdesigner/shaders/` — readable,
  hot-swappable `.frag`/`.vert` files, not buried in nodes.
- **`Look` page** on `PhysicsVJ`: `Kaleido`, `RGB Shift`, `Beat Punch`,
  **`Dark Mode`** (on by default: crushed blacks, a heavier mid gamma, a
  tighter vignette and `Exposure` 0.85, so every scene reads as dark-mode UI
  rather than a bright screen) and a **`Post FX`** toggle — off routes the raw
  scene mix straight to `out`, so a shader problem on a new TouchDesigner
  build can never black out a show.
  The **`FX` page** is sixteen whole-screen toggles the same shader applies:
  Invert, Edges (Sobel, in the scene's own colour), Posterize, Pixelate,
  Mirror, Mono, Solarize, Fisheye, Tiles, Shake, ASCII (5×5 glyph cells by
  brightness), Blur, Kaleido, RGB Boost, Strobe, Negative-on-beat.
- **Ink title** (`ink_title.frag`): a Text TOP renders the scene's title,
  the shader roughens its edges with noise, grows a halo into tendrils as the
  ink soaks in, and fades it out on release. It sits after the FX bypass, so
  it works with post FX off, and a Switch keeps it from cooking between titles.
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

## The operator's view

`build_all` also drops a **`Dashboard`** COMP at the top level. View its `out`
on your laptop and you get the show as it goes out beside the control map:

- **Left two thirds** — the **program feed**: a Select TOP on the very same
  `PhysicsVJ/out` the second display is sent, so what you are watching is the
  program and not a second render of it.
- **Right column** — the **APC cheatsheet** (`docs/apc_map.png`), so a pad you
  have forgotten is a glance away.
- `Program / Map Split` moves the divider; `Show APC Map` off gives the plain
  program feed full frame.

Beside it is a **`program_window`** Window COMP pointed at the same TOP, set to
**monitor 2**; the dashboard's `Open Program Window` pulse opens it.

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

**The control map** (factory mode, MIDI channel 1). `python tools/apc_map.py`
draws it from the controller's own tables:

![APC mini mk2 control map](docs/apc_map.svg)

The 8×8 grid is **four quadrants**:

| Quadrant | Does |
|----------|------|
| **Upper-left: actions** (white; yellow while on) | Induce things in the live scene. **Punch** / **Big Punch** send a shock through the soft body, flow and storm (scenes without a punch re-fire instead). **Re-fire** = new collision / next event / reseed. **Reset** restarts the sim. **Pulse** is a manual beat — and its pad flashes on every kick the reactor hears, so you can see the beat tracking. **Freeze** stops every sim; **Hold** is the Feynman "hold lit" (Freeze elsewhere). **Title** shows the scene's physics title: tap for a few seconds, hold to keep it up. **Trail Max** maxes the trails while held; **Orbit Flip** reverses the camera spin; **Variant** steps the scene's own menu (initial condition, orbital, form, event order, field); **Palette >** next palette. **Scene < / >**, **Blackout**, **Strobe** (while held). |
| **Upper-right: whole-screen FX** (purple; green while on) | Sixteen toggles applied by the master shader — invert, Sobel edges, posterize, pixelate, mirror, mono, solarize, fisheye, tiles, shake, ASCII, blur, kaleidoscope, RGB boost, strobe, negative-on-beat. Pads that are on breathe with the beat. **Shift + any FX pad clears them all.** |
| **Lower-left: scene queue** | The eleven scenes in reading order, lit in their palette's colour. Press to **arm** a scene on deck B (it blinks), then ride the master fader or hit **Cut**; **Shift + pad** cuts straight to it. The live scene's pad breathes with the level and jumps on each kick. |
| **Lower-right: palettes + tools** | Rows 3–2: the eight palettes for the live scene (bright = current). Row 1, tempo engine: **Tap** tempo (the pad ticks red with the tempo's phase), **Sync** (re-anchor the beat phase + fire a beat), **BPM ×2 / ÷2**. Row 0, audio reactor: **Auto Reset** (forget the level history), **Sens − / +**, **Mute** (red while on). |

| Control | Does |
|---------|------|
| **Scene buttons** (round, right column) | **Scene select:** button *k* cuts to scene *k* (Ising … Raymarch); **Shift + button 1–3** cuts to POP Storm, Bohmian H, Feynman. Lit = live, blinking = armed. Holding Shift shows the shifted layer. |
| **Track buttons** (round, below grid) | Title · Freeze · Punch · Tap · **Clear FX** · Palette > · **Cut** (lit mid-fade) · **Freerun All**. With **Shift**: **Reset LEDs** · Re-fire · Big Punch · Sync · Hold · Variant · Scene < · Scene >. |
| **Faders 1–8** | The live scene's own parameters. 1–3 are always **Trail / Orbit / Point Size** where the scene has them; 4–8 are the physics: Ising temperature + wall glow + sweeps; N-Body G + time step + softening + substeps; Flow/Storm speed + noise scale + evolve + punch strength; Soft Body spin + punch strength; LHC B-field + event period + grow time + scale; Open Data event period + grow time + scale + events kept + HUD opacity; RD feed + kill; SDF speed + twist + zoom + detail + morph; Hydrogen speed + refresh + substeps; Feynman lifetime + traverse + growth + fade + fronts (`FADER_MAP` in `apc_mini.py`). |
| **Master fader (9)** | **Crossfade** A/B. |
| **Shift** (bottom-right) | Held: scene pads cut, FX pads clear all, the round buttons switch to their second layer. |

**Live LEDs.** An Execute DAT drives the surface every frame: the live scene's
pad breathes with the audio level and jumps to full on each kick, **Pulse**
flashes yellow on every detected kick (your beat-tracking meter), **Tap** ticks
red with the tempo engine's phase, FX pads that are on breathe with the beat,
and Strobe strobes the whole grid while held. Presses fire **grid animations**
so you can feel a hit land without looking at the screen: Punch ripples white
outward from the pad, Big Punch flashes the grid and fades, Re-fire / Reset
wipe across in the live palette colour, Title curtains down from the top,
Palette sparkles, Freeze flashes ice-blue, Blackout darkens the grid and
returns, and any scene cut ripples out in the new scene's colour. All of this
rides on by-difference sending, so a quiet frame costs zero MIDI bytes.

**The reset mechanism.** APC controllers come up dark, can be hot-plugged, and
their LEDs drift out of sync if the show is also driven from the mouse. The
**Shift + track button 1** (and the `Reset` parameter) blanks every LED and
repaints the full state in one shot — your safety net mid-set. Changing the
`Device` id re-routes MIDI and resyncs automatically. In normal use LEDs are
sent *by difference* (only pads whose state changed), so riding a fader costs
a couple of MIDI messages a frame rather than ninety.

---

## Performance

Defaults target 60 fps for a single active scene on a modern GPU. Tune the
`Count`/`Bodies`/`Particles` parameters if needed. Measured per-frame solver
cost (numpy, single core, including the colour pass the callback does):

| Scene | Default size | ~Cost / frame | Notes |
|-------|-------------|-------------|-------|
| Ising | 256² | ~1.8 ms | 512² ≈ 8.6 ms. Metropolis acceptance is a 9-entry table, no `exp` over the lattice |
| N-Body | 600 bodies | ~5.6 ms | O(N²) via a Gram matrix + BLAS matmul; 1000 bodies ≈ 14 ms. Softening 0.12–0.15 keeps mergers stable for a whole set |
| Flow | 20 000 particles | ~7 ms | grid-accelerated curl noise; the field rebuilds every other frame |
| Soft Body | 6 000 particles | ~2 ms | unconditionally-stable position-based shape matching |
| LHC / Open Data | — | ~0 ms | only rebuilds geometry while the tracks grow, and only when a new point would appear |
| Feynman | 615 lines / 12.5k points | ~1 ms | geometry built once; per frame is one numpy pass over the points |
| Bohmian H | 20 000 electrons, 2 substeps | ~7 ms | analytic Bohmian velocity (one wavefunction pass per substep); superpositions cost a little more. Changing orbital re-samples the cloud, a one-off ~0.25 s |
| React-Diff / SDF | GPU | — | feedback / raymarch GLSL TOPs; cost is on the GPU |
| POP Storm | GPU | — | POPs on the GPU (Flow fallback on builds without POPs) |

Because only the active scene cooks, running all eleven in parallel is free until
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

Two gates, one for each half of the project:

**Outside TouchDesigner** — the physics cores are covered by a numpy-only test
suite (conservation laws, divergence-free flow, resonance peaks, every kernel
checked against the formulation it replaced), and the TouchDesigner layer is
checked statically: every callback and shader the builder refers to exists,
the APC controller's scene table matches `build_all`, and the controller's
button/fader logic runs against a stub of the TD API. GitHub Actions runs it
on Python 3.9 and 3.11 (the two Pythons TD ships) on every push.

```bash
pip install -r requirements-dev.txt
pytest -q
```

**Inside TouchDesigner** — `./tools/run_td.sh --check` builds the show
headless and prints `build_report.txt` with every operator error. Run it after
pulling, before a show.

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
  feynman.py        Feynman field loader + travelling flood + point colours
  palette.py        neon colormaps (glow-on-black)
touchdesigner/
  td_build.py       run inside TD to assemble scenes
  callbacks/        Script TOP/CHOP/SOP callback sources (embedded by td_build)
  shaders/          GLSL shader source (.frag/.vert) for the GPU scenes + FX
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
