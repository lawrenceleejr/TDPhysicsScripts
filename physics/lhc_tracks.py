"""LHC-style collision tracks: charged particles spiralling in a B field.

A collider detector (CMS/ATLAS) sits in a strong solenoidal magnetic field
pointing along the beam (z) axis. Charged particles follow helices: tight
curls for low transverse momentum, nearly straight lines for high pT. The
transverse bending radius is

    R [m] = pT [GeV] / (0.3 * B [T])

We generate a primary vertex, sample a realistic falling pT spectrum and a
flat pseudorapidity distribution, add a couple of collimated "jets", and
propagate each track as a helix. Tracks are colour-coded by pT. ``grow()``
animates the tracks emerging from the vertex for a nice reveal on every new
collision.
"""

from __future__ import annotations

import numpy as np

from .palette import colorize, normalize

# CMS-like solenoid field strength in Tesla.
DEFAULT_B = 3.8


def helix_track(
    vertex,
    momentum,
    charge: float,
    B: float = DEFAULT_B,
    length: float = 7.0,
    n_points: int = 64,
) -> np.ndarray:
    """Return (n_points, 3) points along a charged particle's helix.

    ``momentum`` is (px, py, pz) in GeV; ``vertex`` is the (x, y, z) origin in
    metres; ``length`` is the transverse arc length to trace. Neutral or very
    high-pT particles come out as straight lines (the curvature -> 0 limit).
    """
    vertex = np.asarray(vertex, dtype=np.float64)
    px, py, pz = (float(c) for c in momentum)
    pt = np.hypot(px, py)
    t = np.linspace(0.0, length, int(n_points))

    if pt < 1e-6:
        # No transverse momentum: travel straight along z.
        dirz = np.sign(pz) if pz != 0 else 1.0
        pts = vertex[None, :] + np.column_stack(
            [np.zeros_like(t), np.zeros_like(t), dirz * t]
        )
        return pts

    d0 = np.arctan2(py, px)  # initial transverse direction
    R = pt / (0.3 * B)       # bending radius in metres
    k = np.sign(charge) / R if charge != 0 else 0.0

    if abs(k) < 1e-9:
        # Straight transverse line (neutral or extremely stiff track).
        x = vertex[0] + np.cos(d0) * t
        y = vertex[1] + np.sin(d0) * t
    else:
        x = vertex[0] + (np.sin(d0 + k * t) - np.sin(d0)) / k
        y = vertex[1] - (np.cos(d0 + k * t) - np.cos(d0)) / k
    z = vertex[2] + (pz / pt) * t
    return np.column_stack([x, y, z])


class Track:
    __slots__ = ("points", "color", "pt", "charge", "pid")

    def __init__(self, points, color, pt, charge, pid):
        self.points = points          # (M, 3) float
        self.color = color            # (3,) float in [0, 1]
        self.pt = float(pt)
        self.charge = int(charge)
        self.pid = pid


class LHCEventGenerator:
    """Generates synthetic collision events as collections of tracks."""

    def __init__(
        self,
        B: float = DEFAULT_B,
        palette: str = "inferno",
        max_length: float = 7.0,
        n_points: int = 64,
        seed: int | None = None,
    ):
        self.B = float(B)
        self.palette = palette
        self.max_length = float(max_length)
        self.n_points = int(n_points)
        self._rng = np.random.default_rng(seed)
        self.tracks: list[Track] = []
        self.last_frame = -1
        self.new_event()

    def _sample_track(self, eta=None, phi=None, pt=None, vertex=None) -> Track:
        rng = self._rng
        # Falling pT spectrum (exponential) unless a value is forced (jets).
        if pt is None:
            pt = 0.25 + rng.exponential(0.9)
        if eta is None:
            eta = rng.uniform(-2.4, 2.4)
        if phi is None:
            phi = rng.uniform(0.0, 2.0 * np.pi)
        if vertex is None:
            vertex = np.array([0.0, 0.0, rng.normal(0.0, 0.03)])
        # Charge: mostly +/-1, a minority neutral (drawn as straight lines).
        charge = rng.choice([-1, 0, 1], p=[0.42, 0.16, 0.42])
        px = pt * np.cos(phi)
        py = pt * np.sin(phi)
        pz = pt * np.sinh(eta)
        # Lower-pT tracks curl more, so trace a shorter arc -> tidier picture.
        length = float(np.clip(self.max_length * (0.4 + 0.6 * pt / 3.0), 1.5, self.max_length))
        pts = helix_track(vertex, (px, py, pz), charge, self.B, length, self.n_points)
        # Colour by pT.
        c = colorize(normalize(pt, 0.0, 4.0, gamma=0.6), self.palette).reshape(3)
        return Track(pts, c, pt, charge, "charged")

    def new_event(self) -> None:
        """Build a fresh collision: soft tracks + a few jets."""
        rng = self._rng
        tracks: list[Track] = []
        vertex = np.array([0.0, 0.0, rng.normal(0.0, 0.04)])
        n_soft = int(rng.integers(25, 60))
        for _ in range(n_soft):
            tracks.append(self._sample_track(vertex=vertex))
        # A couple of collimated high-pT jets.
        n_jets = int(rng.integers(2, 4))
        for _ in range(n_jets):
            jet_eta = rng.uniform(-2.0, 2.0)
            jet_phi = rng.uniform(0.0, 2.0 * np.pi)
            n_in_jet = int(rng.integers(5, 11))
            for _ in range(n_in_jet):
                tracks.append(
                    self._sample_track(
                        eta=jet_eta + rng.normal(0.0, 0.12),
                        phi=jet_phi + rng.normal(0.0, 0.12),
                        pt=2.5 + rng.exponential(3.0),
                        vertex=vertex,
                    )
                )
        self.tracks = tracks

    def grow(self, frac: float) -> list[Track]:
        """Return copies of the tracks truncated to ``frac`` of their length.

        Use a value that ramps 0 -> 1 over a second or two to animate the
        tracks shooting out from the collision point.
        """
        frac = float(np.clip(frac, 0.0, 1.0))
        out = []
        for tr in self.tracks:
            m = max(2, int(round(frac * len(tr.points))))
            out.append(Track(tr.points[:m], tr.color, tr.pt, tr.charge, tr.pid))
        return out
