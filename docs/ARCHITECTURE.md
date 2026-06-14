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
  black `constantTOP`.
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
   **Null TOP** `out`.

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
   `lights`) → bloom → **Null TOP** `out`.

### Polyline scene (LHC / Open Data)

1. **Geometry COMP** `geo`; inside it a **Script SOP** named `sim` with
   `lhc_sop.py` / `opendata_sop.py` as callbacks (`_REPO` edited). Turn the
   SOP's Render and Display flags on. Pulse Setup Parameters.
2. Assign a **Constant MAT** with **Apply Point Color** On to `geo` (this shows
   the per-track `Cd` colours).
3. **Camera** + **Render TOP** → bloom → **Null TOP** `out`.

### The switcher / crossfader (build_all)

Two **Switch TOP** "decks" each pick a scene `out` (deck A driven by the
`Scene` menu, deck B by `Nextscene`), and a **Cross TOP** blends them by the
`Crossfade` parameter (`cross = 0` shows A, `1` shows B). A **Parameter Execute
DAT** watches the `Cut` pulse and commits a transition (copies B→A, resets the
fader). Each scene's `Active` is true only while its deck contributes:

```
(Scene==i and Crossfade<1) or (Nextscene==i and Crossfade>0) or FreerunAll
```

so a single sim runs in steady state and both run only mid-fade. For a plain
instant-only switcher, drop deck B + the Cross TOP and drive one Switch TOP's
`index` from `Scene`.

## The APC mini mk2 surface (build_apc)

`build_apc` assembles an **`APCShow`** COMP that drives a `PhysicsVJ` from an
Akai APC mini mk2 and lights its RGB grid to match. It's event-driven, not
per-frame: all behaviour lives in `callbacks/apc_mini.py` as plain functions,
and three small operators call into it:

* a **MIDI In DAT** (`midiin`) whose `onReceiveMIDI` forwards every message to
  `apc_mini.on_midi(apc, message, channel, index, value)`. Notes 0–63 cut a
  scene + palette (`note = row*8 + col`); the round buttons arm deck B, commit
  the crossfade, toggle freerun, reset and re-fire; CCs 48–56 are the faders.
* a **MIDI Out CHOP** (`ledout`). `sendMIDI('note', channel, note, velocity)`
  lights a pad: `velocity` is the APC's 128-colour index and the **channel
  selects the behaviour** (ch 1–7 = 10 %→100 % solid, 8–11 = pulse, 12–16 =
  blink). `apc_mini.repaint` redraws the whole surface from the show's `Scene`,
  `Nextscene`, `Crossfade`, `Freerunall` and each scene's `Palette`.
* two **Parameter Execute DATs**: `statewatch` repaints when the show changes
  (so mouse and MIDI stay in sync), and `selfwatch` catches the surface's own
  `Reset` pulse and `Device` changes.

**Reset** (`apc_mini.reset`) blanks every LED, then repaints — the recovery
path for a controller that powered on dark, was hot-plugged, or drifted out of
sync. `build_all(apc=True)` (the default) wires this in pointing at the show.

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
  `v = Im(grad psi / psi)` (complex finite-difference gradient). The Script CHOP
  seeds electrons by rejection-sampling `|psi|^2`, integrates them along `v`
  (small fixed step, a few substeps), recycles a fraction each frame to stay
  crisp, and colours by speed. Verified in `tests/test_hydrogen.py`: 1s is
  static, `psi_{2,1,+1}` circulates in +phi (and −m reverses), superpositions
  are time-dependent, and the sampler reproduces `<r>_{1s}=1.5 a0`. Rendered via
  instancing (reliable) with a `choptopPOP` wired but render-off so you can move
  to a POP render path once verified on your build. The velocity scale is
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
