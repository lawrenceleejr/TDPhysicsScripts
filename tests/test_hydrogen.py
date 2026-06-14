"""Tests for the hydrogen orbital + Bohmian dynamics core."""
import numpy as np

from physics.hydrogen import HydrogenState, PRESET_NAMES, _Rnl, _ylm, psi_nlm

# numpy 2.0 renamed trapz -> trapezoid; support both.
_trapz = getattr(np, "trapezoid", None) or np.trapz


def test_radial_normalization():
    """integral |R_nl|^2 r^2 dr == 1 for several states."""
    r = np.linspace(1e-4, 60.0, 40000)
    for n, l in [(1, 0), (2, 0), (2, 1), (3, 1), (3, 2)]:
        R = _Rnl(n, l, r)
        norm = _trapz(R * R * r * r, r)
        assert abs(norm - 1.0) < 1e-2, (n, l, norm)


def test_spherical_harmonic_normalization():
    """integral |Y_l^m|^2 dOmega == 1."""
    th = np.linspace(0, np.pi, 400)
    ph = np.linspace(0, 2 * np.pi, 400)
    TH, PH = np.meshgrid(th, ph, indexing="ij")
    for l, m in [(0, 0), (1, 0), (1, 1), (2, 2), (2, -1)]:
        Y = _ylm(l, m, TH, PH)
        integ = _trapz(_trapz((np.abs(Y) ** 2) * np.sin(TH), ph, axis=1), th)
        assert abs(integ - 1.0) < 2e-2, (l, m, integ)


def test_1s_is_static():
    """A real m=0 eigenstate (1s) has ~zero Bohmian velocity everywhere."""
    st = HydrogenState([(1.0, 1, 0, 0)])
    rng = np.random.default_rng(0)
    pts = (rng.random((200, 3)) * 2 - 1) * 3.0
    v = st.bohm_velocity(pts)
    assert np.nanmax(np.linalg.norm(v, axis=1)) < 1e-3


def test_2p_m1_circulates_about_z():
    """psi_{2,1,+1} drives pure azimuthal (+phi) circulation: v_z ~ 0,
    radial component ~ 0, and v.phihat > 0."""
    st = HydrogenState([(1.0, 2, 1, 1)])
    # Points off the z-axis at mid radius.
    ang = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    r0, z0 = 3.0, 1.0
    pts = np.stack([r0 * np.cos(ang), r0 * np.sin(ang), np.full_like(ang, z0)], axis=1)
    v = st.bohm_velocity(pts)
    phi = np.arctan2(pts[:, 1], pts[:, 0])
    phihat = np.stack([-np.sin(phi), np.cos(phi), np.zeros_like(phi)], axis=1)
    rhat_xy = np.stack([np.cos(phi), np.sin(phi), np.zeros_like(phi)], axis=1)
    vphi = np.einsum("ij,ij->i", v, phihat)
    vrad = np.einsum("ij,ij->i", v, rhat_xy)
    assert np.all(vphi > 0)                       # circulates in +phi (m>0)
    assert np.max(np.abs(v[:, 2])) < 1e-3         # no z motion
    assert np.max(np.abs(vrad)) < 1e-2 * np.mean(vphi)


def test_negative_m_reverses_circulation():
    pos = np.array([[3.0, 0.5, 1.0]])
    vp = HydrogenState([(1.0, 2, 1, 1)]).bohm_velocity(pos)
    vm = HydrogenState([(1.0, 2, 1, -1)]).bohm_velocity(pos)
    phi = np.arctan2(pos[0, 1], pos[0, 0])
    phihat = np.array([-np.sin(phi), np.cos(phi), 0.0])
    assert (vp[0] @ phihat) * (vm[0] @ phihat) < 0


def test_superposition_is_time_dependent():
    """Different-energy superposition: density and velocity evolve in time."""
    st = HydrogenState([(1.0, 1, 0, 0), (1.0, 2, 1, 0)])
    pts = np.array([[0.5, 0.0, 1.5], [0.0, 0.0, -1.5]])
    d0 = st.density(pts, t=0.0)
    d1 = st.density(pts, t=3.0)
    assert np.max(np.abs(d0 - d1)) > 1e-3
    v = st.bohm_velocity(pts, t=1.0)
    assert np.linalg.norm(v) > 1e-4


def test_sample_density_matches_1s_mean_radius():
    """<r> for the 1s state is 1.5 a0; rejection sampler should reproduce it."""
    st = HydrogenState([(1.0, 1, 0, 0)])
    pts = st.sample_density(8000, rng=np.random.default_rng(1), extent=8.0)
    mean_r = np.mean(np.linalg.norm(pts, axis=1))
    assert abs(mean_r - 1.5) < 0.2


def test_presets_build_and_evaluate():
    pos = np.array([[1.0, 1.0, 1.0]])
    for name in PRESET_NAMES:
        st = HydrogenState.preset(name)
        assert np.isfinite(st.density(pos)).all()
        assert np.isfinite(st.bohm_velocity(pos)).all()
