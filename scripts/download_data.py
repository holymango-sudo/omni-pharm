"""Download the bulk public sources (resumable, SHA-256 manifest in data/raw/MANIFEST.json).

Usage:
    .venv\\Scripts\\python.exe scripts\\download_data.py              # all: twosides + openfda_label
    .venv\\Scripts\\python.exe scripts\\download_data.py twosides
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omnipharm.data.download import fetch
from omnipharm.data.sources import SOURCES


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="*", help=f"any of {list(SOURCES)} (default: all)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    failed = 0
    for name in args.sources or list(SOURCES):
        if name not in SOURCES:
            print(f"unknown source: {name}", file=sys.stderr)
            failed += 1
            continue
        try:
            paths = fetch(name)
            print(f"[OK] {name}: {len(paths)} file(s) — license: {SOURCES[name].license}")
        except Exception as exc:  # report and continue with other sources
            print(f"[FAIL] {name}: {exc}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
