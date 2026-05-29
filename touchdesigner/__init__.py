"""TouchDesigner adapter layer for TDPhysicsScripts.

``td_build`` assembles ready-to-run scene networks inside TouchDesigner; the
``callbacks`` package holds the Script TOP/CHOP/SOP callback sources that the
builder embeds. None of this is importable outside TouchDesigner (it uses TD
globals like ``op``/``absTime``), but ``td_build`` itself only touches TD APIs
lazily inside its functions, so importing the package is harmless elsewhere.
"""
