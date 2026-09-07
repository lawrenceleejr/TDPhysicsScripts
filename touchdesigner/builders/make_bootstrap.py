# Mint a reusable, command-line-friendly bootstrap .toe  (ONE-TIME setup)
#
# WHY: .toe is a binary only TouchDesigner can write, so this single in-app step
# creates it. After this, you never touch the GUI to rebuild -- just run
#   ./tools/run_td.sh            (open + rebuild from the latest files)
#   ./tools/run_td.sh --check    (rebuild headless, print build_report.txt, quit)
#
# HOW TO RUN inside TouchDesigner (once):
#   1. Create a Text DAT, paste this file (or sync its File), right-click > Run.
#   2. It adds an Execute DAT at '/' that builds the show on every launch, then
#      saves '<repo>/physicsvj.toe'. Done.
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
        "Could not locate TDPhysicsScripts. Set TD_PHYSICS_REPO or sync this DAT "
        "to its file on disk."
    )
if REPO not in sys.path:
    sys.path.insert(0, REPO)

root = op("/")

# An Execute DAT that builds the show when TouchDesigner starts.
ex = root.op("physics_startup") or root.create(executeDAT, "physics_startup")
ex.nodeX, ex.nodeY = -400, 400
ex.text = (
    "import sys\n"
    f"sys.path.insert(0, r\"{REPO}\")\n"
    "\n"
    "def onStart():\n"
    "    # Fresh process per launch picks up file edits; purge anyway for safety.\n"
    "    for _m in list(sys.modules):\n"
    "        if (_m == 'touchdesigner' or _m.startswith('touchdesigner.')\n"
    "                or _m == 'physics' or _m.startswith('physics.')):\n"
    "            del sys.modules[_m]\n"
    "    from touchdesigner import startup\n"
    "    startup.run(op('/'))\n"
    "    return\n"
)
try:
    ex.par.start = True      # fire on process start
    ex.par.active = True
except Exception as e:
    print("[make_bootstrap] could not set Execute DAT flags:", e)

# Save the bootstrap .toe *before* building, so it holds only this Execute DAT.
# Every launch rebuilds from the files on disk anyway; saving a fully built show
# into it would just make each launch load a large network and then destroy it.
toe = os.path.join(REPO, "physicsvj.toe")
try:
    project.save(toe)
    print(f"[make_bootstrap] saved {toe}")
    print("[make_bootstrap] from now on:  ./tools/run_td.sh   (or --check)")
except Exception as e:
    print("[make_bootstrap] could not save .toe:", e)

# Build now as well, so this session shows the result immediately. (If you
# later save this project, the built show goes into the .toe too; harmless,
# every launch destroys and rebuilds it anyway -- it just opens slower.)
from touchdesigner import startup
startup.run(root)
