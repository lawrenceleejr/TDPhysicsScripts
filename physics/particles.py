"""Particle systems: turbulent curl-noise flow and a rotating soft body.

Two independent systems share this module because they feed the same
TouchDesigner instancing pipeline (a CHOP of per-particle tx/ty/tz + colour):

* ``CurlNoiseFlow`` advects particles through the curl of a 3D Perlin noise
  vector potential. The curl of any field is divergence-free, so the flow is
  incompressible -- it swirls and folds like smoke/turbulence without
  particles bunching up or thinning out. For speed the vector potential is
  evaluated on a small 3D grid and the curl is taken with ``np.gradient``
  (3 noise evaluations per frame instead of 12 finite-difference samples);
  particle velocities are then trilinearly interpolated from that grid, so
  cost barely depends on particle count and tens of thousands of particles
  run in real time.

* ``ShapeMatchedSoftBody`` is a Mueller-style shape-matching soft body: a
  cloud of particles is pulled toward the best-fit rigid transform of its
  rest shape every step, while a steady spin is injected. The result is a
  cohesive blob that rotates and wobbles like jelly.
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Vectorized 3D Perlin gradient noise
# ---------------------------------------------------------------------------
_FADE = lambda t: t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
_LERP = lambda a, b, t: a + t * (b - a)

# Ken Perlin's 12 gradient directions, padded to 16 (the pad repeats four of
# them, which is the standard trick to allow a cheap ``hash & 15`` lookup).
_GRADIENTS = np.array(
    [
        [1, 1, 0], [-1, 1, 0], [1, -1, 0], [-1, -1, 0],
        [1, 0, 1], [-1, 0, 1], [1, 0, -1], [-1, 0, -1],
        [0, 1, 1], [0, -1, 1], [0, 1, -1], [0, -1, -1],
        [1, 1, 0], [0, -1, 1], [-1, 1, 0], [0, -1, -1],
    ],
    dtype=np.float32,
)


class NoiseField:
    """Classic Perlin 3D noise with a randomised permutation table."""

    def __init__(self, seed: int | None = None):
        rng = np.random.default_rng(seed)
        p = rng.permutation(256)
        self.perm = np.concatenate([p, p]).astype(np.int32)  # length 512

    def _grad(self, h: np.ndarray, x, y, z) -> np.ndarray:
        g = _GRADIENTS[h & 15]
        return g[:, 0] * x + g[:, 1] * y + g[:, 2] * z

    def noise(self, pts: np.ndarray) -> np.ndarray:
        """Perlin noise sampled at ``pts`` (N, 3) -> (N,) roughly in [-1, 1]."""
        p = self.perm
        pts = np.asarray(pts, dtype=np.float32)
        x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
        xi = np.floor(x).astype(np.int32) & 255
        yi = np.floor(y).astype(np.int32) & 255
        zi = np.floor(z).astype(np.int32) & 255
        xf = x - np.floor(x)
        yf = y - np.floor(y)
        zf = z - np.floor(z)
        u, v, w = _FADE(xf), _FADE(yf), _FADE(zf)

        # Reuse intermediate hashes (the 8 cube-corner indices).
        A = p[xi] + yi
        AA = p[A] + zi
        AB = p[A + 1] + zi
        B = p[xi + 1] + yi
        BA = p[B] + zi
        BB = p[B + 1] + zi

        g = self._grad
        x1 = _LERP(g(p[AA], xf, yf, zf), g(p[BA], xf - 1, yf, zf), u)
        x2 = _LERP(g(p[AB], xf, yf - 1, zf), g(p[BB], xf - 1, yf - 1, zf), u)
        y1 = _LERP(x1, x2, v)
        x1 = _LERP(g(p[AA + 1], xf, yf, zf - 1), g(p[BA + 1], xf - 1, yf, zf - 1), u)
        x2 = _LERP(g(p[AB + 1], xf, yf - 1, zf - 1), g(p[BB + 1], xf - 1, yf - 1, zf - 1), u)
        y2 = _LERP(x1, x2, v)
        return _LERP(y1, y2, w)


# ---------------------------------------------------------------------------
# Curl-noise turbulent flow (grid-accelerated, divergence-free)
# ---------------------------------------------------------------------------
class CurlNoiseFlow:
    # Offsets to decorrelate the three components of the vector potential.
    _OFFSETS = (
        np.array([0.0, 0.0, 0.0], dtype=np.float32),
        np.array([31.4, 17.0, 8.1], dtype=np.float32),
        np.array([-12.6, 5.7, 43.2], dtype=np.float32),
    )

    def __init__(
        self,
        n: int = 30000,
        bounds: float = 6.0,
        grid_res: int = 24,
        freq: float = 0.22,
        speed: float = 1.6,
        evolve: float = 0.12,
        seed: int | None = None,
    ):
        self._rng = np.random.default_rng(seed)
        self.n = int(n)
        self.bounds = float(bounds)
        self.grid_res = int(grid_res)
        self.freq = float(freq)
        self.speed = float(speed)
        self.evolve = float(evolve)
        self._noise = NoiseField(seed)

        # Static world-space grid covering [-bounds, bounds]^3.
        R = self.grid_res
        axis = np.linspace(-self.bounds, self.bounds, R, dtype=np.float32)
        gx, gy, gz = np.meshgrid(axis, axis, axis, indexing="ij")
        self._grid = np.stack([gx, gy, gz], axis=-1)  # (R, R, R, 3)
        self._grid_flat = self._grid.reshape(-1, 3)
        self._spacing = (2.0 * self.bounds) / (R - 1)

        self.pos = self._rng.uniform(-bounds, bounds, size=(self.n, 3)).astype(np.float32)
        self.vel = np.zeros_like(self.pos)
        self.t = 0.0
        self.field = self._build_field()
        self.last_frame = -1

    @property
    def positions(self) -> np.ndarray:
        return self.pos

    def speeds(self) -> np.ndarray:
        return np.sqrt((self.vel * self.vel).sum(axis=1)).astype(np.float32)

    def _build_field(self) -> np.ndarray:
        """Curl of a noise vector potential on the grid -> (R, R, R, 3)."""
        R = self.grid_res
        drift = self.evolve * self.t
        base = self._grid_flat * self.freq + drift
        # Three potential components.
        psi = [
            self._noise.noise(base + self._OFFSETS[c]).reshape(R, R, R)
            for c in range(3)
        ]
        h = self._spacing
        # Curl = (dψz/dy - dψy/dz, dψx/dz - dψz/dx, dψy/dx - dψx/dy).
        g0 = np.gradient(psi[0], h)  # [d/dx, d/dy, d/dz]
        g1 = np.gradient(psi[1], h)
        g2 = np.gradient(psi[2], h)
        vx = g2[1] - g1[2]
        vy = g0[2] - g2[0]
        vz = g1[0] - g0[1]
        return np.stack([vx, vy, vz], axis=-1).astype(np.float32)

    def velocity_at(self, pos: np.ndarray) -> np.ndarray:
        """Trilinearly interpolate the current curl field at ``pos`` (N, 3)."""
        R = self.grid_res
        g = (pos + self.bounds) / (2.0 * self.bounds) * (R - 1)
        g = np.clip(g, 0.0, R - 1.0 - 1e-4)
        i = np.floor(g).astype(np.int32)
        f = g - i
        ix, iy, iz = i[:, 0], i[:, 1], i[:, 2]
        ix1, iy1, iz1 = ix + 1, iy + 1, iz + 1
        fx, fy, fz = f[:, 0:1], f[:, 1:2], f[:, 2:3]
        fld = self.field
        c00 = fld[ix, iy, iz] * (1 - fx) + fld[ix1, iy, iz] * fx
        c10 = fld[ix, iy1, iz] * (1 - fx) + fld[ix1, iy1, iz] * fx
        c01 = fld[ix, iy, iz1] * (1 - fx) + fld[ix1, iy, iz1] * fx
        c11 = fld[ix, iy1, iz1] * (1 - fx) + fld[ix1, iy1, iz1] * fx
        c0 = c00 * (1 - fy) + c10 * fy
        c1 = c01 * (1 - fy) + c11 * fy
        return c0 * (1 - fz) + c1 * fz

    def step(self, dt: float = 1.0 / 60.0) -> None:
        self.t += dt
        self.field = self._build_field()
        self.vel = self.velocity_at(self.pos) * self.speed
        self.pos = self.pos + self.vel * dt
        # Wrap through the box so the population stays put and recirculates.
        b = self.bounds
        self.pos = ((self.pos + b) % (2 * b)) - b


# ---------------------------------------------------------------------------
# Rotating soft body (shape matching)
# ---------------------------------------------------------------------------
def _rotation_matrix(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / (np.linalg.norm(axis) + 1e-12)
    x, y, z = axis
    c, s = np.cos(angle), np.sin(angle)
    C = 1.0 - c
    return np.array(
        [
            [c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C],
        ]
    )


class ShapeMatchedSoftBody:
    def __init__(
        self,
        n: int = 6000,
        radius: float = 2.2,
        stiffness: float = 0.55,
        damping: float = 0.06,
        spin: float = 1.1,
        axis=(0.2, 1.0, 0.1),
        wobble: float = 0.4,
        seed: int | None = None,
    ):
        self._rng = np.random.default_rng(seed)
        self.n = int(n)
        self.stiffness = float(stiffness)
        self.damping = float(damping)
        self.spin = float(spin)
        self.axis = np.asarray(axis, dtype=np.float64)
        self.wobble = float(wobble)
        self._noise = NoiseField(seed)

        # Rest shape: points filling a sphere (radius weighted for uniformity).
        u = self._rng.uniform(0.0, 1.0, self.n)
        r = radius * np.cbrt(u)
        theta = np.arccos(self._rng.uniform(-1.0, 1.0, self.n))
        phi = self._rng.uniform(0.0, 2.0 * np.pi, self.n)
        self.rest = np.column_stack(
            [
                r * np.sin(theta) * np.cos(phi),
                r * np.sin(theta) * np.sin(phi),
                r * np.cos(theta),
            ]
        )
        self.rest_cm = self.rest.mean(axis=0)
        self.q0 = self.rest - self.rest_cm

        self.pos = self.rest.copy()
        # Seed an initial spin so it's moving from frame one.
        self.vel = np.cross(self.spin * self.axis, self.pos - self.rest_cm)
        self.t = 0.0
        self.last_frame = -1

    @property
    def positions(self) -> np.ndarray:
        return self.pos

    def speeds(self) -> np.ndarray:
        return np.sqrt((self.vel * self.vel).sum(axis=1)).astype(np.float32)

    def _best_fit_rotation(self, p: np.ndarray) -> np.ndarray:
        # Cross-covariance between current (centered) and rest positions.
        A = p.T @ self.q0  # (3, 3)
        U, _, Vt = np.linalg.svd(A)
        R = U @ Vt
        if np.linalg.det(R) < 0:  # guard against reflection
            U[:, -1] *= -1.0
            R = U @ Vt
        return R

    def stress(self) -> np.ndarray:
        """Per-particle deviation from the matched rigid shape (N,) float32."""
        cm = self.pos.mean(axis=0)
        R = self._best_fit_rotation(self.pos - cm)
        goal = self.q0 @ R.T + cm
        return np.sqrt(((self.pos - goal) ** 2).sum(axis=1)).astype(np.float32)

    def step(self, dt: float = 1.0 / 60.0) -> None:
        self.t += dt
        cm = self.pos.mean(axis=0)
        p = self.pos - cm
        R = self._best_fit_rotation(p)
        # Nudge the orientation forward each step to keep a steady spin.
        R_step = _rotation_matrix(self.axis, self.spin * dt)
        goal = (self.q0 @ R.T) @ R_step.T + cm
        # Shape-matching restoring velocity.
        self.vel += self.stiffness * (goal - self.pos) / dt
        self.vel *= (1.0 - self.damping)
        # A little curl-ish wobble so the surface ripples.
        if self.wobble > 0.0:
            jitter = self._noise.noise(self.pos * 0.5 + self.t * 0.3)
            norm = np.linalg.norm(p, axis=1, keepdims=True) + 1e-6
            self.vel += self.wobble * jitter[:, None] * (p / norm)
        self.pos += self.vel * dt
