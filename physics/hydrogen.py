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


def _ipow(x, k):
    """x**k for a small non-negative integer k, by multiplication (np.power
    with a float exponent is several times slower over a large array)."""
    if k == 0:
        return np.ones_like(x)
    out = x
    for _ in range(k - 1):
        out = out * x
    return out


def _Rnl_and_deriv(n, l, r):
    """R_{nl}(r) and dR_{nl}/dr together (a0 = 1), sharing the recurrences.

    With rho = 2r/n and k = n-l-1, alpha = 2l+1:
        R = N e^{-rho/2} rho^l L_k^alpha(rho)
        dR/drho = N e^{-rho/2} [ rho^l (-L/2 + L') + l rho^{l-1} L ],
    and L' = d/drho L_k^alpha = -L_{k-1}^{alpha+1} (the standard identity).
    """
    rho = 2.0 * r / n
    k, alpha = n - l - 1, 2 * l + 1
    norm = sqrt((2.0 / n) ** 3 * factorial(n - l - 1) / (2.0 * n * factorial(n + l)))
    L = _laguerre(k, alpha, rho)
    dL = -_laguerre(k - 1, alpha + 1, rho) if k > 0 else np.zeros_like(rho)
    e = norm * np.exp(-rho / 2.0)
    rl = _ipow(rho, l)
    R = e * rl * L
    dR = e * rl * (dL - 0.5 * L)
    if l > 0:
        dR = dR + e * (l * _ipow(rho, l - 1)) * L
    return R, dR * (2.0 / n)


def _plgndr_and_dtheta(l, m, ct, st_safe):
    """P_l^m(cos theta) and dP_l^m/dtheta, m >= 0, from the same recurrence.

    Abramowitz & Stegun 8.5.4 gives (1 - x^2) dP/dx = (l+m) P_{l-1}^m - l x P_l^m
    so, with x = cos theta, dP/dtheta = (l x P_l^m - (l+m) P_{l-1}^m) / sin theta.
    ``st_safe`` is sin theta clamped away from zero; the numerator vanishes at
    the poles at least as fast, so the ratio stays finite.
    """
    P = _plgndr(l, m, ct)
    Pm1 = _plgndr(l - 1, m, ct) if l >= 1 else np.zeros_like(ct)
    dP = (l * ct * P - (l + m) * Pm1) / st_safe
    return P, dP


def _unit_phase_power(cp, sp, m):
    """exp(i m phi) from cos phi, sin phi without a transcendental call.

    (cp + i sp) is exp(i phi); raise it to the (small, integer) power by
    multiplication. Falls back to exp() for unusually large |m|.
    """
    if m == 0:
        return np.ones_like(cp, dtype=np.complex128)
    base = cp + 1j * sp if m > 0 else cp - 1j * sp
    am = abs(int(m))
    if am > 8:
        return np.exp(1j * m * np.arctan2(sp, cp))
    out = base
    for _ in range(am - 1):
        out = out * base
    return out


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

    def _eval(self, pos, t=0.0, grad=False):
        """psi at Cartesian ``pos`` (N, 3), and optionally grad psi in
        *spherical* components, with the local frame.

        Returns ``psi`` when ``grad`` is False; otherwise
        ``(psi, g_r, g_th, g_ph, frame)`` with ``frame = (st, ct, cp, sp)``
        (sin/cos theta, cos/sin phi). The angles are never materialised: the
        trig comes straight from the coordinates and exp(i m phi) from a
        complex power, so there is no arccos, arctan2 or complex exp over the
        cloud. The gradient is analytic (d/dr, d/dtheta, d/dphi of R_nl Y_lm),
        which is what makes the Bohmian velocity one wavefunction pass instead
        of the seven a finite-difference stencil needs.

        On the z-axis cos/sin phi come out as 0/0 -> 0; that is harmless: every
        m != 0 term vanishes there anyway and m = 0 terms do not use them.
        """
        pos = np.asarray(pos, dtype=np.float64)
        x, y, z = pos[..., 0], pos[..., 1], pos[..., 2]
        rho2 = x * x + y * y
        r = np.sqrt(rho2 + z * z)
        rxy = np.sqrt(rho2)
        r_safe = np.maximum(r, 1e-12)
        inv_r = 1.0 / r_safe
        inv_rxy = 1.0 / np.maximum(rxy, 1e-12)
        ct = np.clip(z * inv_r, -1.0, 1.0)
        st = rxy * inv_r
        st_safe = np.maximum(st, 1e-12)
        cp = x * inv_rxy
        sp = y * inv_rxy

        psi = np.zeros(pos.shape[:-1], dtype=np.complex128)
        if grad:
            g_r = np.zeros_like(psi)      # spherical components of grad psi
            g_th = np.zeros_like(psi)
            g_ph = np.zeros_like(psi)
            inv_r_st = inv_r / st_safe

        for c, n, l, m in self.terms:
            energy = -0.5 / (n * n)               # E_n in Hartree
            a = c * np.exp(-1j * energy * t)
            am = abs(m)
            sign = ((-1) ** am) if m < 0 else 1.0
            norm = sign * sqrt((2 * l + 1) / (4 * pi) * factorial(l - am) / factorial(l + am))
            aE = a * _unit_phase_power(cp, sp, m)    # a * exp(i m phi), complex N
            if grad:
                R, dR = _Rnl_and_deriv(n, l, r)
                P, dP = _plgndr_and_dtheta(l, am, ct, st_safe)
                nP = norm * P
                # Everything below the multiply by aE is real arithmetic.
                psi += (R * nP) * aE
                g_r += (dR * nP) * aE
                g_th += (R * inv_r * norm * dP) * aE
                if m != 0:
                    g_ph += (R * inv_r_st * nP * m) * (1j * aE)
            else:
                psi += (_Rnl(n, l, r) * (norm * _plgndr(l, am, ct))) * aE

        if not grad:
            return psi
        return psi, g_r, g_th, g_ph, (st, ct, cp, sp)

    def psi(self, pos, t=0.0):
        """Complex wavefunction at positions ``pos`` and time ``t``."""
        return self._eval(pos, t, grad=False)

    def psi_grad(self, pos, t=0.0):
        """(psi, grad psi) -- grad is complex (N, 3) in Cartesian components."""
        psi, g_r, g_th, g_ph, (st, ct, cp, sp) = self._eval(pos, t, grad=True)
        gx = g_r * (st * cp) + g_th * (ct * cp) - g_ph * sp
        gy = g_r * (st * sp) + g_th * (ct * sp) + g_ph * cp
        gz = g_r * ct - g_th * st
        return psi, np.stack([gx, gy, gz], axis=-1)

    def density(self, pos, t=0.0):
        p = self.psi(pos, t)
        return (p.real ** 2 + p.imag ** 2)

    def bohm_velocity(self, pos, t=0.0):
        """Bohmian guidance velocity v = Im(grad psi / psi) (hbar = m = 1).

        Analytic gradient, one wavefunction pass. Im(grad psi conj psi) is
        taken per spherical component, so the change to Cartesian components
        is real arithmetic. Where |psi|^2 is below 1e-12 the velocity is set
        to zero (the caller respawns such electrons rather than flinging them
        along a near-singular direction).
        """
        psi, g_r, g_th, g_ph, (st, ct, cp, sp) = self._eval(pos, t, grad=True)
        pc = np.conj(psi)
        s_r = (g_r * pc).imag
        s_th = (g_th * pc).imag
        s_ph = (g_ph * pc).imag
        denom = psi.real * psi.real + psi.imag * psi.imag
        inv = np.where(denom > 1e-12, 1.0 / np.maximum(denom, 1e-300), 0.0)
        s_r *= inv
        s_th *= inv
        s_ph *= inv
        v = np.empty(pos.shape if hasattr(pos, "shape") else np.shape(pos), dtype=np.float64)
        v[..., 0] = s_r * (st * cp) + s_th * (ct * cp) - s_ph * sp
        v[..., 1] = s_r * (st * sp) + s_th * (ct * sp) + s_ph * cp
        v[..., 2] = s_r * ct - s_th * st
        return v

    def bohm_velocity_fd(self, pos, t=0.0, eps=1e-4):
        """Reference: the same velocity by central finite differences (slow;
        kept for tests that cross-check the analytic gradient)."""
        pos = np.asarray(pos, dtype=np.float64)
        psi0 = self.psi(pos, t)
        denom = (psi0.real ** 2 + psi0.imag ** 2)
        v = np.zeros_like(pos)
        for ax in range(3):
            d = np.zeros_like(pos)
            d[..., ax] = eps
            grad = (self.psi(pos + d, t) - self.psi(pos - d, t)) / (2.0 * eps)
            v[..., ax] = (grad * np.conj(psi0)).imag
        safe = denom > 1e-12
        v[safe] /= denom[safe, None]
        v[~safe] = 0.0
        return v

    @staticmethod
    def _ball(n, radius, rng):
        """``n`` points uniformly distributed in a ball of the given radius."""
        d = rng.normal(size=(n, 3))
        d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-12
        r = radius * np.cbrt(rng.random(n))
        return d * r[:, None]

    def sample_density(self, n_samples, t=0.0, extent=None, rng=None, batch=16384):
        """Rejection-sample ``n_samples`` positions distributed as |psi|^2.

        Proposals are uniform within a *ball* of radius ``extent`` rather than a
        cube: orbitals are roughly spherical, so the ball wastes far fewer
        proposals on empty corners (~2x acceptance), cutting scene-init time.
        """
        rng = rng or np.random.default_rng()
        if extent is None:
            extent = 6.0 * self.nmax ** 2 * 0.5 + 6.0
        # Estimate the peak density to set the rejection ceiling.
        dmax = float(self.density(self._ball(12000, extent, rng), t).max()) * 1.25 + 1e-12
        out = np.empty((n_samples, 3), dtype=np.float64)
        filled = 0
        while filled < n_samples:
            cand = self._ball(batch, extent, rng)
            keep = cand[rng.random(batch) * dmax < self.density(cand, t)]
            take = min(len(keep), n_samples - filled)
            out[filled:filled + take] = keep[:take]
            filled += take
        return out
