"""Unit tests for the framework-agnostic physics cores.

These run with plain numpy (no TouchDesigner) and validate the physics:
conservation laws, expected statistical behaviour, shapes and dtypes.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from physics import palette
from physics.ising import IsingModel, CRITICAL_TEMPERATURE
from physics.nbody import NBodySim
from physics.particles import CurlNoiseFlow, ShapeMatchedSoftBody, NoiseField
from physics.lhc_tracks import LHCEventGenerator, helix_track, DEFAULT_B
from physics.opendata import (
    generate_synthetic_dimuon, _invariant_mass, DimuonShow, RESONANCES,
)
from physics.feynman import Field, FeynmanShow, LEGAL_VERTICES

_FIELDS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "feynman")
_SHIPPED = ["16x9", "16x9-b", "16x9-c", "21x9", "9x16", "1x1"]


# --- palette ------------------------------------------------------------
def test_colorize_shape_and_range():
    for name in palette.PALETTE_NAMES:
        out = palette.colorize(np.linspace(0, 1, 50), name)
        assert out.shape == (50, 3)
        assert out.dtype == np.float32
        assert out.min() >= 0.0 and out.max() <= 1.0


def test_colorize_image_shape():
    img = np.random.rand(8, 12)
    assert palette.colorize(img, "cyber").shape == (8, 12, 3)


def test_dark_palettes_start_near_black():
    # Glow-on-black look requires the low end to be dark.
    for name in ("inferno", "magma", "cyber", "synth", "ice", "acid"):
        assert palette.colorize(0.0, name).max() < 0.12


def test_normalize_handles_flat_input():
    assert np.all(palette.normalize(np.full(5, 3.0)) == 0.0)


# --- Ising --------------------------------------------------------------
def test_ising_shapes_and_values():
    m = IsingModel(size=32, seed=1)
    assert m.spins.shape == (32, 32)
    assert set(np.unique(m.spins)).issubset({-1, 1})
    m.step(2)
    assert m.field01().shape == (32, 32)
    assert m.field01().dtype == np.float32
    dw = m.domain_walls()
    assert dw.shape == (32, 32)
    assert dw.min() >= 0.0 and dw.max() <= 1.0


def test_ising_low_temperature_stays_ordered():
    # Starting essentially aligned at very low T, magnetisation should remain
    # near saturation (no thermal disordering).
    m = IsingModel(size=48, temperature=0.5, seed=2)
    m.spins[:] = 1
    m.step(20)
    assert abs(m.magnetization()) > 0.9


def test_ising_high_temperature_disorders():
    m = IsingModel(size=64, temperature=6.0, seed=3)
    m.step(60)
    assert abs(m.magnetization()) < 0.3


def test_ising_determinism():
    a = IsingModel(size=32, seed=7)
    b = IsingModel(size=32, seed=7)
    a.step(5)
    b.step(5)
    assert np.array_equal(a.spins, b.spins)


def test_critical_temperature_value():
    assert CRITICAL_TEMPERATURE == pytest.approx(2.2691853, abs=1e-4)


# --- N-body -------------------------------------------------------------
def test_nbody_momentum_conserved():
    sim = NBodySim.plummer_sphere(n=80, seed=4, dt=0.002)
    p0 = sim.total_momentum()
    for _ in range(50):
        sim.step()
    p1 = sim.total_momentum()
    assert np.allclose(p0, p1, atol=1e-6)


def test_nbody_energy_bounded():
    # Leapfrog should keep total energy from drifting much over many steps.
    sim = NBodySim.plummer_sphere(n=120, seed=5, dt=0.003, softening=0.1)
    e0 = sim.kinetic_energy() + sim.potential_energy()
    for _ in range(300):
        sim.step()
    e1 = sim.kinetic_energy() + sim.potential_energy()
    assert abs(e1 - e0) / abs(e0) < 0.1


def test_nbody_initial_conditions_shapes():
    for ctor in (NBodySim.rotating_disk, NBodySim.two_galaxies, NBodySim.plummer_sphere):
        sim = ctor(n=50, seed=6)
        assert sim.pos.shape[1] == 3
        assert sim.vel.shape == sim.pos.shape
        assert sim.mass.shape[0] == sim.pos.shape[0]
        assert sim.speeds().dtype == np.float32
        assert np.isfinite(sim.pos).all()


def test_nbody_no_self_force_nan():
    sim = NBodySim.rotating_disk(n=40, seed=8)
    for _ in range(20):
        sim.step()
    assert np.isfinite(sim.pos).all()


# --- particles ----------------------------------------------------------
def test_noise_range_and_determinism():
    nf = NoiseField(seed=1)
    pts = np.random.RandomState(0).uniform(-5, 5, (1000, 3))
    v = nf.noise(pts)
    assert v.shape == (1000,)
    assert np.abs(v).max() <= 1.2
    nf2 = NoiseField(seed=1)
    assert np.allclose(nf2.noise(pts), v)


def test_curl_noise_incompressible():
    # The curl of a vector field is divergence-free; verify numerically that
    # the divergence of the grid velocity field (the one actually sampled by
    # the particles) is tiny compared with the curl magnitude.
    flow = CurlNoiseFlow(n=1, grid_res=32, bounds=6.0, seed=2)
    fld = flow.field  # (R, R, R, 3)
    h = flow._spacing
    div = (
        np.gradient(fld[..., 0], h, axis=0)
        + np.gradient(fld[..., 1], h, axis=1)
        + np.gradient(fld[..., 2], h, axis=2)
    )
    typical = np.linalg.norm(fld, axis=-1)
    # Ignore the outer shell where one-sided gradients are less accurate.
    inner = slice(2, -2)
    rel = np.abs(div[inner, inner, inner]).mean() / typical[inner, inner, inner].mean()
    assert rel < 0.05


def test_curl_noise_stays_in_bounds():
    flow = CurlNoiseFlow(n=2000, bounds=5.0, seed=3)
    for _ in range(120):
        flow.step(1 / 60)
    assert np.abs(flow.pos).max() <= 5.0 + 1e-6
    assert np.isfinite(flow.pos).all()
    assert flow.speeds().shape == (2000,)


def test_softbody_cohesive_and_rotates():
    body = ShapeMatchedSoftBody(n=800, radius=2.0, seed=4, wobble=0.0)
    start = body.pos.copy()
    radii = []
    moved = 0.0
    for i in range(120):
        body.step(1 / 60)
        cm = body.pos.mean(axis=0)
        radii.append(np.linalg.norm(body.pos - cm, axis=1).mean())
        if i == 60:
            moved = np.linalg.norm(body.pos - start, axis=1).mean()
    # Stays cohesive: mean radius doesn't blow up or collapse.
    assert 1.0 < np.mean(radii) < 3.5
    assert np.isfinite(body.pos).all()
    # It actually moves (spins), so points are displaced from their start.
    assert moved > 0.2


def test_softbody_stress_shape():
    body = ShapeMatchedSoftBody(n=300, seed=5)
    body.step(1 / 60)
    s = body.stress()
    assert s.shape == (300,)
    assert s.dtype == np.float32


# --- LHC tracks ---------------------------------------------------------
def test_helix_starts_at_vertex():
    v = np.array([0.1, -0.2, 0.05])
    pts = helix_track(v, (1.0, 0.5, 2.0), charge=1, B=DEFAULT_B, n_points=32)
    assert pts.shape == (32, 3)
    assert np.allclose(pts[0], v, atol=1e-6)


def test_high_pt_straighter_than_low_pt():
    # Curvature ~ 1/pT, so a high-pT track deviates less from a straight line.
    def deviation(pt):
        pts = helix_track((0, 0, 0), (pt, 0.0, 0.0), 1, DEFAULT_B, length=5.0, n_points=50)
        chord = pts[-1] - pts[0]
        chord = chord / np.linalg.norm(chord)
        rel = pts - pts[0]
        proj = rel - np.outer(rel @ chord, chord)
        return np.linalg.norm(proj, axis=1).max()

    assert deviation(20.0) < deviation(1.0)


def test_neutral_track_is_straight():
    pts = helix_track((0, 0, 0), (2.0, 0.0, 1.0), charge=0, B=DEFAULT_B, n_points=20)
    # All points collinear -> cross products with the direction vanish.
    d = pts[-1] - pts[0]
    cross = np.cross(pts - pts[0], d)
    assert np.allclose(cross, 0.0, atol=1e-6)


def test_event_generator():
    gen = LHCEventGenerator(seed=11)
    assert len(gen.tracks) > 20
    for tr in gen.tracks:
        assert tr.points.shape[1] == 3
        assert tr.color.shape == (3,)
    grown = gen.grow(0.5)
    assert all(len(g.points) <= len(t.points) for g, t in zip(grown, gen.tracks))
    n_before = len(gen.tracks)
    gen.new_event()
    assert len(gen.tracks) > 0


# --- open data ----------------------------------------------------------
def test_synthetic_dimuon_recovers_mass():
    d = generate_synthetic_dimuon(n=500, seed=0)
    m_recomputed = _invariant_mass(d)
    assert np.allclose(m_recomputed, d["M"], atol=1e-3)


def test_synthetic_dimuon_shows_resonances():
    d = generate_synthetic_dimuon(n=8000, seed=1)
    M = d["M"]
    # There should be a clear excess of events near the Z mass.
    near_z = np.sum(np.abs(M - RESONANCES["Z"]) < 3.0)
    near_gap = np.sum(np.abs(M - 60.0) < 3.0)  # a quiet region
    assert near_z > 5 * max(near_gap, 1)


def test_dimuon_show_builds_two_tracks():
    show = DimuonShow(palette="ice")
    assert len(show.tracks) == 2
    show.next_event()
    assert len(show.tracks) == 2
    assert show.current_mass > 0
    grown = show.grow(0.3)
    assert len(grown) == 2


def test_opendata_empty_csv_falls_back(tmp_path):
    # A present-but-empty CSV must fall back to synthetic, not crash.
    from physics.opendata import load_dimuon
    p = tmp_path / "dimuon.csv"
    p.write_text("E1,px1,py1,pz1,Q1,E2,px2,py2,pz2,Q2,M\n")  # header only
    d = load_dimuon(str(p))
    assert len(d["M"]) > 0
    show = DimuonShow(path=str(p))
    assert show.n_events > 0 and len(show.tracks) == 2


def test_ising_low_temperature_no_overflow():
    # Very low T used to overflow exp() to inf and spam RuntimeWarnings.
    import warnings
    m = IsingModel(size=48, temperature=0.02, seed=1)
    m.spins[:] = 1
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        m.step(10)
    assert np.isfinite(m.field01()).all()


def test_nbody_merger_stays_finite_long_run():
    # The galaxy-merger scene runs for a whole set; it must not blow up.
    sim = NBodySim.two_galaxies(n=200, seed=3)
    for _ in range(1500):
        sim.step()
    assert np.isfinite(sim.pos).all()
    # Core stays compact: most bodies remain within a sane radius.
    r = np.linalg.norm(sim.pos - sim.pos.mean(axis=0), axis=1)
    assert np.median(r) < 40.0
# --- the Feynman field --------------------------------------------------
@pytest.mark.parametrize("name", _SHIPPED)
def test_field_loads_and_every_vertex_is_legal(name):
    """The exporter audits before writing; this audits after reading.

    A field with an illegal vertex in it is the one defect that would show as
    physics nonsense on a wall rather than as a glitch, so it is checked at
    both ends of the pipe.
    """
    f = Field(os.path.join(_FIELDS, "%s.json" % name))
    assert f.n_edges > 100
    assert len(f.verts) > 50
    assert f.illegal_vertices() == []
    legs = f.vertex_legs()
    interactions = [v for v in legs if len(v) > 2 or (len(v) == 2 and v[0] != v[1])]
    assert interactions, "a field with no interactions in it is not a field"
    assert all(v in LEGAL_VERTICES for v in interactions)


def test_geometry_and_colour_arrays_line_up():
    """The contract the Script SOP and the Script CHOP meet on.

    The SOP appends these polylines in this order; the CHOP puts out one
    sample per point in the same order. If the two ever disagree the colours
    land on the wrong lines, so the count is asserted rather than assumed.
    """
    show = FeynmanShow(os.path.join(_FIELDS, "16x9.json"), seed=3)
    assert sum(len(p) for p in show.polys) == show.n_points
    c = show.colours()
    assert c.shape == (show.n_points, 4)
    assert c.dtype == np.float32
    # Line points are the field's own polylines plus the extra pieces dashing
    # the scalar lines adds; every one of them is owned by a line (owner >= 0).
    assert show.n_line_points >= sum(len(p) for p in show.field.polys)
    assert show.n_line_points == int((show._owner >= 0).sum())


def test_marks_toggle_changes_only_the_marks():
    on = FeynmanShow(os.path.join(_FIELDS, "16x9.json"), marks=True, seed=3)
    off = FeynmanShow(os.path.join(_FIELDS, "16x9.json"), marks=False, seed=3)
    assert off.n_points == off.n_line_points == on.n_line_points
    assert on.n_points > off.n_points


def test_lines_grow_out_of_one_end_and_only_forward():
    """A line draws itself out of the vertex the front reached, monotonically.

    Growth is carried by alpha, so a line that grew backwards or flickered
    would show up here as an alpha prefix that shrinks.
    """
    show = FeynmanShow(os.path.join(_FIELDS, "16x9.json"), walkers=1,
                       tail=0.9, traverse=20.0, seed=11)
    fl = show.flood
    seen = {}
    shrank = 0
    for _ in range(400):
        show.step(1 / 30)
        for i in (7, 40, 123, 300, 500):
            g = float(fl.grow[i])
            if g <= 0:
                continue
            if i in seen and g + 1e-6 < seen[i] and fl.tone[i] > 0.99:
                shrank += 1
            seen[i] = max(g, seen.get(i, 0.0))
    assert shrank == 0
    assert len(seen) == 5, "the flood never reached some of the sampled lines"


def test_a_short_lifetime_keeps_the_pattern_turning_over():
    """The dial that decides whether the field evolves or just fills up.

    With a long tail almost everything alight now was alight ten seconds ago;
    with a short one most of it is new. Both are useful live, and this is what
    separates them.
    """
    def churn(tail, walkers):
        show = FeynmanShow(os.path.join(_FIELDS, "16x9.json"), walkers=walkers,
                           tail=tail, traverse=30.0, fade=0.5, seed=4)
        seen, new, tot, low = None, 0, 0, 1.0
        for k in range(1800):                      # a minute at 30 fps
            show.step(1 / 30)
            if k % 300 == 0:                       # every ten seconds
                lit = show.flood.tone > 0.5
                low = min(low, float(lit.mean()))
                if seen is not None and lit.any():
                    new += int((lit & ~seen).sum())
                    tot += int(lit.sum())
                seen = lit
        return (new / tot if tot else 0.0), low

    fresh_short, floor_short = churn(0.3, 3)
    fresh_long, _ = churn(1.0, 3)
    assert fresh_short > 0.5, "a short lifetime should keep turning the field over"
    assert fresh_long < fresh_short, "a long lifetime should hold the field still"
    assert floor_short > 0.0, "three fronts should never leave the field empty"


def test_nbody_gram_forces_match_the_direct_pair_sum():
    """The live kernel forms pair distances from the Gram matrix and sums
    forces with a matmul. Check it against the explicit (N, N, 3) difference
    formulation it replaced, for every initial-condition family."""
    def direct(sim):
        diff = sim.pos[None, :, :] - sim.pos[:, None, :]
        r2 = np.einsum("ijk,ijk->ij", diff, diff) + sim.softening ** 2
        inv_r3 = 1.0 / (r2 * np.sqrt(r2))
        np.fill_diagonal(inv_r3, 0.0)
        return sim.G * np.einsum("ij,ijk->ik", inv_r3 * sim.mass[None, :], diff)

    for ctor in (NBodySim.two_galaxies, NBodySim.rotating_disk, NBodySim.plummer_sphere):
        sim = ctor(n=150, seed=9, softening=0.05)
        a, b = sim._accelerations(), direct(sim)
        assert np.abs(a - b).max() < 1e-10 * np.abs(b).max()


def test_ising_lut_matches_the_general_metropolis_rule():
    """With no external field the acceptance probability is a 9-entry table;
    it must reproduce exp(-dE/T) for every (spin, neighbour-sum) combination,
    and a non-zero field must still take the general path."""
    m = IsingModel(size=16, temperature=1.7, seed=2)
    nbr = m._neighbour_sum()
    lut = m._accept_lut()[m.spins * nbr + 4]
    general = np.exp(np.minimum(-(2.0 * m.spins * (m.coupling * nbr)) / m.temperature, 0.0))
    assert np.abs(lut - general).max() < 1e-6
    # temperature change refreshes the table
    m.temperature = 0.9
    assert m._accept_lut()[0] < lut.min() + 1e-9 or m._lut_key[0] == 0.9
    with_field = IsingModel(size=16, temperature=1.7, field=0.3, seed=2)
    with_field.step(3)  # exercises the general branch
    assert set(np.unique(with_field.spins)).issubset({-1, 1})


def test_curl_flow_interpolation_matches_three_index_gather():
    """velocity_at fetches trilinear corners with 1-D take on a flat field;
    it must agree exactly with the straightforward three-index gather."""
    f = CurlNoiseFlow(n=3000, seed=1)
    pos = f.pos
    R = f.grid_res
    g = np.clip((pos + f.bounds) / (2 * f.bounds) * (R - 1), 0, R - 1 - 1e-4)
    i = np.floor(g).astype(np.int32)
    fr = g - i
    ix, iy, iz = i[:, 0], i[:, 1], i[:, 2]
    fx, fy, fz = fr[:, 0:1], fr[:, 1:2], fr[:, 2:3]
    fld = f.field
    c00 = fld[ix, iy, iz] * (1 - fx) + fld[ix + 1, iy, iz] * fx
    c10 = fld[ix, iy + 1, iz] * (1 - fx) + fld[ix + 1, iy + 1, iz] * fx
    c01 = fld[ix, iy, iz + 1] * (1 - fx) + fld[ix + 1, iy, iz + 1] * fx
    c11 = fld[ix, iy + 1, iz + 1] * (1 - fx) + fld[ix + 1, iy + 1, iz + 1] * fx
    ref = (c00 * (1 - fy) + c10 * fy) * (1 - fz) + (c01 * (1 - fy) + c11 * fy) * fz
    assert np.array_equal(f.velocity_at(pos), ref)


def test_softbody_punch_barrels_through_and_settles():
    """A punch sends a bulge across the body: the deviation from the matched
    shape rises well above rest, then shape matching restores it."""
    body = ShapeMatchedSoftBody(n=1200, radius=2.0, seed=6, wobble=0.0)
    for _ in range(30):
        body.step(1 / 60)
    rest = float(body.stress().mean())
    body.punch(1.0, direction=(1.0, 0.0, 0.0))
    peak = 0.0
    for _ in range(90):
        body.step(1 / 60)
        peak = max(peak, float(body.stress().mean()))
    assert peak > 6 * rest + 0.05, (peak, rest)
    for _ in range(240):
        body.step(1 / 60)
    assert float(body.stress().mean()) < 3 * rest + 0.02
    assert np.isfinite(body.pos).all() and not body._waves


def test_flow_punch_blasts_outward_then_fades():
    flow = CurlNoiseFlow(n=3000, bounds=5.0, seed=3)
    r0 = np.linalg.norm(flow.pos, axis=1).mean()
    flow.punch(1.0)
    flow.step(1 / 60)
    outward = ((flow.vel * flow.pos).sum(axis=1) > 0).mean()
    assert outward > 0.9                         # nearly everything moving out
    for _ in range(180):
        flow.step(1 / 60)
    assert flow._burst == 0.0
    assert np.abs(flow.pos).max() <= 5.0 + 1e-6  # still wrapped in the box


def test_scalar_lines_are_drawn_dashed():
    """A Higgs (scalar) propagator is dashed, per the Feynman convention:
    several two-point polylines along the same segment, ink at both ends, and
    their 'along' spans still let the flood light the line progressively."""
    from physics.feynman import dash_spans, SCALAR
    for L in (3.0, 8.0, 20.0, 47.0):
        spans = dash_spans(L)
        assert len(spans) >= 3 and len(spans) % 2 == 1
        assert spans[0][0] == 0.0 and abs(spans[-1][1] - 1.0) < 1e-9
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            assert a1 < b0                       # a real gap between dashes
    show = FeynmanShow(os.path.join(_FIELDS, "16x9.json"), marks=False, seed=3)
    f = show.field
    scalars = [i for i in range(f.n_edges) if int(f.etype[i]) == SCALAR and len(f.polys[i]) == 2]
    if scalars:
        owner = show._owner
        for i in scalars[:3]:
            n_pieces = int((owner == i).sum()) // 2
            assert n_pieces >= 3
    # the polyline list and the colour buffer still agree point for point
    assert sum(len(p) for p in show.polys) == show.n_points == len(show.colours())

