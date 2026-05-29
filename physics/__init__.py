"""TDPhysicsScripts — framework-agnostic physics simulation cores.

These modules depend only on numpy so they can be unit-tested outside of
TouchDesigner and imported unchanged inside TouchDesigner's bundled Python.
Each core exposes a small, explicit state API (``step()`` plus numpy
accessors); the TouchDesigner adapter layer in ``touchdesigner/`` bridges
them to Script TOP / CHOP / SOP operators.
"""

from __future__ import annotations

from . import palette
from .ising import IsingModel
from .nbody import NBodySim
from .particles import CurlNoiseFlow, ShapeMatchedSoftBody
from .lhc_tracks import LHCEventGenerator, helix_track
from .opendata import DimuonShow, load_dimuon

__all__ = [
    "palette",
    "IsingModel",
    "NBodySim",
    "CurlNoiseFlow",
    "ShapeMatchedSoftBody",
    "LHCEventGenerator",
    "helix_track",
    "DimuonShow",
    "load_dimuon",
]

__version__ = "0.1.0"
