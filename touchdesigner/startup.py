"""Launch-time entry point for the PhysicsVJ show.

A bootstrap ``.toe`` (created once by ``builders/make_bootstrap.py``) has an
Execute DAT whose ``onStart`` calls :func:`run`. Because every TouchDesigner
launch is a fresh process, re-running from the command line always picks up the
latest files on disk -- no copy-paste, no module-cache dance.

:func:`run` builds the whole show and writes ``build_report.txt`` next to the
repo: TD version, the captured ``[td_build]`` log, and every operator error
(GLSL compile failures, missing files, unknown op types, ...). That single file
is what to share when something looks wrong -- it pinpoints the exact node.
"""
import contextlib
import io
import os
import traceback


def _report(root, repo, build_log, err):
    L = ["# PhysicsVJ build report"]
    try:
        L.append("TD version: %s   build: %s" % (app.version, app.build))  # noqa: F821
    except Exception:
        L.append("TD version: (unknown)")
    L.append("repo: " + repo)
    L.append("")
    if err:
        L += ["== BUILD RAISED ==", err, ""]
    L.append("== [td_build] log ==")
    L.append(build_log.strip() or "(no output captured)")
    L.append("")
    L.append("== operator errors / warnings ==")
    found = []
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
                found.append("%s:\n  %s" % (o.path, msg.replace("\n", "\n  ")))
    except Exception as e2:
        found.append("(error walk failed: %s)" % e2)
    L.append("\n".join(found) if found else "(none)")
    return "\n".join(L) + "\n"


def run(root, build="all"):
    """Build the show under ``root`` and write build_report.txt. Returns the
    PhysicsVJ COMP (or None on failure). Quits TD afterward if PHYSICSVJ_QUIT
    is set in the environment (used by the --check command-line path)."""
    from touchdesigner import td_build

    repo = td_build.REPO
    buf = io.StringIO()
    err = ""
    base = None
    try:
        with contextlib.redirect_stdout(buf):
            if build == "all":
                base = td_build.build_all(root)
            else:
                base = getattr(td_build, "build_" + build)(root)
    except Exception:
        err = traceback.format_exc()

    report = _report(root, repo, buf.getvalue(), err)
    try:
        with open(os.path.join(repo, "build_report.txt"), "w") as fh:
            fh.write(report)
    except Exception:
        pass
    print(report)  # also goes to the Textport

    if os.environ.get("PHYSICSVJ_QUIT"):
        try:
            project.quit(force=True)  # noqa: F821 (TD global)
        except Exception:
            pass
    return base
