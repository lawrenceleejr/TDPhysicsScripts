# Architecture & manual setup

## Design in one paragraph

The physics lives in pure-numpy classes under `physics/` with a tiny, explicit
API: construct, `step()`, then read numpy arrays (`positions`, `speeds()`,
`field01()`, …). These are unit-tested with no TouchDesigner involved. The
TouchDesigner side is intentionally thin: a Script TOP/CHOP/SOP callback that
(1) keeps one sim instance per operator, (2) advances it **once per rendered
frame** (guarded by `absTime.frame` so multiple cooks don't double-step), and
(3) ships the result to the GPU with `copyNumpyArray`. `td_build.py` is a
convenience that wires whole scenes so you never touch a node by hand.

## How a sim advances exactly once per frame

Script operators don't cook every frame unless something pulls them. Each scene
gets a small **Execute DAT** (`cook_driver`) whose `onFrameStart` force-cooks the
scene's `sim` operator — but only while the scene's `Active` flag is on:

```python
def onFrameStart(frame):
    c = me.parent()
    if int(c.par.Active):
        op(c.fetch('simpath', '')).cook(force=True)
```

`build_all` sets each scene's `Active` to an expression like
`int(parent().par.Scene.menuIndex == 2 or parent().par.Freerunall)`. So only the
selected scene (plus everything, if `Freerun All` is on) consumes CPU. Inside
`onCook`, stepping is guarded:

```python
if sim.last_frame != absTime.frame:
    sim.step(...)
    sim.last_frame = absTime.frame
```

## CHOP channel layout (instancing)

The N-body and particle callbacks emit a single `(7, N)` float32 array via
`copyNumpyArray(arr, baseName="c")`, producing channels `c0…c6`:

| channel | meaning            | instance parameter |
|---------|--------------------|--------------------|
| c0 c1 c2 | translate x y z   | `instancetx/ty/tz` |
| c3 c4 c5 | colour r g b      | `instancer/g/b`    |
| c6       | uniform scale     | `instancesx/sy/sz` |

The Feynman callback emits a `(4, P)` array instead — one sample per **point**
of the field's geometry, not per instance:

| channel | meaning |
|---------|---------|
| c0 c1 c2 | colour r g b, already multiplied by the line's tone |
| c3       | alpha: 1 up to the growing tip of the line, 0 past it |

A **CHOP to SOP** lands those on the geometry's `Cd`, which is why the sample
count has to equal the point count exactly. `feynman_chop.py` reads the field
and marks settings off the Script SOP rather than keeping its own copies, so
the two cannot drift apart.

## Key TouchDesigner APIs used

* **Script TOP** — `scriptOp.copyNumpyArray(arr)`, `arr` shape `(H, W, 3)`
  float32 in [0, 1].
* **Script CHOP** — `scriptOp.copyNumpyArray(arr, baseName='c')`, `arr` shape
  `(channels, samples)` float32.
* **Script SOP** — `scriptOp.appendPoly(n, closed=False, addPoints=True)`,
  `poly[i].point.x/.y/.z`; colour via `scriptOp.pointAttribs.create('Cd', (0,0,0))`
  then `poly[i].point.Cd = (r, g, b)`.
* **Geometry COMP instancing** — `instancing`, `instanceop`,
  `instancetx/ty/tz`, `instancesx/sy/sz`, `instancecolormode`, `instancer/g/b`.
* **Render TOP** — `camera`, `geometry`, `lights` (operator names / patterns).
* **Constant MAT** — `colorr/g/b`, `alpha`, `applypointcolor`.
* **Glow** — `blurTOP.size`, then a `compositeTOP` with `operand='add'` over a
  black `constantTOP`, into an **Out TOP** named `out`. The Out TOP is what
  gives the scene COMP an output connector.
* **Wiring only happens inside one network.** `connect()` from an operator
  inside a COMP to one outside it does nothing and raises nothing. That is why
  each scene ends in an Out TOP (the COMP's connector is wired to the decks)
  and why the overlay fetches the Reactor's textures with Select TOPs.
  `td_build._connect` verifies every wire and reports a miss.
* **Building** — `parent.create('scriptCHOP', 'sim')`,
  `a.outputConnectors[0].connect(b.inputConnectors[i])`, `appendCustomPage`,
  `appendFloat/Int/Menu/Toggle/Pulse`, parameter `callbacks` + `setuppars` pulse.

These are stable across TouchDesigner 2022+. Every assignment in `td_build.py`
is wrapped, so a renamed parameter prints a `[td_build]` note and the build
still finishes — you then fix that one line in the UI.

## Manual wiring (fallback)

If you'd rather build a scene by hand (or a builder hits a version quirk):

### Texture scene (Ising)

1. **Script TOP** named `sim`. Set its `callbacks` DAT text to
   `touchdesigner/callbacks/ising_top.py`, editing the `_REPO = r""` line to
   your repo path. Pulse **Setup Parameters**.
2. Optional bloom: **Blur TOP** (input = `sim`) → **Composite TOP**
   (`operand = add`, inputs: `sim`, the blur, and a black **Constant TOP**) →
   **Out TOP** `out`.

### Instanced scene (N-Body / Flow / Soft Body)

1. **Script CHOP** named `sim`; set its `callbacks` to the matching file
   (`nbody_chop.py` or `particles_chop.py`) with `_REPO` edited. Pulse Setup
   Parameters. (For Soft Body, set `Mode = softbody`.)
2. **Geometry COMP** `geo`. Inside it put a low-poly **Sphere SOP**
   (Primitive = Polygon, ~6×8). Delete the default torus.
3. On `geo`: **Instancing** On; **Instance OP** = `../sim`; Translate
   `c0/c1/c2`; Scale `c6/c6/c6`; Instance Colour `c3/c4/c5`.
4. Assign a **Constant MAT** (any bright base colour) to `geo`.
5. **Camera COMP** pulled back along +Z; **Render TOP** (`camera`, `geometry`,
   `lights`) → bloom → **Out TOP** `out`.

### Polyline scene (LHC / Open Data)

1. **Geometry COMP** `geo`; inside it a **Script SOP** named `sim` with
   `lhc_sop.py` / `opendata_sop.py` as callbacks (`_REPO` edited). Turn the
   SOP's Render and Display flags on. Pulse Setup Parameters.
2. Assign a **Constant MAT** with **Apply Point Color** On to `geo` (this shows
   the per-track `Cd` colours).
3. **Camera** + **Render TOP** → bloom → **Out TOP** `out`.

### Static-geometry scene (Feynman)

The one scene whose geometry never moves. The field is 12,000-odd points and
rebuilding that in Python every frame would not hold 60 fps, so the geometry is
built once and only its point colours change.

1. **Geometry COMP** `geo`; inside it a **Script SOP** named `lines` with
   `feynman_sop.py` as callbacks (`_REPO` edited). Pulse Setup Parameters.
   Leave its Render and Display flags **off** — the SOP downstream is what
   renders.
2. A **Script CHOP** `state` beside `geo` (not inside it) with
   `feynman_chop.py`. Set its **Geometry SOP** parameter to `../geo/lines`.
3. Inside `geo`, a **CHOP to SOP** `paint` with `lines` as its input and
   **CHOP** set to `../state`. Set **Channel Scope** to `*` and **Attribute
   Scope** to `Cd`. Turn its Render and Display flags on.
4. Assign a **Constant MAT** with **Apply Point Color** On to `geo`.
5. **Camera** (straight on, no tilt) + **Render TOP** → bloom → **Null TOP**
   `out`.
6. The `cook_driver` force-cooks **`state`**, not the SOP: the CHOP is what has
   to run every frame.

If the field renders as flat white, step 3 is where to look: the CHOP to SOP
matches channels to attributes **by name**, so the CHOP's channels must be
called `Cd(0) Cd(1) Cd(2) Cd(3)` (`td_build` inserts a Rename CHOP, `state_cd`,
between `state` and `paint` for exactly this). If it renders as
nothing, check that the CHOP's sample count matches the SOP's point count
(`state` info → `numSamples`); a mismatch means the two are on different
fields or disagree about the marks toggle.

### The switcher / crossfader (build_all)

Two **Switch TOP** "decks" each pick a scene (the scene COMP's output
connector, fed by its Out TOP; deck A driven by the `Scene` menu, deck B by
`Nextscene`), and a **Cross TOP** blends them by the
`Crossfade` parameter (`cross = 0` shows A, `1` shows B). A **Parameter Execute
DAT** watches the `Cut` pulse and commits a transition (copies B→A, resets the
fader).

A Cross TOP cooks *both* inputs every frame, so a deck that contributes nothing
to the mix must not point at a different scene or that scene's whole render
chain (render, trails, bloom — or a raymarch) runs for nothing. The deck index
expressions therefore fall back to the other deck's scene:

```
deck_a.index = Scene     if Crossfade < 1 else Nextscene
deck_b.index = Nextscene if Crossfade > 0 else Scene
```

TouchDesigner cooks a node once per frame however many things pull it, so in
steady state the idle deck is free. Each scene's `Active` uses the same rule:

```
(Scene==i and Crossfade<1) or (Nextscene==i and Crossfade>0) or FreerunAll
```

so a single sim runs (and a single scene renders) in steady state, and both
only mid-fade. For a plain
instant-only switcher, drop deck B + the Cross TOP and drive one Switch TOP's
`index` from `Scene`.

## The APC mini mk2 surface (build_apc)

`build_apc` assembles an **`APCShow`** COMP that drives a `PhysicsVJ` from an
Akai APC mini mk2 and lights its RGB grid to match. It's event-driven, not
per-frame: all behaviour lives in `callbacks/apc_mini.py` as plain functions,
and three small operators call into it:

* a **MIDI In DAT** (`midiin`) whose `onReceiveMIDI` forwards every message to
  `apc_mini.on_midi(apc, message, channel, index, value)`. Grid notes 0–63
  (`note = row*8 + col`) are decoded into one of four quadrants by
  `_decode`: `('scene', i)` lower-left, `('palette', i)` / `('tool', name)`
  lower-right, `('action', name)` upper-left, `('fx', name)` upper-right. The
  round buttons map to the same action names (`TRACK_ACTIONS`,
  `SCENE_ACTIONS`); CCs 48–55 are faders (`FADER_MAP`, per live scene), 56 the
  crossfade; note 122 is Shift. Note-offs matter: actions in `MOMENTARY`
  (Trail+, Strobe, Title) undo on release, and Title distinguishes a tap
  (stays `TITLE_TAP_SECONDS`) from a hold.
* everything funnels through **`apc_mini.perform(show, action, apc, pressed)`**
  — the one place an action is defined. The show's own pulses, the click DAT
  and the keyboard DAT can call it too, so a "Punch" from the mouse and from
  the pad are literally the same code path.
* a **MIDI Out CHOP** (`ledout`). `sendNoteOn(channel, note, velocity)`
  lights a pad: `velocity` is the APC's 128-colour index and the **channel
  selects the behaviour** (ch 1–7 = 10 %→100 % solid, 8–11 = pulse, 12–16 =
  blink). `apc_mini.repaint` redraws the whole surface from the show's `Scene`,
  `Nextscene`, `Crossfade`, `Freerunall`, `Freeze`, `Blackout`, the sixteen
  FX toggles, the Reactor's `Mute` and each scene's `Palette`; quadrants have
  their own colours (white/yellow actions, purple/green FX, cyan/red tools).
* two **Parameter Execute DATs**: `statewatch` repaints when the show changes
  (so mouse and MIDI stay in sync), and `selfwatch` catches the surface's own
  `Reset` pulse and `Device` changes.

The title overlay is storage-driven: `perform('title')` writes `title_t0` /
`title_toff` onto the PhysicsVJ COMP, and `_title_overlay` in the builder binds
the ink shader's bleed/fade and the gating Switch to `parent().fetch(...)` of
those two numbers, so no per-frame Python runs for it.

LEDs are sent **by difference**: `repaint` remembers what every pad was last
told (per surface, in `_LED_STATE`) and only re-sends pads whose state changed,
so the `statewatch` firing on every `Crossfade` tick during a fade costs one or
two messages, not the ninety a full repaint would. **Reset** (`apc_mini.reset`)
forgets that memory, blanks every LED unconditionally, then repaints — the
recovery path for a controller that powered on dark, was hot-plugged, or
drifted out of sync. `build_all(apc=True)` (the default) wires this in pointing
at the show.

`tests/test_td_contracts.py` drives the whole control map against a stub of
the TD API — every quadrant, Shift, momentary release, tap tempo, the title
timing and the fader map — because the one bug this surface has had (Cut and
Freerun arming scenes once the grid grew to eight columns) was exactly the kind
a static parse cannot see. It also pins `FX_NAMES` to the builder's copy and
the shader's `uFxA..uFxD` order.

The scene/palette tables (`SCENE_NAMES`, the per-scene re-fire pulse names, the
palette→colour map) are constants at the top of `apc_mini.py` — re-map the
surface by editing those, not the wiring.

## The GLSL / audio / tempo / POP layer

The same split as the physics scenes applies: testable numpy cores +
hot-swappable assets + a thin, defensive TD adapter.

* **Audio (`physics/audio.py` → `callbacks/audio_chop.py`).** `AudioAnalyzer`
  does a windowed rFFT, splits perceptual bands (bass/mid/high) and tracks RMS
  with an asymmetric attack/release envelope; `BeatTracker` is an adaptive
  energy onset detector; all unit-tested in `tests/test_audio.py`. The Script
  CHOP reads input 0's samples via `numpyArray()` and emits one sample per
  channel. `build_reactor` also builds waveform + spectrum row textures
  (CHOP-to-TOP) for the visualiser.
* **Tempo (`physics/audio.py:TempoClock` → `callbacks/tempo_chop.py`).** A
  24-PPQN clock follower with a free-running manual-BPM fallback. The paired
  MIDI In DAT forwards realtime messages to `tempo_chop.on_realtime`; the Script
  CHOP queries `TempoClock.phase(absTime.seconds)` each frame for continuous
  beat/bar position.
* **Shaders (`touchdesigner/shaders/`).** `td_build._load_shader` prepends
  `common.glsl` (TD has no `#include`) and drops the source into a Text DAT set
  as the GLSL TOP/MAT `pixeldat`/`vertexdat`. Reaction-diffusion runs in a
  Feedback TOP loop at 32-bit float; the raymarch/post/waveform shaders are
  single-pass. Uniforms are bound to expressions reading the Reactor/Tempo CHOPs
  via `_glsl_uniforms` (the GLSL-TOP "Vectors" slots).
* **POPs (`build_pops`).** Attempts a real POP network (`spherePOP` →
  `particlePOP` with `forceradialPOP`/`noisePOP` in a feedback loop) and falls
  back to the proven curl-noise Flow callback if the family isn't available.
  Both paths share the compiled glow MAT + 3-point light rig.
* **Bohmian hydrogen (`physics/hydrogen.py` → `callbacks/hydrogen_chop.py` →
  `build_bohmian`).** Hydrogen eigenstates `psi_{nlm}=R_{nl}Y_l^m` (generalised
  Laguerre + associated Legendre recurrences, atomic units), time-dependent
  superpositions, and the de Broglie-Bohm guidance velocity
  `v = Im(grad psi / psi)`. The gradient is analytic — d/dr, d/dθ, d/dφ of
  `R_nl Y_lm` in spherical components, with `Im(grad psi · conj psi)` taken
  per component so the change to Cartesian is real arithmetic — which makes a
  velocity evaluation one wavefunction pass instead of the seven a central
  finite-difference stencil needs; that stencil is kept as `bohm_velocity_fd`
  and the test suite holds the two to 1e-6. The Script CHOP
  seeds electrons by rejection-sampling `|psi|^2`, integrates them along `v`
  (small fixed step, a few substeps), recycles a fraction each frame to stay
  crisp, and colours by speed. Verified in `tests/test_hydrogen.py`: 1s is
  static, `psi_{2,1,+1}` circulates in +phi (and −m reverses), superpositions
  are time-dependent, and the sampler reproduces `<r>_{1s}=1.5 a0`. Rendered via
  instancing. The velocity scale is
  deliberately exaggerated (a `Speed` param) — true atomic velocities are tiny.

### Version-sensitive spots (verify on first load)

This layer follows standard TD conventions but is built to be edited, since a
few APIs vary by build. All assignments are wrapped, so a mismatch degrades
gracefully (a uniform stays 0, a scene falls back) rather than breaking the
build. Glance at these:

1. **GLSL-TOP uniform slots.** `_glsl_uniforms` writes `uninameN` / `valueNx`.
   If your build names them differently, the shaders still compile — just bind
   the listed uniforms by hand on each GLSL TOP's *Vectors* page. The uniform
   names each shader expects are documented in its header comment.
2. **POP operators/parameters** (`build_pops`): the create type strings
   (`particlePOP`, `forceradialPOP`, `noisePOP`, `spherePOP`, `nullPOP`) and the
   feedback-loop / render parameter names. If POPs aren't in your build it
   silently uses the Flow fallback.
3. **MIDI realtime clock delivery** (`tempo_chop`): whether your TD delivers
   clock/start/stop through the MIDI In DAT callback, and the exact `message`
   text. The manual-BPM path always works regardless.
4. **`audiodeviceinCHOP` / `choptopTOP` / `audiospectrumCHOP`** device + param
   names on the Reactor.
5. **Named CHOP channels.** The Reactor and Tempo Script CHOPs emit their
   channels with `appendChan` (the documented way to *name* Script CHOP
   channels), because every binding downstream reads them by name
   (`op('Reactor/analyze')['bass']`). If a build ever lacks `appendChan`, the
   channels would come out as `chan0..`, and the audio bindings read 0.
6. **`ParMode` is not importable everywhere.** TD injects it into DAT scripts
   but not into imported modules, and on a 2025 build `from td import ParMode`
   came back `None`, which silently left *every* expression in the show unset
   (decks, `Active`, crossfade, orbit, trails, all uniforms). `td_build._expr`
   now takes the enum from the parameter itself (`type(par.mode)`) and logs
   every binding it could not make, so a repeat would show in the report as
   dozens of lines rather than as a show that does not switch scenes.
7. **CHOP to SOP maps channels to attributes by name.** Channels must be
   called `Cd(0)..Cd(3)` (the SOP to CHOP convention); the Feynman scene
   renames its `c0..c3` with a Rename CHOP (`state_cd`) and names them in
   the CHOP to SOP's Channel Scope. If the field renders flat white, that
   Rename CHOP is where to look.
8. **Execute DAT frame-start flag.** The cook drivers set `framestart` (with
   `fs` as a fallback spelling). If neither exists on a build, the visible
   scene still animates — TD pulls its Script OP every frame through the
   render — but hidden scenes under `Freerun All` would not advance.

## Going further

* **Trails:** built in (`_trails`) — a **Feedback TOP** → **Level** (decay) →
  **Composite** (`over`) inserted between the render and the bloom, driven by a
  per-scene `Trail` parameter (0 = passthrough). Lives on every geometry scene.
* **Point sprites:** for very high particle counts, instance a single point with
  a Point Sprite MAT (a textured glow dot) instead of a sphere.
* **Crossfades:** built in via the A/B decks + Cross TOP (see above). For more
  than two simultaneous layers, chain additional Cross TOPs.
* **HUD:** the Open Data scene stores `invariant_mass` on its SOP, and `_mass_hud`
  overlays a **Script TOP** spectrum (the `mass_hud_top.py` callback histograms
  the dataset and marks the live event) + a **Text TOP** title, faded by a
  `HUD Opacity` level. The Script TOP array is RGBA `float32`, row 0 = bottom
  (flip with `_FLIP_Y` in the callback if your build shows it inverted).
