"""CMS dimuon open-data visualiser.

The CMS experiment publishes an educational CSV of dimuon events (two muons
per collision: their 4-momenta, charges and the reconstructed invariant
mass). Histogramming the invariant mass reveals real particles -- the J/psi,
Upsilon and Z resonances pop out as peaks. Here we render each event as two
muon tracks emerging from the vertex, colour-coded by the event's invariant
mass, and cycle through events.

If the real dataset hasn't been downloaded (see ``data/fetch_opendata.py``),
this falls back to a small, physically-plausible synthetic sample that ships
with the repo, so the visual always works offline.

Dataset: CMS Open Data, e.g. record 545 (Dimuon_DoubleMu). CC0.
"""

from __future__ import annotations

import csv
import os

import numpy as np

from .lhc_tracks import Track, helix_track, DEFAULT_B
from .palette import colorize, normalize

MUON_MASS = 0.1056583745  # GeV

# Well-known resonances, used by the synthetic generator and for nice colour
# anchoring of the mass axis.
RESONANCES = {
    "J/psi": 3.0969,
    "Upsilon": 9.4603,
    "Z": 91.1876,
}

# Canonical output column order for the CSV the synthetic generator writes.
_COLUMNS = [
    "Run", "Event",
    "E1", "px1", "py1", "pz1", "pt1", "eta1", "phi1", "Q1",
    "E2", "px2", "py2", "pz2", "pt2", "eta2", "phi2", "Q2",
    "M",
]


def _data_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _find_dataset(path: str | None) -> str | None:
    if path:
        return path if os.path.isfile(path) else None
    for name in ("dimuon.csv", "dimuon_sample.csv"):
        cand = os.path.join(_data_dir(), name)
        if os.path.isfile(cand):
            return cand
    return None


def load_dimuon(path: str | None = None, max_rows: int | None = None) -> dict:
    """Load a dimuon CSV into a dict of numpy arrays.

    Column names are matched case-insensitively, so this works with the
    official CMS files (which include extra ``Type``/``Run`` columns) and the
    bundled sample. The invariant mass ``M`` is recomputed if absent. If no
    file is found, a synthetic sample is generated in memory.
    """
    fname = _find_dataset(path)
    if fname is None:
        return generate_synthetic_dimuon(n=4000, seed=0)

    rows: list[dict] = []
    with open(fname, "r", newline="") as fh:
        reader = csv.reader(fh)
        header = [h.strip().lower() for h in next(reader)]
        idx = {name: i for i, name in enumerate(header)}

        def get(parts, key, default=0.0):
            i = idx.get(key.lower())
            if i is None or i >= len(parts) or parts[i] == "":
                return default
            try:
                return float(parts[i])
            except ValueError:
                return default

        for parts in reader:
            if not parts:
                continue
            rows.append(
                {
                    "E1": get(parts, "e1"), "px1": get(parts, "px1"),
                    "py1": get(parts, "py1"), "pz1": get(parts, "pz1"),
                    "Q1": get(parts, "q1", 1.0),
                    "E2": get(parts, "e2"), "px2": get(parts, "px2"),
                    "py2": get(parts, "py2"), "pz2": get(parts, "pz2"),
                    "Q2": get(parts, "q2", -1.0),
                    "M": get(parts, "m", float("nan")),
                }
            )
            if max_rows and len(rows) >= max_rows:
                break

    if not rows:
        # A present-but-empty CSV (header only / all-blank rows) must not crash;
        # fall back to the synthetic sample so the visual always has events.
        return generate_synthetic_dimuon(n=4000, seed=0)

    out = {k: np.array([r[k] for r in rows], dtype=np.float64) for k in rows[0]}
    # Recompute M where missing.
    bad = ~np.isfinite(out["M"])
    if bad.any():
        out["M"][bad] = _invariant_mass(out)[bad]
    return out


def _invariant_mass(d: dict) -> np.ndarray:
    E = d["E1"] + d["E2"]
    px = d["px1"] + d["px2"]
    py = d["py1"] + d["py2"]
    pz = d["pz1"] + d["pz2"]
    m2 = E * E - px * px - py * py - pz * pz
    return np.sqrt(np.clip(m2, 0.0, None))


# ---------------------------------------------------------------------------
# Synthetic generator (offline fallback + sample-file builder)
# ---------------------------------------------------------------------------
def _boost(p4: np.ndarray, beta: np.ndarray) -> np.ndarray:
    """Lorentz-boost a 4-vector (E, px, py, pz) by velocity ``beta`` (3,)."""
    b2 = float(beta @ beta)
    if b2 < 1e-15:
        return p4.copy()
    gamma = 1.0 / np.sqrt(1.0 - b2)
    E = p4[0]
    p = p4[1:]
    bp = float(beta @ p)
    E2 = gamma * (E + bp)
    p2 = p + ((gamma - 1.0) * bp / b2 + gamma * E) * beta
    return np.array([E2, p2[0], p2[1], p2[2]])


def generate_synthetic_dimuon(n: int = 4000, seed: int | None = 0) -> dict:
    """Generate ``n`` dimuon events with realistic resonance peaks."""
    rng = np.random.default_rng(seed)
    # Mixture: continuum + J/psi + Upsilon + Z.
    kinds = rng.choice(
        ["cont", "jpsi", "ups", "z"], size=n, p=[0.55, 0.20, 0.12, 0.13]
    )
    cols = {c: np.zeros(n) for c in _COLUMNS}
    cols["Run"][:] = 146511  # arbitrary, mimics a real run number
    cols["Event"] = np.arange(n)

    for i, kind in enumerate(kinds):
        if kind == "jpsi":
            M = max(2 * MUON_MASS + 1e-3, rng.normal(RESONANCES["J/psi"], 0.05))
            pt_p = rng.exponential(4.0)
        elif kind == "ups":
            M = max(2 * MUON_MASS + 1e-3, rng.normal(RESONANCES["Upsilon"], 0.08))
            pt_p = rng.exponential(6.0)
        elif kind == "z":
            M = max(2 * MUON_MASS + 1e-3, rng.normal(RESONANCES["Z"], 2.5))
            pt_p = rng.exponential(15.0)
        else:  # falling continuum
            M = float(np.clip(1.0 / rng.uniform(0.01, 1.0), 1.0, 110.0))
            pt_p = rng.exponential(5.0)

        # Two muons back-to-back in the parent rest frame.
        p_star = np.sqrt(max((M / 2) ** 2 - MUON_MASS ** 2, 0.0))
        cth = rng.uniform(-1.0, 1.0)
        sth = np.sqrt(1.0 - cth * cth)
        ph = rng.uniform(0.0, 2.0 * np.pi)
        d = np.array([sth * np.cos(ph), sth * np.sin(ph), cth])
        Emu = M / 2
        mu1 = np.array([Emu, *(p_star * d)])
        mu2 = np.array([Emu, *(-p_star * d)])

        # Boost the parent: sample its lab momentum.
        eta_p = rng.uniform(-2.2, 2.2)
        phi_p = rng.uniform(0.0, 2.0 * np.pi)
        pz_p = pt_p * np.sinh(eta_p)
        P = np.array([pt_p * np.cos(phi_p), pt_p * np.sin(phi_p), pz_p])
        Ep = np.sqrt(float(P @ P) + M * M)
        beta = P / Ep
        mu1 = _boost(mu1, beta)
        mu2 = _boost(mu2, beta)

        q1 = rng.choice([-1.0, 1.0])
        for tag, mu, q in (("1", mu1, q1), ("2", mu2, -q1)):
            E, px, py, pz = mu
            cols["E" + tag][i] = E
            cols["px" + tag][i] = px
            cols["py" + tag][i] = py
            cols["pz" + tag][i] = pz
            cols["pt" + tag][i] = np.hypot(px, py)
            cols["eta" + tag][i] = np.arcsinh(pz / max(np.hypot(px, py), 1e-9))
            cols["phi" + tag][i] = np.arctan2(py, px)
            cols["Q" + tag][i] = q
        cols["M"][i] = M

    return cols


def write_dimuon_csv(path: str, data: dict) -> None:
    """Write a dimuon dict (from the generator) to CSV in canonical order."""
    n = len(data["M"])
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_COLUMNS)
        for i in range(n):
            writer.writerow(
                [int(data[c][i]) if c in ("Run", "Event") else round(float(data[c][i]), 4)
                 for c in _COLUMNS]
            )


# ---------------------------------------------------------------------------
# Live show
# ---------------------------------------------------------------------------
class DimuonShow:
    """Cycle through dimuon events, rendering each as two muon tracks."""

    def __init__(
        self,
        path: str | None = None,
        B: float = DEFAULT_B,
        palette: str = "ice",
        max_length: float = 7.0,
        n_points: int = 64,
        order: str = "mass",
        seed: int | None = None,
        underlying: int = 90,
    ):
        self.underlying = int(underlying)
        self.data = load_dimuon(path)
        self.n_events = len(self.data["M"])
        if self.n_events == 0:  # never divide by zero in show_event()
            self.data = generate_synthetic_dimuon(n=4000, seed=0)
            self.n_events = len(self.data["M"])
        self.B = float(B)
        self.palette = palette
        self.max_length = float(max_length)
        self.n_points = int(n_points)
        self._rng = np.random.default_rng(seed)
        # An order that lingers on the pretty high-mass events looks better
        # than purely sequential; "mass" sorts so resonances are easy to spot.
        if order == "mass":
            self.sequence = np.argsort(self.data["M"])
        elif order == "random":
            self.sequence = self._rng.permutation(self.n_events)
        else:
            self.sequence = np.arange(self.n_events)
        self.index = 0
        self.tracks: list[Track] = []
        self.current_mass = 0.0
        self.last_frame = -1
        self.show_event(0)

    def _build(self, ev: int) -> list[Track]:
        """The event: the two measured muons, plus the rest of the collision.

        A real dimuon event is not two lines in an empty detector -- the pair
        sits inside the hadronic debris of the same proton-proton collision.
        The dataset records only the muons, so the underlying event is
        simulated around them: soft charged tracks from the same vertex, drawn
        dim and short, with the muons bright and full length on top. Seeded
        from the event number, so an event looks the same every time it comes
        round.
        """
        d = self.data
        vertex = np.array([0.0, 0.0, 0.0])
        M = float(d["M"][ev])
        # Anchor colour on a log mass scale so J/psi..Z spread across the ramp.
        c01 = normalize(np.log10(max(M, 0.3)), np.log10(0.5), np.log10(120.0))
        col = colorize(c01, self.palette).reshape(3)

        tracks = list(self._underlying_event(ev, vertex))
        for tag in ("1", "2"):
            mom = (d["px" + tag][ev], d["py" + tag][ev], d["pz" + tag][ev])
            q = d["Q" + tag][ev]
            pts = helix_track(vertex, mom, q, self.B, self.max_length, self.n_points)
            tracks.append(Track(pts, col, np.hypot(mom[0], mom[1]), int(q), "muon"))
        return tracks

    def _underlying_event(self, ev: int, vertex) -> list[Track]:
        """Soft charged tracks from the same vertex: the rest of the collision.

        Momenta follow the shapes a minimum-bias event actually has -- an
        exponential pT spectrum around 0.6 GeV, flat in azimuth, Gaussian in
        pseudorapidity -- so the spray thins out the way a detector display
        does rather than looking like noise. Low momentum means tight curvature
        in the field, which is what gives the picture its spiral.
        """
        n = self.underlying
        if n <= 0:
            return []
        rng = np.random.default_rng(1000 + int(ev))
        pt = rng.exponential(0.6, n) + 0.12
        eta = rng.normal(0.0, 1.6, n)
        phi = rng.uniform(0.0, 2.0 * np.pi, n)
        charge = rng.choice((-1, 1), n)
        theta = 2.0 * np.arctan(np.exp(-eta))
        out = []
        for i in range(n):
            px = pt[i] * np.cos(phi[i])
            py = pt[i] * np.sin(phi[i])
            pz = pt[i] / max(np.tan(theta[i]), 1e-3)
            # A soft track is drawn short: it curls up long before the muons do.
            length = float(np.clip(self.max_length * (0.25 + 0.5 * pt[i]),
                                   0.8, self.max_length))
            pts = helix_track(vertex, (px, py, pz), int(charge[i]), self.B,
                              length, max(12, self.n_points // 2))
            # Cool and dim, so they read as context and never fight the pair.
            shade = 0.10 + 0.16 * float(np.clip(pt[i] / 2.0, 0.0, 1.0))
            out.append(Track(pts, np.array([shade * 0.55, shade * 0.85, shade],
                                           dtype=np.float32),
                             float(pt[i]), int(charge[i]), "hadron"))
        return out

    def show_event(self, i: int) -> None:
        self.index = int(i) % self.n_events
        ev = int(self.sequence[self.index])
        self.current_mass = float(self.data["M"][ev])
        self.tracks = self._build(ev)

    def next_event(self) -> None:
        self.show_event(self.index + 1)

    def grow(self, frac: float) -> list[Track]:
        """The event part-drawn. The underlying tracks lead the muons slightly,
        so the debris is already there when the pair sweeps out through it."""
        frac = float(np.clip(frac, 0.0, 1.0))
        out = []
        for tr in self.tracks:
            f = min(1.0, frac * 1.35) if tr.pid == "hadron" else frac
            m = max(2, int(round(f * len(tr.points))))
            out.append(Track(tr.points[:m], tr.color, tr.pt, tr.charge, tr.pid))
        return out

    @property
    def muons(self) -> list[Track]:
        """Just the measured pair -- what the invariant mass is computed from."""
        return [t for t in self.tracks if t.pid == "muon"]
