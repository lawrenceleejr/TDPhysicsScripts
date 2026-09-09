#!/usr/bin/env python3
"""Download the show's Google Fonts as TTF, for TouchDesigner to load.

TouchDesigner's Text TOP loads TrueType, not woff2, and the Google Fonts CSS
API only serves subsetted dynamic fonts, so these come from the google/fonts
repository, which holds the real .ttf files.

    python assets/get_fonts.py

All Google Fonts are OFL/Apache/UFL: free to bundle and redistribute. Each
family's licence is fetched alongside it.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")

RAW = "https://raw.githubusercontent.com/google/fonts/main"

# The show's type. Two families and a mono, each with one job:
#   Archivo Black   the big ink titles -- a heavy grotesque reads at 300 px
#   Archivo         labels and UI -- the same superfamily, so it pairs by build
#   JetBrains Mono  the instrument readouts -- technical, even at small sizes
#
# Fetched from the google/fonts repository rather than the CSS API: that API
# serves a subsetted dynamic font (its bytes are not even TrueType), while the
# repository holds the real files. Archivo and JetBrains Mono ship only as
# variable fonts upstream; TouchDesigner renders their default instance, which
# is the regular weight, and that is the one wanted here.
FONTS = [
    ("ArchivoBlack-Regular.ttf", "ofl/archivoblack/ArchivoBlack-Regular.ttf", "archivoblack"),
    ("Archivo-Variable.ttf", "ofl/archivo/Archivo%5Bwdth,wght%5D.ttf", "archivo"),
    ("JetBrainsMono-Variable.ttf", "ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf", "jetbrainsmono"),
]
TRUETYPE_MAGIC = (b"\x00\x01\x00\x00", b"true", b"ttcf", b"OTTO")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=120) as fh:
        return fh.read()


def fetch(name, path, slug, out_dir=FONT_DIR):
    """Download one font file and its licence. Returns the path written."""
    os.makedirs(out_dir, exist_ok=True)
    data = _get("%s/%s" % (RAW, path))
    if not data.startswith(TRUETYPE_MAGIC):
        raise SystemExit("%s did not come back as TrueType (%r)" % (name, data[:4]))
    dest = os.path.join(out_dir, name)
    with open(dest, "wb") as fh:
        fh.write(data)
    print("  %-28s %6d KB" % (name, len(data) // 1024))
    lic_name = "%s-OFL.txt" % name.split("-")[0]
    if not os.path.exists(os.path.join(out_dir, lic_name)):
        try:
            with open(os.path.join(out_dir, lic_name), "w") as fh:
                fh.write(_get("%s/ofl/%s/OFL.txt" % (RAW, slug)).decode("utf8"))
        except Exception as e:
            print("  (licence for %s not fetched: %s)" % (name, e))
    return dest


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=FONT_DIR)
    args = ap.parse_args(argv[1:])
    for name, path, slug in FONTS:
        fetch(name, path, slug, args.out)
    print("fonts in", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
