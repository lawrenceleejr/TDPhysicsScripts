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

if [ "${PHYSICSVJ_NO_PULL:-}" != "1" ]; then
  echo "==> git pull"
  git -C "$REPO" pull --ff-only || echo "(pull skipped/failed; using current files)"
fi

if [ ! -f "$TOE" ]; then
  echo "Bootstrap .toe not found at: $TOE"
  echo "In TouchDesigner, run touchdesigner/builders/make_bootstrap.py once to create it."
  exit 1
fi

if [ "${1:-}" = "--check" ]; then
  echo "==> headless rebuild ($TOE)"
  PHYSICSVJ_QUIT=1 "$TD" "$TOE"
  echo "================ build_report.txt ================"
  cat "$REPO/build_report.txt" 2>/dev/null || echo "(no report written)"
else
  echo "==> launching TouchDesigner ($TOE)"
  "$TD" "$TOE"
fi
