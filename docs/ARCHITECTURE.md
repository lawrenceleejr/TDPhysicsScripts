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

## Going further

* **Trails:** add a **Feedback TOP** before the bloom for motion trails — looks
  great on N-Body and Flow.
* **Point sprites:** for very high particle counts, instance a single point with
  a Point Sprite MAT (a textured glow dot) instead of a sphere.
* **Crossfades:** built in via the A/B decks + Cross TOP (see above). For more
  than two simultaneous layers, chain additional Cross TOPs.
* **HUD:** the Open Data scene stores `invariant_mass` on its SOP — read it with
  a Text TOP/CHOP for a live readout.
