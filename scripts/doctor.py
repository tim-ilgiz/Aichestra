#!/usr/bin/env python3
"""Convenience wrapper: python scripts/doctor.py"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from aichestra.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["doctor", *sys.argv[1:]]))
