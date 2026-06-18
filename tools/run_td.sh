#!/usr/bin/env bash
# Rebuild + run the PhysicsVJ show from the command line.
#
#   ./tools/run_td.sh           pull latest, open TD, rebuild (GUI stays open)
#   ./tools/run_td.sh --check   pull, rebuild headless, print build_report.txt, quit
#
# One-time setup: in TouchDesigner, run touchdesigner/builders/make_bootstrap.py
# once to create physicsvj.toe (a .toe is binary, so only TD can write it).
#
# Overrides via env:
#   TOUCHDESIGNER_APP  full path to the TD binary
#   PHYSICSVJ_TOE      path to the bootstrap .toe
#   PHYSICSVJ_NO_PULL=1  skip the git pull
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOE="${PHYSICSVJ_TOE:-$REPO/physicsvj.toe}"

# Self-update: pull FIRST, and if this script changed, re-exec the new copy so
# we never run stale launcher logic (the bug that caused the earlier hang).
if [ "${PHYSICSVJ_NO_PULL:-}" != "1" ] && [ -z "${PHYSICSVJ_REEXEC:-}" ]; then
  echo "==> git pull"
  before="$(shasum "$0" 2>/dev/null | awk '{print $1}')"
  git -C "$REPO" pull --ff-only || echo "(pull skipped/failed; using current files)"
  after="$(shasum "$0" 2>/dev/null | awk '{print $1}')"
  if [ -n "$before" ] && [ "$before" != "$after" ]; then
    echo "==> run_td.sh changed in the pull; re-running the updated version"
    PHYSICSVJ_REEXEC=1 exec "$0" "$@"
  fi
fi

# Locate the TouchDesigner binary (macOS default; allow override).
TD="${TOUCHDESIGNER_APP:-}"
if [ -z "$TD" ]; then
  for c in \
    "/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner" \
    /Applications/TouchDesigner*/Contents/MacOS/TouchDesigner; do
    [ -x "$c" ] && TD="$c" && break
  done
fi
if [ -z "$TD" ] || [ ! -x "$TD" ]; then
  echo "Could not find the TouchDesigner binary. Set TOUCHDESIGNER_APP to it, e.g.:"
  echo "  export TOUCHDESIGNER_APP=/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner"
  exit 1
fi

if [ ! -f "$TOE" ]; then
  echo "Bootstrap .toe not found at: $TOE"
  echo "In TouchDesigner, run touchdesigner/builders/make_bootstrap.py once to create it."
  exit 1
fi

if [ "${1:-}" = "--check" ]; then
  REPORT="$REPO/build_report.txt"
  TIMEOUT="${PHYSICSVJ_TIMEOUT:-300}"
  rm -f "$REPORT"
  echo "==> headless rebuild ($TOE), up to ${TIMEOUT}s"
  # Run TD in the background so we never wait on it forever (e.g. if quit fails
  # or a startup dialog appears). Poll for the report's END marker, then stop TD.
  PHYSICSVJ_QUIT=1 "$TD" "$TOE" >/dev/null 2>&1 &
  TDPID=$!
  done=0
  for _ in $(seq 1 "$TIMEOUT"); do
    if [ -f "$REPORT" ] && grep -q "== END ==" "$REPORT" 2>/dev/null; then done=1; break; fi
    kill -0 "$TDPID" 2>/dev/null || break   # TD exited on its own
    sleep 1
  done
  kill "$TDPID" 2>/dev/null; sleep 1; kill -9 "$TDPID" 2>/dev/null
  echo "================ build_report.txt ================"
  if [ -f "$REPORT" ]; then
    cat "$REPORT"
    if [ "$done" != 1 ]; then
      echo
      echo "*** build did NOT reach the end within ${TIMEOUT}s ***"
      echo "*** the log above stops at the operation that stalled ***"
    fi
  else
    echo "(no report written within ${TIMEOUT}s)"
    echo "TD likely didn't run onStart -- e.g. a startup dialog is blocking, or the"
    echo "'physics_startup' Execute DAT wasn't created. Open physicsvj.toe once in the"
    echo "GUI to dismiss any dialog and confirm that DAT exists at '/', then retry."
  fi
else
  echo "==> launching TouchDesigner ($TOE)"
  "$TD" "$TOE"
fi
