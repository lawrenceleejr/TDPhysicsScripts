#!/usr/bin/env python3
"""Download a small real CMS Open Data dimuon dataset for the open-data scene.

By default this fetches the classic ``Dimuon_DoubleMu.csv`` from CERN Open
Data record 545 (CC0). It is a few MB of real LHC collisions: two muons per
event, whose invariant-mass histogram shows the J/psi, Upsilon and Z peaks.

Usage:
    python3 data/fetch_opendata.py                 # default dataset
    python3 data/fetch_opendata.py --url <CSV_URL> # a different CSV
    python3 data/fetch_opendata.py --out data/dimuon.csv

The open-data scene automatically prefers ``data/dimuon.csv`` if present and
otherwise falls back to the bundled ``data/dimuon_sample.csv``, so this step
is optional.
"""

import argparse
import os
import sys
import time
import urllib.request

DEFAULT_URL = "https://opendata.cern.ch/record/545/files/Dimuon_DoubleMu.csv"
# Alternative (larger) dataset: the Run2010B muon sample, record 700:
#   https://opendata.cern.ch/record/700/files/MuRun2010B.csv
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dimuon.csv")


def download(url: str, out: str, retries: int = 4) -> None:
    delay = 2.0
    for attempt in range(1, retries + 1):
        try:
            print(f"Downloading {url}\n  -> {out} (attempt {attempt}/{retries})")
            req = urllib.request.Request(url, headers={"User-Agent": "TDPhysicsScripts"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            with open(out, "wb") as fh:
                fh.write(data)
            print(f"Done: {len(data)/1024:.0f} KB written.")
            return
        except Exception as e:  # noqa: BLE001
            print(f"  failed: {e}")
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2


def verify(out: str) -> None:
    with open(out, "r", newline="") as fh:
        header = fh.readline().strip()
        n = sum(1 for _ in fh)
    print(f"Header: {header}")
    print(f"Rows:   {n}")
    lower = header.lower()
    if "px1" not in lower or "m" not in lower.split(","):
        print(
            "WARNING: expected dimuon columns (px1, py1, ... M) not all found. "
            "The loader matches columns by name and recomputes M if missing, "
            "but double-check this CSV is a dimuon dataset."
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    try:
        download(args.url, args.out)
        verify(args.out)
    except Exception as e:  # noqa: BLE001
        print(
            f"\nCould not download the dataset ({e}).\n"
            "No problem -- the open-data scene falls back to the bundled "
            "synthetic sample (data/dimuon_sample.csv), which still shows the "
            "J/psi, Upsilon and Z resonances.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
