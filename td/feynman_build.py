"""Build the Feynman field scene inside TouchDesigner.

Run once, from the textport:

    run(op('/project1').par.file or 'td/feynman_build.py')

or more simply, paste this file into a Text DAT and use its right-click
'Run Script'. It creates /feynman as a self-contained Base COMP:

    /feynman
      field_geo      Script SOP    the field as ribbons, built once
      field_geo_cb   Text DAT      td/feynman_geo.py
      state          Script CHOP   the flood, two channels a frame
      state_cb       Text DAT      td/feynman_state.py
      state_top      CHOP to TOP   growth in red, tone in green
      field_mat      GLSL MAT      trims, dashes, colours
      field_vert     Text DAT      td/shaders/field.vert
      field_frag     Text DAT      td/shaders/field.frag
      geo            Geo COMP
      cam            Camera COMP   orthographic, sized to the field
      render          Render TOP    what to send out

and puts the parameters worth performing with on /feynman itself, so the
component can be dropped into a VJ project and driven from one page.

This has not been run — there is no TouchDesigner in the environment it was
written in. The geometry and the flood it drives are checked by
tools/preview.py, which renders the same numbers to PNG without TD; what is
unverified is this wiring. Expect to fix a parameter name or two — the README's
"If it breaks" section is the first place to look.
"""

FIELD = 'fields/16x9.json'


def build(parent_op=None, name='feynman'):
    parent_op = parent_op or op('/')
    if parent_op.op(name):
        parent_op.op(name).destroy()
    base = parent_op.create(baseCOMP, name)
    base.par.w, base.par.h = 240, 200

    def dat(nm, path):
        d = base.create(textDAT, nm)
        d.par.file = path
        d.par.loadonstartpulse.pulse()
        d.par.syncfile = True          # edit the file, TD picks it up
        return d

    # ---- geometry, built once -------------------------------------------
    geo_cb = dat('field_geo_cb', 'td/feynman_geo.py')
    sop = base.create(scriptSOP, 'field_geo')
    sop.par.callbacks = geo_cb
    p = sop.appendCustomPage('Field')
    p.appendFile('Fieldfile', label='Field file')
    p.appendFloat('Width', label='Line width')
    p.appendFloat('Markscale', label='Mark size')
    p.appendPulse('Rebuild')
    sop.par.Fieldfile = FIELD
    sop.par.Width = 1.0
    sop.par.Markscale = 1.0
    sop.cook(force=True)

    # ---- the flood, per frame -------------------------------------------
    state_cb = dat('state_cb', 'td/feynman_state.py')
    chop = base.create(scriptCHOP, 'state')
    chop.par.callbacks = state_cb
    q = chop.appendCustomPage('Flood')
    q.appendOP('Geo')
    q.appendFloat('Speed')
    q.appendFloat('Traverse')
    q.appendFloat('Tail')
    q.appendFloat('Fade')
    q.appendInt('Walkers')
    q.appendInt('Seed')
    q.appendToggle('Freeze')
    q.appendPulse('Fill')
    q.appendPulse('Reseed')
    chop.par.Geo = sop
    chop.par.Speed, chop.par.Traverse = 1.0, 34.0
    chop.par.Tail, chop.par.Fade = 0.85, 0.34
    chop.par.Walkers, chop.par.Seed = 2, 3

    top = base.create(chopToTOP, 'state_top')
    top.par.chop = chop
    top.par.dataformat = 1            # 32-bit float, so growth is not banded

    # ---- material --------------------------------------------------------
    vert = dat('field_vert', 'td/shaders/field.vert')
    frag = dat('field_frag', 'td/shaders/field.frag')
    mat = base.create(glslMAT, 'field_mat')
    mat.par.vertexdat = vert
    mat.par.pixeldat = frag
    # One sampler and the uniforms the shader reads.
    mat.par.samplername0 = 'sState'
    mat.par.sampler0 = top
    mat.par.uniname0, mat.par.value0x = 'uStateRows', 1.0
    mat.par.uniname1 = 'uInk'
    mat.par.value1x, mat.par.value1y, mat.par.value1z = 239 / 255, 233 / 255, 218 / 255
    mat.par.uniname2 = 'uAccent'
    mat.par.value2x, mat.par.value2y, mat.par.value2z = 255 / 255, 82 / 255, 48 / 255
    mat.par.uniname3, mat.par.value3x = 'uDash', 12.0
    mat.par.uniname4, mat.par.value4x = 'uSoft', 0.55
    mat.par.uniname5, mat.par.value5x = 'uBright', 1.0
    # Lines and marks are drawn with alpha and must not fight over depth.
    mat.par.blending = True
    mat.par.depthtest = False
    # uStateRows follows the state CHOP, so a different field needs no edit.
    mat.par.value0x.expr = f"op('{chop.path}').numSamples"

    # ---- render ----------------------------------------------------------
    g = base.create(geometryCOMP, 'geo')
    g.par.sop = sop           # some builds want the SOP path as a string
    g.par.material = mat

    cam = base.create(cameraCOMP, 'cam')
    cam.par.projection = 1    # orthographic
    cam.par.tz = 10
    # Sized to the field, so one unit is one pixel at native resolution.
    cam.par.orthowidth.expr = f"op('{sop.path}').storage.get('field_w', 1920)"

    render = base.create(renderTOP, 'render')
    render.par.camera = cam
    render.par.geometry = g
    render.par.resolutionw.expr = f"op('{sop.path}').storage.get('field_w', 1920)"
    render.par.resolutionh.expr = f"op('{sop.path}').storage.get('field_h', 1080)"
    render.par.bgalpha = 0            # transparent, so it can be composited
    render.par.antialias = 3          # the linework is thin; it needs the samples

    # ---- one page to perform from ----------------------------------------
    page = base.appendCustomPage('Field')
    page.appendFile('Fieldfile', label='Field file')
    page.appendFloat('Speed')
    page.appendFloat('Tail')
    page.appendInt('Walkers')
    page.appendFloat('Width', label='Line width')
    page.appendFloat('Bright', label='Brightness')
    page.appendFloat('Dash')
    page.appendToggle('Freeze')
    page.appendPulse('Fill')
    page.appendPulse('Reseed')
    base.par.Fieldfile = FIELD
    base.par.Speed, base.par.Tail, base.par.Walkers = 1.0, 0.85, 2
    base.par.Width, base.par.Bright, base.par.Dash = 1.0, 1.0, 12.0

    sop.par.Fieldfile.expr = "parent().par.Fieldfile"
    sop.par.Width.expr = "parent().par.Width"
    chop.par.Speed.expr = "parent().par.Speed"
    chop.par.Tail.expr = "parent().par.Tail"
    chop.par.Walkers.expr = "parent().par.Walkers"
    chop.par.Freeze.expr = "parent().par.Freeze"
    mat.par.value5x.expr = "parent().par.Bright"
    mat.par.value3x.expr = "parent().par.Dash"

    for o in base.children:
        o.nodeY = -120 * list(base.children).index(o)

    print(f"[feynman] built {base.path}. Look at {render.path}.")
    print("[feynman] pulse Fill on /feynman to light the whole field at once.")
    return base


if __name__ == '__main__':
    build()
