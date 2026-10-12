#!/usr/bin/env python3
"""Convenience readiness check for Research Helper Skill."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


if __name__ == "__main__":
    entry = Path(__file__).resolve().with_name("tool_entry.py")
    raise SystemExit(subprocess.call([sys.executable, str(entry), "environment-check"]))
