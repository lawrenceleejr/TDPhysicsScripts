"""The working layout: three panes, arranged the way the show is run.

    network editor          |  the program output
    (left, the whole show)  |------------------------
                            |  the APC, live

The left pane is where the patch is edited, the upper right is exactly what the
audience is being sent, and the lower right is the controller with its real LED
state -- so a pad can be checked without looking away from the output.

TouchDesigner's pane API differs between builds (which method splits a pane,
what the type enum is called), so every call here is tried against several
spellings and the whole thing degrades to "leave the layout alone" rather than
raising during a build. :func:`three_panel` reports what it managed.
"""
from __future__ import annotations


def _td(name):
    """A TouchDesigner global, from inside an imported module (TD injects them
    into DAT scripts, not into modules; the ``td`` module carries them)."""
    for modname in ("td", "builtins"):
        try:
            val = getattr(__import__(modname), name, None)
            if val is not None:
                return val
        except Exception:
            pass
    return None


def _pane_type(name):
    """A PaneType enum member by name, whatever this build calls the enum."""
    pt = _td("PaneType")
    if pt is None:
        return name.lower()
    for attr in (name, name.upper(), name.capitalize()):
        got = getattr(pt, attr, None)
        if got is not None:
            return got
    return name.lower()


def _split(pane, direction):
    """Split ``pane`` and return the new one. ``direction`` is 'right' or
    'bottom'; the method that does it is named differently across builds."""
    names = {
        "right": ("splitRight", "splitVertical", "splitHorizontal"),
        "bottom": ("splitBottom", "splitHorizontal", "splitVertical"),
    }[direction]
    for nm in names:
        fn = getattr(pane, nm, None)
        if fn is None:
            continue
        try:
            new = fn()
            return new if new is not None else pane
        except Exception:
            continue
    return None


def _set_pane(pane, kind, owner=None, ratio=None):
    """Point a pane at what it should show. Returns True if the type took."""
    ok = False
    try:
        pane.type = _pane_type(kind)
        ok = True
    except Exception:
        pass
    if owner is not None:
        for attr in ("owner", "owner_comp", "comp"):
            try:
                setattr(pane, attr, owner)
                break
            except Exception:
                continue
    if ratio is not None:
        try:
            pane.ratio = float(ratio)
        except Exception:
            pass
    return ok


def three_panel(program=None, apc=None, ratio=0.58, verbose=True):
    """Arrange the three panes. ``program`` and ``apc`` are the COMPs whose
    panels fill the two right-hand panes (containers with their Top parameter
    set). Returns the panes it ended up with, or None if this build's pane API
    could not be driven.
    """
    ui = _td("ui")
    if ui is None or not getattr(ui, "panes", None):
        if verbose:
            print("[layout] no pane API on this build; layout left alone")
        return None
    try:
        # Collapse to one pane first, so running this twice does not keep
        # splitting the window into ever thinner strips.
        while len(ui.panes) > 1:
            try:
                ui.panes[-1].close()
            except Exception:
                break
        left = ui.panes[0]
        _set_pane(left, "NetworkEditor", ratio=ratio)
        right = _split(left, "right")
        if right is None:
            if verbose:
                print("[layout] could not split the pane; layout left alone")
            return None
        _set_pane(right, "Panel", owner=program)
        lower = _split(right, "bottom")
        if lower is not None:
            _set_pane(lower, "Panel", owner=apc)
        if verbose:
            print("[layout] three panes: network | program / APC "
                  "(%d panes)" % len(ui.panes))
        return [p for p in ui.panes]
    except Exception as e:
        if verbose:
            print("[layout] arranging the panes raised: %s" % e)
        return None
