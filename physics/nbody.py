"""Direct-summation N-body gravity with a softened potential.

Uses a kick-drift-kick (leapfrog) integrator, which is symplectic and keeps
energy bounded over long runs -- important for a visual that has to look
stable for the length of a DJ set rather than blowing up after a minute.

Forces are an O(N^2) all-pairs numpy computation. That comfortably handles
a few hundred to ~1500 bodies in real time; ``two_galaxies`` collisions at
N~600 look spectacular and run smoothly.

Conventions: positions/velocities are (N, 3) float64; mass is (N,) float64.
"""

from __future__ import annotations

import numpy as np


class NBodySim:
    def __init__(
        self,
        positions: np.ndarray,
        velocities: np.ndarray,
        masses: np.ndarray,
        G: float = 1.0,
        softening: float = 0.12,
        dt: float = 0.005,
    ):
        self.pos = np.asarray(positions, dtype=np.float64).copy()
        self.vel = np.asarray(velocities, dtype=np.float64).copy()
        self.mass = np.asarray(masses, dtype=np.float64).copy()
        self.G = float(G)
        self.softening = float(softening)
        self.dt = float(dt)
        self.n = self.pos.shape[0]
        self._acc = self._accelerations()
        self.last_frame = -1

    @property
    def positions(self) -> np.ndarray:
        return self.pos

    @property
    def velocities(self) -> np.ndarray:
        return self.vel

    @property
    def masses(self) -> np.ndarray:
        return self.mass

    def speeds(self) -> np.ndarray:
        """Per-body speed |v| (N,) float32 -- handy for colour mapping."""
        return np.sqrt((self.vel * self.vel).sum(axis=1)).astype(np.float32)

    # -- dynamics --------------------------------------------------------
    def _accelerations(self) -> np.ndarray:
        # diff[i, j] = pos[j] - pos[i]
        diff = self.pos[None, :, :] - self.pos[:, None, :]  # (N, N, 3)
        # einsum is markedly faster than (diff*diff).sum(axis=2) for the
        # squared-distance reduction, which dominates the step cost.
        r2 = np.einsum("ijk,ijk->ij", diff, diff) + self.softening * self.softening
        inv_r3 = 1.0 / (r2 * np.sqrt(r2))
        np.fill_diagonal(inv_r3, 0.0)  # no self-force
        # acc[i] = G * sum_j m_j * diff[i, j] / r3
        weight = inv_r3 * self.mass[None, :]
        return self.G * np.einsum("ij,ijk->ik", weight, diff)

    def step(self, dt: float | None = None) -> None:
        """One kick-drift-kick leapfrog step."""
        h = self.dt if dt is None else float(dt)
        self.vel += 0.5 * h * self._acc          # kick
        self.pos += h * self.vel                 # drift
        self._acc = self._accelerations()         # recompute forces
        self.vel += 0.5 * h * self._acc          # kick

    # -- diagnostics -----------------------------------------------------
    def kinetic_energy(self) -> float:
        return float(0.5 * (self.mass * (self.vel * self.vel).sum(axis=1)).sum())

    def potential_energy(self) -> float:
        diff = self.pos[None, :, :] - self.pos[:, None, :]
        r = np.sqrt((diff * diff).sum(axis=2) + self.softening * self.softening)
        inv_r = 1.0 / r
        np.fill_diagonal(inv_r, 0.0)
        mm = self.mass[:, None] * self.mass[None, :]
        # Each unordered pair counted once.
        return float(-0.5 * self.G * (mm * inv_r).sum())

    def total_momentum(self) -> np.ndarray:
        return (self.mass[:, None] * self.vel).sum(axis=0)

    # -- initial conditions ---------------------------------------------
    @classmethod
    def rotating_disk(
        cls,
        n: int = 600,
        central_mass: float = 200.0,
        radius: float = 4.0,
        thickness: float = 0.15,
        G: float = 1.0,
        dispersion: float = 0.05,
        seed: int | None = None,
        **kwargs,
    ) -> "NBodySim":
        """A heavy core surrounded by a thin disk on near-circular orbits."""
        rng = np.random.default_rng(seed)
        n_disk = max(n - 1, 1)
        # Sample radii with more particles at larger radius (area weighting).
        r = radius * np.sqrt(rng.uniform(0.04, 1.0, n_disk))
        phi = rng.uniform(0.0, 2.0 * np.pi, n_disk)
        x = r * np.cos(phi)
        y = r * np.sin(phi)
        z = rng.normal(0.0, thickness, n_disk)
        pos = np.column_stack([x, y, z])
        # Circular speed for the enclosed (mostly central) mass.
        v_circ = np.sqrt(G * central_mass / np.maximum(r, 1e-3))
        # Tangential direction (counter-clockwise) + small random dispersion.
        vx = -np.sin(phi) * v_circ
        vy = np.cos(phi) * v_circ
        vel = np.column_stack([vx, vy, np.zeros_like(vx)])
        vel += rng.normal(0.0, dispersion, vel.shape) * v_circ[:, None]
        mass = rng.uniform(0.4, 1.0, n_disk)
        # Prepend the central body, at rest.
        pos = np.vstack([[0.0, 0.0, 0.0], pos])
        vel = np.vstack([[0.0, 0.0, 0.0], vel])
        mass = np.concatenate([[central_mass], mass])
        return cls(pos, vel, mass, G=G, **kwargs)

    @classmethod
    def two_galaxies(
        cls,
        n: int = 600,
        central_mass: float = 150.0,
        radius: float = 3.0,
        separation: float = 9.0,
        approach_speed: float = 2.2,
        G: float = 1.0,
        seed: int | None = None,
        **kwargs,
    ) -> "NBodySim":
        """Two rotating disks on a collision course -- a galaxy merger."""
        rng = np.random.default_rng(seed)
        half = n // 2
        a = cls.rotating_disk(
            n=half, central_mass=central_mass, radius=radius, G=G,
            seed=None if seed is None else seed + 1,
        )
        b = cls.rotating_disk(
            n=n - half, central_mass=central_mass, radius=radius, G=G,
            seed=None if seed is None else seed + 2,
        )
        # Offset and give them an approach velocity + an impact parameter.
        off = separation * 0.5
        impact = radius * 0.6
        a.pos += np.array([-off, -impact, 0.0])
        b.pos += np.array([off, impact, 0.0])
        a.vel += np.array([approach_speed, 0.0, 0.0])
        b.vel += np.array([-approach_speed, 0.0, 0.0])
        # Tilt the second disk so it isn't coplanar -- more dramatic.
        tilt = 0.6
        rot = np.array(
            [
                [1.0, 0.0, 0.0],
                [0.0, np.cos(tilt), -np.sin(tilt)],
                [0.0, np.sin(tilt), np.cos(tilt)],
            ]
        )
        centre = b.pos[0].copy()
        b.pos = (b.pos - centre) @ rot.T + centre
        b.vel = b.vel @ rot.T
        pos = np.vstack([a.pos, b.pos])
        vel = np.vstack([a.vel, b.vel])
        mass = np.concatenate([a.mass, b.mass])
        return cls(pos, vel, mass, G=G, **kwargs)

    @classmethod
    def plummer_sphere(
        cls,
        n: int = 600,
        total_mass: float = 100.0,
        scale: float = 2.0,
        G: float = 1.0,
        seed: int | None = None,
        **kwargs,
    ) -> "NBodySim":
        """A virialised Plummer sphere -- a self-gravitating star cluster."""
        rng = np.random.default_rng(seed)
        m = total_mass / n
        # Sample radii from the Plummer density profile.
        u = rng.uniform(0.0, 1.0, n)
        r = scale / np.sqrt(np.maximum(u ** (-2.0 / 3.0) - 1.0, 1e-9))
        theta = np.arccos(rng.uniform(-1.0, 1.0, n))
        phi = rng.uniform(0.0, 2.0 * np.pi, n)
        pos = np.column_stack(
            [
                r * np.sin(theta) * np.cos(phi),
                r * np.sin(theta) * np.sin(phi),
                r * np.cos(theta),
            ]
        )
        # Velocities scaled to the local escape speed (von Neumann rejection,
        # vectorised in batches -- the old per-particle Python loop cost ~0.7s
        # at n=1500, a visible hitch when switching to the cluster scene).
        v_esc = np.sqrt(2.0) * (1.0 + r * r / scale ** 2) ** -0.25
        q = np.empty(n)
        filled = 0
        while filled < n:
            x = rng.uniform(0.0, 1.0, size=n)
            g = rng.uniform(0.0, 0.1, size=n)
            acc = x[g <= x * x * (1.0 - x * x) ** 3.5]
            take = min(acc.size, n - filled)
            q[filled:filled + take] = acc[:take]
            filled += take
        speed = q * v_esc * np.sqrt(G * total_mass / scale)
        vtheta = np.arccos(rng.uniform(-1.0, 1.0, n))
        vphi = rng.uniform(0.0, 2.0 * np.pi, n)
        vel = np.column_stack(
            [
                speed * np.sin(vtheta) * np.cos(vphi),
                speed * np.sin(vtheta) * np.sin(vphi),
                speed * np.cos(vtheta),
            ]
        )
        mass = np.full(n, m)
        return cls(pos, vel, mass, G=G, **kwargs)
