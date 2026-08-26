"""Shared fixtures for the workflow tests (``test_wf_*.py``).

``project`` is a realistic multi-folder project (no session yet);
``project_with_session`` adds one session, the normal state of a folder
the user has opened before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alxedit2 import sessions
from alxedit2 import settings as cfg

#: The project layout the workflow tests run against.
PROJECT_FILES: dict[str, str] = {
    "src/hello.py": "def hello():\n    return 'world'\n",
    "src/app.js": "const a = 1;\n",
    "src/utils/helper.py": "def helper():\n    return 42\n",
    "docs/guide.md": "# Guide\n\nhello\n",
    "config.json": "{\n  \"theme\": \"dark\"\n}\n",
    "notes.txt": "remember the milk\n",
    ".env": "SECRET=1\n",
    "vendor/lib.py": "VENDOR = True\n",
}


def make_session(root: Path) -> str:
    """Create a session mirroring the current tree (what the UI's New
    button does)."""
    sid = sessions.create_session(root)
    for f in sessions.iter_tracked_files(root, cfg.load(root)):
        sessions.copy_to_mirror(root, sid, f)
    return sid


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A realistic project folder, no session yet."""
    for rel, text in PROJECT_FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / ".alxeditrc").write_text("# project settings\n")
    return tmp_path


@pytest.fixture()
def project_with_session(project: Path) -> Path:
    """The project with one active-style session (baseline = current tree)."""
    make_session(project)
    return project
