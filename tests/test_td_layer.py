"""Static checks for the TouchDesigner-facing code.

These files reference TouchDesigner runtime globals (op, me, absTime, ...), so
they cannot be *executed* outside TD -- but they must parse, and td_build must
import (its module level avoids TD globals). This guards against typos in the
parts of the project that can't be exercised by the physics test suite.
"""

import ast
import glob
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _py_files(*parts):
    return sorted(glob.glob(os.path.join(ROOT, *parts)))


def test_callbacks_parse_and_have_required_funcs():
    files = _py_files("touchdesigner", "callbacks", "*.py")
    assert files, "no callback files found"
    for f in files:
        src = open(f).read()
        tree = ast.parse(src)  # raises on syntax error
        funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert "onSetupParameters" in funcs, f
        assert "onCook" in funcs, f
        # The build script bakes the repo path into this exact line.
        assert '_REPO = r""' in src, f


def test_builders_parse():
    for f in _py_files("touchdesigner", "builders", "*.py"):
        ast.parse(open(f).read())


def test_td_build_imports_and_exposes_builders():
    td_build = importlib.import_module("touchdesigner.td_build")
    for _, fn_name, _ in td_build.SCENES:
        assert hasattr(td_build, fn_name), fn_name
    assert callable(td_build.build_all)
    # Every callback file referenced by a builder must exist on disk.
    for name in (
        "ising_top.py", "nbody_chop.py", "particles_chop.py",
        "lhc_sop.py", "opendata_sop.py", "mass_hud_top.py",
    ):
        assert os.path.isfile(os.path.join(td_build._CALLBACK_DIR, name)), name


def test_repo_path_bake_substitution():
    """The substitution td_build performs must actually change the line."""
    td_build = importlib.import_module("touchdesigner.td_build")
    sample = open(os.path.join(td_build._CALLBACK_DIR, "ising_top.py")).read()
    baked = sample.replace('_REPO = r""', f'_REPO = r"{td_build.REPO}"', 1)
    assert baked != sample
    assert td_build.REPO in baked


# --- the Feynman pair, run against a stub of the TD API -----------------
# These two callbacks carry more logic than the others: one lays down twelve
# thousand points in a fixed order and the other has to put out exactly that
# many samples, or the colours land on the wrong lines. That contract is worth
# exercising rather than only parsing, so here is just enough of the
# TouchDesigner API to run them.
class _Par:
    def __init__(self, name, val, owner):
        self.name, self.val, self.owner = name, val, owner
        self.normMin = self.normMax = 0.0
        self.menuNames = self.menuLabels = []

    def eval(self):
        return self.val

    def pulse(self):
        pass


class _Pars:
    pass


class _Page:
    def __init__(self, o):
        self.o = o

    def _add(self, name, default):
        par = _Par(name, default, self.o)
        setattr(self.o.par, name, par)
        return [par]

    def appendFloat(self, name, label=None):
        return self._add(name, 0.0)

    def appendInt(self, name, label=None):
        return self._add(name, 0)

    def appendStr(self, name, label=None):
        return self._add(name, "")

    def appendToggle(self, name, label=None):
        return self._add(name, False)

    def appendMenu(self, name, label=None):
        return self._add(name, "")

    def appendPulse(self, name, label=None):
        return self._add(name, None)


class _Point:
    __slots__ = ("x", "y", "z", "Cd")


class _Vertex:
    def __init__(self):
        self.point = _Point()


class _StubOp:
    """A Script SOP or Script CHOP, as far as these callbacks can tell."""

    def __init__(self, path="/scene/op", parent=None):
        self.path = path
        self.par = _Pars()
        self._parent = parent
        self.polys = []
        self.chans = None
        self.attribs = []
        self.render = self.display = False

    def appendCustomPage(self, name):
        return _Page(self)

    # geometry side
    def clear(self):
        self.polys = []

    @property
    def pointAttribs(self):
        outer = self

        class _A:
            def create(self, name, default):
                outer.attribs.append(name)
        return _A()

    def appendPoly(self, n, closed=False, addPoints=True):
        poly = [_Vertex() for _ in range(n)]
        self.polys.append(poly)
        return poly

    # channel side
    def copyNumpyArray(self, arr, baseName="c"):
        self.chans = arr

    # network side
    def parent(self):
        return self._parent

    def op(self, pattern):
        return None

    def cook(self, force=False):
        pass


class _Scene:
    def __init__(self, kids):
        self.kids = kids

    def op(self, pattern):
        return self.kids.get(pattern.lstrip("./"))

    def findChildren(self, name=None, depth=1):
        return [v for k, v in self.kids.items() if name is None or k.endswith(name)]


class _AbsTime:
    frame = 1
    seconds = 0.0


def _load(name, extra=None):
    """Exec a callback with the TouchDesigner globals it expects."""
    src = open(os.path.join(ROOT, "touchdesigner", "callbacks", name)).read()
    src = src.replace('_REPO = r""', '_REPO = r"%s"' % ROOT, 1)
    ns = {"__name__": "cb_" + name.replace(".py", ""), "absTime": _AbsTime}
    ns.update(extra or {})
    exec(compile(src, name, "exec"), ns)
    return ns


def test_feynman_sop_builds_the_field_once():
    mod = _load("feynman_sop.py")
    sop = _StubOp("/feynman/geo/lines")
    mod["onSetupParameters"](sop)
    for name, val in (("Field", "16x9"), ("World", 16.0), ("Marks", True)):
        getattr(sop.par, name).val = val

    mod["onCook"](sop)
    assert "Cd" in sop.attribs
    assert len(sop.polys) > 600, "the field should be hundreds of polylines"
    points = sum(len(p) for p in sop.polys)
    assert points > 10000
    assert all(len(p) >= 2 for p in sop.polys)

    # Cooking again must not rebuild: that is the whole point of the split.
    before = len(sop.polys)
    mod["onCook"](sop)
    assert len(sop.polys) == before
    getattr(sop.par, "Marks").val = False       # a structural change does rebuild
    mod["onCook"](sop)
    assert len(sop.polys) < before


def test_feynman_chop_matches_the_sop_point_for_point():
    """The contract: one sample per point, four channels, in the same order."""
    sop_mod = _load("feynman_sop.py")
    sop = _StubOp("/feynman/geo/lines")
    sop_mod["onSetupParameters"](sop)
    for name, val in (("Field", "16x9"), ("World", 16.0), ("Marks", True)):
        getattr(sop.par, name).val = val
    sop_mod["onCook"](sop)
    points = sum(len(p) for p in sop.polys)

    scene = _Scene({"geo/lines": sop})
    chop = _StubOp("/feynman/state", parent=scene)
    chop_mod = _load("feynman_chop.py")
    chop_mod["onSetupParameters"](chop)
    for name, val in (("Geosop", "geo/lines"), ("Tail", 0.3), ("Traverse", 30.0),
                      ("Fade", 0.5), ("Walkers", 3), ("Palette", "sigma"),
                      ("Hold", False)):
        getattr(chop.par, name).val = val

    seen = set()
    for frame in range(1, 40):
        _AbsTime.frame = frame
        _AbsTime.seconds = frame / 60.0
        chop_mod["onCook"](chop)
        arr = chop.chans
        assert arr is not None
        assert arr.shape == (4, points), (arr.shape, points)
        assert arr.dtype.name == "float32"
        assert arr.min() >= 0.0 and arr.max() <= 1.0001
        seen.add(round(float(arr[3].mean()), 4))
    assert len(seen) > 5, "the flood is not advancing"
