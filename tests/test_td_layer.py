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
