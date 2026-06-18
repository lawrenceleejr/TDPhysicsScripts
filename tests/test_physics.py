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
