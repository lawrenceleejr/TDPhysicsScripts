"""Launch-time entry point for the PhysicsVJ show.

A bootstrap ``.toe`` (created once by ``builders/make_bootstrap.py``) has an
Execute DAT whose ``onStart`` calls :func:`run`. Because every TouchDesigner
launch is a fresh process, re-running from the command line always picks up the
latest files on disk -- no copy-paste, no module-cache dance.

:func:`run` builds the whole show and writes ``build_report.txt`` next to the
repo *as it goes* (line-buffered), so even if a build stalls the file shows
exactly how far it got. It ends with an ``== END ==`` marker the launcher polls
for. The report has the TD version, the captured ``[td_build]`` log, and every
operator error (GLSL compile failures, missing files, unknown op types).
"""
import contextlib
import os
import sys
import traceback


class _Tee:
    """Write to several streams at once, flushing each time (so a stalled
    build still leaves its progress on disk)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def _collect_errors(root):
    out = []
    try:
        vj = root.op("PhysicsVJ")
        kids = vj.findChildren(maxDepth=20) if vj is not None else []
        for o in kids:
            try:
                e = o.errors()
            except Exception:
                e = ""
            try:
                w = o.warnings()
            except Exception:
                w = ""
            msg = "\n".join(x for x in (e, w) if x).strip()
            if msg:
                out.append("%s:\n  %s" % (o.path, msg.replace("\n", "\n  ")))
    except Exception as e2:
        out.append("(error walk failed: %s)" % e2)
    return "\n".join(out) if out else "(none)"


def _quit():
    """Quit TD, tolerating API differences across builds."""
    for call in (lambda: project.quit(force=True),  # noqa: F821
                 lambda: project.quit(),             # noqa: F821
                 lambda: app.exit()):                # noqa: F821
        try:
            call()
            return
        except Exception:
            continue


def run(root, build="all"):
    """Build the show under ``root``; stream a report to build_report.txt.
    Quits TD afterward if PHYSICSVJ_QUIT is set (the --check path)."""
    from touchdesigner import td_build

    repo = td_build.REPO
    path = os.path.join(repo, "build_report.txt")
    f = open(path, "w", buffering=1)  # line-buffered: progress survives a stall
    f.write("# PhysicsVJ build report\n")
    try:
        f.write("TD version: %s   build: %s\n" % (app.version, app.build))  # noqa: F821
    except Exception:
        f.write("TD version: (unknown)\n")
    f.write("repo: %s\n\n== [td_build] log ==\n" % repo)
    f.flush()

    base = None
    tee = _Tee(sys.stdout, f)
    try:
        with contextlib.redirect_stdout(tee):
            if build == "all":
                base = td_build.build_all(root)
            else:
                base = getattr(td_build, "build_" + build)(root)
    except Exception:
        f.write("\n== BUILD RAISED ==\n" + traceback.format_exc() + "\n")

    f.write("\n== operator errors / warnings ==\n")
    f.write(_collect_errors(root) + "\n")
    f.write("== END ==\n")
    f.flush()
    try:
        f.close()
    except Exception:
        pass

    if os.environ.get("PHYSICSVJ_QUIT"):
        _quit()
    return base
