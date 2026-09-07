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


def _collect_errors(root, names=("PhysicsVJ", "APCShow")):
    out = []
    try:
        kids = []
        for name in names:
            comp = root.op(name)
            if comp is not None:
                kids.append(comp)
                kids.extend(comp.findChildren(maxDepth=20))
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
                if "compile" in msg.lower():
                    msg += "\n" + _glsl_compile_log(o)
                out.append("%s:\n  %s" % (o.path, msg.replace("\n", "\n  ")))
    except Exception as e2:
        out.append("(error walk failed: %s)" % e2)
    return "\n".join(out) if out else "(none)"


def _td_global(name):
    """A TouchDesigner global (app, project ...) from inside an imported module.

    TD injects these into DAT scripts, not into modules they import; the
    ``td`` module carries them. Bare names here raised NameError, which is why
    the report said "TD version: (unknown)" and the --check run had to be
    killed by the launcher instead of quitting itself.
    """
    for modname in ("td", "builtins"):
        try:
            val = getattr(__import__(modname), name, None)
            if val is not None:
                return val
        except Exception:
            pass
    return None


def _glsl_compile_log(o):
    """The shader compile log of a GLSL TOP/MAT, read through a temporary Info
    DAT (the node itself only says "has compile errors, use an Info DAT")."""
    info = None
    try:
        info = o.parent().create("infoDAT", "glsl_log_tmp")
        info.par.op = o
        info.cook(force=True)
        text = str(info.text).strip()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        # Keep the compile-result sections; drop the long list of uniforms.
        keep, on = [], False
        for ln in lines:
            low = ln.lower()
            if "compile" in low or "error" in low or "warning" in low:
                on = True
            if on:
                keep.append(ln)
        return "-- shader log --\n" + "\n".join(keep[:60] or lines[:60])
    except Exception as e:
        return "(could not read the shader log: %s)" % e
    finally:
        try:
            if info is not None:
                info.destroy()
        except Exception:
            pass


def _quit():
    """Quit TD, tolerating API differences across builds."""
    project, app = _td_global("project"), _td_global("app")
    for call in (lambda: project.quit(force=True),
                 lambda: project.quit(),
                 lambda: app.exit()):
        try:
            call()
            return
        except Exception:
            continue


def run(root, build=None):
    """Build the show under ``root``; stream a report to build_report.txt.

    ``build`` is "all" (the whole show) or one scene's builder suffix, e.g.
    "nbody" for ``td_build.build_nbody``. When not given it comes from the
    PHYSICSVJ_BUILD environment variable (``run_td.sh --scene nbody`` sets it)
    and defaults to "all". Quits TD afterward if PHYSICSVJ_QUIT is set (the
    --check path)."""
    from touchdesigner import td_build

    if build is None:
        build = os.environ.get("PHYSICSVJ_BUILD", "").strip() or "all"

    repo = td_build.REPO
    path = os.path.join(repo, "build_report.txt")
    f = open(path, "w", buffering=1)  # line-buffered: progress survives a stall
    f.write("# PhysicsVJ build report\n")
    try:
        app = _td_global("app")
        f.write("TD version: %s   build: %s   %s\n" % (app.version, app.build, app.osName))
    except Exception:
        f.write("TD version: (unknown)\n")
    f.write("repo: %s\nbuild: %s\n\n== [td_build] log ==\n" % (repo, build))
    f.flush()

    base = None
    tee = _Tee(sys.stdout, f)
    try:
        with contextlib.redirect_stdout(tee):
            if build == "all":
                base = td_build.build_all(root)
            else:
                builder = getattr(td_build, "build_" + build, None)
                if builder is None:
                    names = sorted(n[6:] for n in dir(td_build) if n.startswith("build_"))
                    raise ValueError("no builder 'build_%s'; choose one of: %s"
                                     % (build, ", ".join(names)))
                base = builder(root)
    except Exception:
        f.write("\n== BUILD RAISED ==\n" + traceback.format_exc() + "\n")

    f.write("\n== operator errors / warnings ==\n")
    names = ("PhysicsVJ", "APCShow") if build == "all" else ((base.name,) if base is not None else ())
    f.write(_collect_errors(root, names) + "\n")
    f.write("== END ==\n")
    f.flush()
    try:
        f.close()
    except Exception:
        pass

    if os.environ.get("PHYSICSVJ_QUIT"):
        _quit()
    elif base is not None:
        _show_controls(root, base)
    return base


def _show_controls(root, base):
    """Park the network editor at the root with the show COMP selected, so its
    parameter dialog (Scene, Nextscene, Crossfade, Freerun All) is what you see
    when TD opens -- the controls the README talks about live on that node."""
    try:
        ui = _td_global("ui")
        for pane in ui.panes:
            if pane.type == _td_global("PaneType").NETWORKEDITOR:
                pane.owner = root
                break
    except Exception:
        try:
            ui.panes[0].owner = root
        except Exception:
            pass
    try:
        base.current = True
        base.selected = True
    except Exception:
        pass
