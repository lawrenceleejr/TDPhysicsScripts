"""2D Ising model with vectorized checkerboard Metropolis dynamics.

A lattice of +/-1 spins evolves under the Metropolis-Hastings rule at a
given temperature. Near the critical temperature (Tc ~ 2.269 for the 2D
square lattice with J=1) the model forms slowly churning magnetic domains
-- visually this looks like organic, breathing structure, and the domain
walls make great glowing edges on a dark background.

The update is a checkerboard sweep: the lattice is 2-coloured like a
chessboard and each colour is updated simultaneously (every site's
neighbours belong to the other colour, so there are no race conditions).
This makes the whole step a handful of numpy array operations.
"""

from __future__ import annotations

import numpy as np

# Onsager critical temperature for the 2D square-lattice Ising model (J=1).
CRITICAL_TEMPERATURE = 2.0 / np.log(1.0 + np.sqrt(2.0))  # ~= 2.2692


class IsingModel:
    def __init__(
        self,
        size: int = 256,
        temperature: float = CRITICAL_TEMPERATURE,
        field: float = 0.0,
        coupling: float = 1.0,
        seed: int | None = None,
    ):
        self.size = int(size)
        self.temperature = float(temperature)
        self.field = float(field)
        self.coupling = float(coupling)
        self._rng = np.random.default_rng(seed)
        # Spins as int8 in {-1, +1}.
        self.spins = self._rng.integers(0, 2, size=(self.size, self.size)).astype(np.int8) * 2 - 1
        # Precompute the chessboard masks once.
        ii, jj = np.indices((self.size, self.size))
        parity = (ii + jj) & 1
        self._masks = (parity == 0, parity == 1)
        # Bookkeeping for frame-rate-independent stepping by the host.
        self.last_frame = -1

    # -- evolution -------------------------------------------------------
    def _neighbour_sum(self) -> np.ndarray:
        # Sum of four +/-1 neighbours stays in [-4, 4], well within int8, so we
        # avoid an int32 upcast of the whole lattice on every (8x/sweep) call.
        s = self.spins
        return (
            np.roll(s, 1, axis=0)
            + np.roll(s, -1, axis=0)
            + np.roll(s, 1, axis=1)
            + np.roll(s, -1, axis=1)
        )

    def _update_color(self, mask: np.ndarray) -> None:
        nbr = self._neighbour_sum()
        # Energy cost of flipping each spin: dE = 2 s (J * sum_nbr + h).
        dE = 2.0 * self.spins * (self.coupling * nbr + self.field)
        T = max(self.temperature, 1e-6)
        # Acceptance probability; dE <= 0 gives prob >= 1 (always accept). Clamp
        # the exponent at 0 so exp() never overflows to inf at very low T (the
        # accept decision is identical, but it stops a per-frame overflow warn).
        accept_prob = np.exp(np.minimum(-dE / T, 0.0))
        rand = self._rng.random(self.spins.shape)
        flip = mask & (rand < accept_prob)
        self.spins[flip] = -self.spins[flip]

    def step(self, sweeps: int = 1) -> None:
        """Advance ``sweeps`` full checkerboard Metropolis sweeps."""
        for _ in range(int(sweeps)):
            for mask in self._masks:
                self._update_color(mask)

    # -- observables / outputs ------------------------------------------
    def magnetization(self) -> float:
        """Mean spin in [-1, 1]."""
        return float(self.spins.mean())

    def energy_per_site(self) -> float:
        """Average interaction energy per site (field term excluded)."""
        s = self.spins.astype(np.float64)
        right = np.roll(s, -1, axis=1)
        down = np.roll(s, -1, axis=0)
        bonds = -self.coupling * (s * right + s * down)
        return float(bonds.mean())

    def field01(self) -> np.ndarray:
        """Spins remapped to {0, 1} as float32 (H, W)."""
        return ((self.spins.astype(np.float32) + 1.0) * 0.5)

    def domain_walls(self) -> np.ndarray:
        """Fraction of disagreeing neighbours per site, in [0, 1] (H, W).

        High where spins differ from neighbours -> traces the glowing
        boundaries between magnetic domains.
        """
        s = self.spins
        diff = (
            (s != np.roll(s, 1, axis=0)).astype(np.float32)
            + (s != np.roll(s, -1, axis=0)).astype(np.float32)
            + (s != np.roll(s, 1, axis=1)).astype(np.float32)
            + (s != np.roll(s, -1, axis=1)).astype(np.float32)
        )
        return diff * 0.25

    def reset(self, temperature: float | None = None, seed: int | None = None) -> None:
        if temperature is not None:
            self.temperature = float(temperature)
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self.spins = (
            self._rng.integers(0, 2, size=(self.size, self.size)).astype(np.int8) * 2 - 1
        )
        self.last_frame = -1
