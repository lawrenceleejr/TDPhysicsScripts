# Build the 2D Ising model scene
#
# HOW TO RUN inside TouchDesigner:
#   1. Create a Text DAT.
#   2. Set its 'File' parameter to this file and turn 'Sync to File' On
#      (or just paste the contents).
#   3. Right-click the DAT > Run  (or press the Run button).
#
# It locates the repo via the TD_PHYSICS_REPO environment variable, or by
# walking up from this file's location, then builds the scene under '/'.
import sys, os


def _find_repo():
    env = os.environ.get("TD_PHYSICS_REPO", "")
    if env and os.path.isdir(os.path.join(env, "physics")):
        return env
    try:
        f = me.par.file.eval()
    except Exception:
        f = ""
    d = os.path.dirname(os.path.abspath(f)) if f else ""
    while d and d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, "physics")):
            return d
        d = os.path.dirname(d)
    return ""


REPO = _find_repo()
if not REPO:
    raise RuntimeError(
        "Could not locate TDPhysicsScripts. Set the TD_PHYSICS_REPO environment "
        "variable to the cloned repo path, or sync this DAT to its file on disk."
    )
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from touchdesigner import td_build
td_build.build_ising(op('/'))
