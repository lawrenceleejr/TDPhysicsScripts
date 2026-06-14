# Build an Akai APC mini mk2 control surface for an existing PhysicsVJ show
#
# HOW TO RUN inside TouchDesigner:
#   1. Build the show first (run build_all) so a 'PhysicsVJ' COMP exists.
#   2. In TD's MIDI Device Mapper, map your APC mini mk2 to a device id.
#   3. Create a Text DAT, set its 'File' to this file and turn 'Sync to File'
#      On (or paste the contents), then right-click > Run.
#
# It locates the repo via the TD_PHYSICS_REPO environment variable, or by
# walking up from this file's location, then builds an 'APCShow' COMP under '/'
# pointing at '/PhysicsVJ'. Press the top-right scene button (or pulse the
# 'Reset' par) any time to resync the controller's LEDs.
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

# Drop cached copies so on-disk edits take effect without restarting TD.
for _m in list(sys.modules):
    if (_m == "touchdesigner" or _m.startswith("touchdesigner.")
            or _m == "physics" or _m.startswith("physics.")):
        del sys.modules[_m]

from touchdesigner import td_build
td_build.build_apc(op('/'))
