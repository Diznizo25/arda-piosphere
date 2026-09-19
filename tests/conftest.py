"""Shared test setup.

Every test in this suite must run with NO network, NO database and NO
credentials, so it can gate a pull request in CI. Anything needing live
infrastructure belongs in scripts/ as an operator tool, not here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
