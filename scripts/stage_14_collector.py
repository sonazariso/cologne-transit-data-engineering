#!/usr/bin/env python3
"""Thin Stage 14 one-heartbeat entry point."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from cologne_transit_collector.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
