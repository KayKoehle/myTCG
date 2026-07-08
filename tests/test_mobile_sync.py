"""The Android tree must stay a byte-identical copy of the shared masters."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_mobile_tree_in_sync():
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "sync_mobile.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
