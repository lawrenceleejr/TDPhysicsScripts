"""Hydrogen orbitals + de Broglie-Bohm (pilot-wave) particle dynamics.

Pure numpy, no TouchDesigner dependency -- the testable core behind the
"Bohmian hydrogen" POP scene. We build the hydrogen energy eigenstates
psi_{nlm} = R_{nl}(r) Y_l^m(theta, phi) (atomic units: hbar = m_e = a0 = 1),
allow time-dependent superpositions, and integrate electrons along the Bohmian
guidance equation

    v = (hbar/m) Im(grad psi / psi)

so a cloud of electrons sampled from |psi|^2 flows deterministically. For a
single eigenstate with magnetic quantum number m this is pure circulation about
the z-axis at angular rate proportional to m; for m = 0 / real orbitals the
electrons are (almost) at rest; superpositions of different energies oscillate.
This is the picture MinutePhysics shows.
"""
from math import factorial, pi, sqrt

import numpy as np

# Named superpositions, each a list of (coefficient, n, l, m) terms. Different
# energies (n) -> genuinely time-dependent, swirling/sloshing motion.
PRESETS = {
    "2p circulating":   [(1.0, 2, 1, 1)],
    "3d circulating":   [(1.0, 3, 2, 2)],
    "3p circulating":   [(1.0, 3, 1, 1)],
    "1s+2pz sloshing":  [(0.70710678, 1, 0, 0), (0.70710678, 2, 1, 0)],
    "1s+2p swirl":      [(0.70710678, 1, 0, 0), (0.70710678, 2, 1, 1)],
    "2pz static":       [(1.0, 2, 1, 0)],
}
PRESET_NAMES = list(PRESETS.keys())


def _laguerre(k, alpha, x):
    """Generalised Laguerre L_k^alpha(x), vectorised over x, via recurrence."""
    if k < 0:
        return np.zeros_like(x)
    lkm1 = np.ones_like(x)              # L_0
    if k == 0:
        return lkm1
    lk = 1.0 + alpha - x               # L_1
    for i in range(1, k):
        lkp1 = ((2 * i + 1 + alpha - x) * lk - (i + alpha) * lkm1) / (i + 1)
        lkm1, lk = lk, lkp1
    return lk


def _plgndr(l, m, x):
    """Associated Legendre P_l^m(x) (m >= 0), vectorised. Numerical-Recipes
    recurrence, including the Condon-Shortley (-1)^m phase."""
    m = abs(m)
    if m > l:
        return np.zeros_like(x)
    pmm = np.ones_like(x)
    if m > 0:
        somx2 = np.sqrt(np.maximum((1.0 - x) * (1.0 + x), 0.0))
        fact = 1.0
        for _ in range(m):
            pmm = pmm * (-fact) * somx2
            fact += 2.0
    if l == m:
        return pmm
    pmmp1 = x * (2 * m + 1) * pmm
    if l == m + 1:
        return pmmp1
    pll = pmmp1
    for ll in range(m + 2, l + 1):
        pll = (x * (2 * ll - 1) * pmmp1 - (ll + m - 1) * pmm) / (ll - m)
        pmm, pmmp1 = pmmp1, pll
    return pll


def _ylm(l, m, theta, phi):
    """Complex spherical harmonic Y_l^m(theta, phi), vectorised."""
    am = abs(m)
    norm = sqrt((2 * l + 1) / (4 * pi) * factorial(l - am) / factorial(l + am))
    p = _plgndr(l, am, np.cos(theta))
    y = norm * p * np.exp(1j * m * phi)
    if m < 0:
        y = ((-1) ** am) * np.conj(_ylm(l, am, theta, phi))
    return y


def _Rnl(n, l, r):
    """Radial hydrogen wavefunction R_{nl}(r), vectorised (a0 = 1)."""
    rho = 2.0 * r / n
    norm = sqrt((2.0 / n) ** 3 * factorial(n - l - 1) / (2.0 * n * factorial(n + l)))
    return norm * np.exp(-rho / 2.0) * rho ** l * _laguerre(n - l - 1, 2 * l + 1, rho)


def psi_nlm(n, l, m, pos):
    """Complex psi_{nlm} evaluated at Cartesian positions ``pos`` (..., 3)."""
    pos = np.asarray(pos, dtype=np.float64)
    x, y, z = pos[..., 0], pos[..., 1], pos[..., 2]
    r = np.sqrt(x * x + y * y + z * z)
    rsafe = np.where(r > 0, r, 1.0)
    theta = np.arccos(np.clip(z / rsafe, -1.0, 1.0))
    phi = np.arctan2(y, x)
    return _Rnl(n, l, r) * _ylm(l, m, theta, phi)


class HydrogenState:
    """A (possibly time-dependent) superposition of hydrogen eigenstates."""

    def __init__(self, terms):
        # terms: iterable of (coef, n, l, m). Stored normalised.
        self.terms = [(complex(c), int(n), int(l), int(m)) for c, n, l, m in terms]
        norm = sqrt(sum(abs(c) ** 2 for c, _, _, _ in self.terms)) or 1.0
        self.terms = [(c / norm, n, l, m) for c, n, l, m in self.terms]

    @classmethod
    def preset(cls, name):
        return cls(PRESETS.get(name, PRESETS["2p circulating"]))

    @property
    def nmax(self):
        return max(n for _, n, _, _ in self.terms)

    def psi(self, pos, t=0.0):
        """Complex wavefunction at positions ``pos`` and time ``t``."""
        pos = np.asarray(pos, dtype=np.float64)
        out = np.zeros(pos.shape[:-1], dtype=np.complex128)
        for c, n, l, m in self.terms:
            energy = -0.5 / (n * n)               # E_n in Hartree
            out = out + c * psi_nlm(n, l, m, pos) * np.exp(-1j * energy * t)
        return out

    def density(self, pos, t=0.0):
        p = self.psi(pos, t)
        return (p.real ** 2 + p.imag ** 2)

    def bohm_velocity(self, pos, t=0.0, eps=1e-4):
        """Bohmian guidance velocity v = Im(grad psi / psi) (hbar = m = 1)."""
        pos = np.asarray(pos, dtype=np.float64)
        psi0 = self.psi(pos, t)
        denom = (psi0.real ** 2 + psi0.imag ** 2)
        v = np.zeros_like(pos)
        for ax in range(3):
            d = np.zeros_like(pos)
            d[..., ax] = eps
            grad = (self.psi(pos + d, t) - self.psi(pos - d, t)) / (2.0 * eps)
            # Im(grad/psi) = Im(grad * conj(psi)) / |psi|^2
            v[..., ax] = (grad * np.conj(psi0)).imag
        safe = denom > 1e-12
        v[safe] /= denom[safe, None]
        v[~safe] = 0.0
        return v

    def sample_density(self, n_samples, t=0.0, extent=None, rng=None, batch=4096):
        """Rejection-sample ``n_samples`` positions distributed as |psi|^2."""
        rng = rng or np.random.default_rng()
        if extent is None:
            extent = 6.0 * self.nmax ** 2 * 0.5 + 6.0
        # Estimate the peak density to set the rejection ceiling.
        probe = (rng.random((20000, 3)) * 2 - 1) * extent
        dmax = float(self.density(probe, t).max()) * 1.3 + 1e-12
        out = np.empty((n_samples, 3), dtype=np.float64)
        filled = 0
        while filled < n_samples:
            cand = (rng.random((batch, 3)) * 2 - 1) * extent
            keep = cand[rng.random(batch) * dmax < self.density(cand, t)]
            take = min(len(keep), n_samples - filled)
            out[filled:filled + take] = keep[:take]
            filled += take
        return out
