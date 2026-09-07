#!/usr/bin/env bash
# Rebuild + run the PhysicsVJ show from the command line.
#
#   ./tools/run_td.sh                 pull latest, open TD, rebuild (GUI stays open)
#   ./tools/run_td.sh --check         pull, rebuild headless, print build_report.txt, quit
#   ./tools/run_td.sh --scene nbody   build just one scene (any td_build.build_<name>)
#   ./tools/run_td.sh --check --scene feynman
#
# One-time setup: in TouchDesigner, run touchdesigner/builders/make_bootstrap.py
# once to create physicsvj.toe (a .toe is binary, so only TD can write it).
#
# Overrides via env:
#   TOUCHDESIGNER_APP    full path to the TD binary (macOS .app/Contents/MacOS/...,
#                        or TouchDesigner.exe under Git Bash / MSYS on Windows)
#   PHYSICSVJ_TOE        path to the bootstrap .toe
#   PHYSICSVJ_NO_PULL=1  skip the git pull
#   PHYSICSVJ_TIMEOUT    seconds to wait for a --check build (default 300)
#
# Exit status (so this can run from a script or CI):
#   0  launched / --check reached the end of the build with no exception
#   1  setup problem (no TD binary, no .toe, bad arguments)
#   2  --check: the build raised or did not finish within the timeout
#   3  --check: no report was written at all (TD never ran onStart)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOE="${PHYSICSVJ_TOE:-$REPO/physicsvj.toe}"

CHECK=0
SCENE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --scene) shift; SCENE="${1:-}"; [ -n "$SCENE" ] || { echo "--scene needs a name (e.g. nbody)"; exit 1; } ;;
    --scene=*) SCENE="${1#--scene=}" ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (try --help)"; exit 1 ;;
  esac
  shift
done

# A digest of a file, with whatever tool this machine has (macOS: shasum;
# Linux: sha1sum; last resort: cksum). Used to notice when this script itself
# changed in the pull.
digest() {
  if command -v shasum >/dev/null 2>&1; then shasum "$1" | awk '{print $1}'
  elif command -v sha1sum >/dev/null 2>&1; then sha1sum "$1" | awk '{print $1}'
  else cksum "$1" | awk '{print $1}'; fi
}

# Self-update: pull FIRST, and if this script changed, re-exec the new copy so
# we never run stale launcher logic.
if [ "${PHYSICSVJ_NO_PULL:-}" != "1" ] && [ -z "${PHYSICSVJ_REEXEC:-}" ]; then
  echo "==> git pull"
  before="$(digest "$0" 2>/dev/null)"
  git -C "$REPO" pull --ff-only || echo "(pull skipped/failed; using current files)"
  after="$(digest "$0" 2>/dev/null)"
  if [ -n "$before" ] && [ "$before" != "$after" ]; then
    echo "==> run_td.sh changed in the pull; re-running the updated version"
    args=()
    [ "$CHECK" = 1 ] && args+=(--check)
    [ -n "$SCENE" ] && args+=(--scene "$SCENE")
    # ${args[@]+"${args[@]}"}: an empty array under 'set -u' is an error on the
    # bash 3.2 that macOS ships; this idiom expands to nothing instead.
    PHYSICSVJ_REEXEC=1 exec "$0" ${args[@]+"${args[@]}"}
  fi
fi

# Locate the TouchDesigner binary: macOS app bundles, then Windows installs as
# seen from Git Bash / MSYS (/c/Program Files/...), then $PROGRAMFILES.
TD="${TOUCHDESIGNER_APP:-}"
if [ -z "$TD" ]; then
  candidates=(
    "/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner"
    /Applications/TouchDesigner*/Contents/MacOS/TouchDesigner
    "$HOME"/Applications/TouchDesigner*/Contents/MacOS/TouchDesigner
    "/c/Program Files/Derivative/TouchDesigner"*/bin/TouchDesigner.exe
  )
  if [ -n "${PROGRAMFILES:-}" ]; then
    pf="$(printf '%s' "$PROGRAMFILES" | sed -e 's#\\#/#g' -e 's#^\([A-Za-z]\):#/\L\1#')"
    candidates+=("$pf"/Derivative/TouchDesigner*/bin/TouchDesigner.exe)
  fi
  for c in "${candidates[@]}"; do
    [ -x "$c" ] && TD="$c" && break
  done
fi
if [ -z "$TD" ] || [ ! -x "$TD" ]; then
  echo "Could not find the TouchDesigner binary. Set TOUCHDESIGNER_APP to it, e.g.:"
  echo "  macOS:   export TOUCHDESIGNER_APP=/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner"
  echo "  Windows: export TOUCHDESIGNER_APP='/c/Program Files/Derivative/TouchDesigner/bin/TouchDesigner.exe'"
  exit 1
fi

if [ ! -f "$TOE" ]; then
  echo "Bootstrap .toe not found at: $TOE"
  echo "In TouchDesigner, run touchdesigner/builders/make_bootstrap.py once to create it."
  exit 1
fi

# The scene selection reaches startup.run() through the environment.
if [ -n "$SCENE" ]; then
  export PHYSICSVJ_BUILD="$SCENE"
  echo "==> building only: $SCENE"
fi

if [ "$CHECK" = 1 ]; then
  REPORT="$REPO/build_report.txt"
  TIMEOUT="${PHYSICSVJ_TIMEOUT:-300}"
  rm -f "$REPORT"
  echo "==> headless rebuild ($TOE), up to ${TIMEOUT}s"
  # Run TD in the background so we never wait on it forever (e.g. if quit fails
  # or a startup dialog appears). Poll for the report's END marker, then stop TD.
  PHYSICSVJ_QUIT=1 "$TD" "$TOE" >/dev/null 2>&1 &
  TDPID=$!
  done=0
  i=0
  while [ "$i" -lt "$TIMEOUT" ]; do
    if [ -f "$REPORT" ] && grep -q "== END ==" "$REPORT" 2>/dev/null; then done=1; break; fi
    kill -0 "$TDPID" 2>/dev/null || break   # TD exited on its own
    sleep 1
    i=$((i + 1))
  done
  kill "$TDPID" 2>/dev/null; sleep 1; kill -9 "$TDPID" 2>/dev/null
  echo "================ build_report.txt ================"
  if [ -f "$REPORT" ]; then
    cat "$REPORT"
    status=0
    if grep -q "== BUILD RAISED ==" "$REPORT" 2>/dev/null; then
      echo
      echo "*** the build raised an exception (see BUILD RAISED above) ***"
      status=2
    fi
    if [ "$done" != 1 ]; then
      echo
      echo "*** build did NOT reach the end within ${TIMEOUT}s ***"
      echo "*** the log above stops at the operation that stalled ***"
      status=2
    fi
    exit "$status"
  else
    echo "(no report written within ${TIMEOUT}s)"
    echo "TD likely didn't run onStart -- e.g. a startup dialog is blocking, or the"
    echo "'physics_startup' Execute DAT wasn't created. Open physicsvj.toe once in the"
    echo "GUI to dismiss any dialog and confirm that DAT exists at '/', then retry."
    exit 3
  fi
else
  echo "==> launching TouchDesigner ($TOE)"
  exec "$TD" "$TOE"
fi
