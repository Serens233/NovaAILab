"""Test bootstrap: make the repository root importable without installing it."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TESTS = Path(__file__).resolve().parent
for item in (ROOT, ROOT / "src", TESTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))
