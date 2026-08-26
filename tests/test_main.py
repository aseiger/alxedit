"""CLI entry-point tests (``python -m alxedit2``)."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys

from alxedit2 import __version__


def test_version_matches_packaged_metadata() -> None:
    """The version in source and the installed package metadata must agree
    — the guard against forgetting to re-sync them at release time."""
    assert __version__ == importlib.metadata.version("alxedit2")


def test_version_flag_prints_and_exits() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "alxedit2", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == f"alxedit2 {__version__}"
