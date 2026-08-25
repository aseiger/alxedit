"""Session management: a mirror of the working tree under ``.alxedit/<sid>/``.

Layout::

    <root>/.alxedit/<session-id>/
        session.json     # id, label, created/updated timestamps
        files/           # byte-for-byte copy of the tracked tree at session start

The mirror is the **source of truth for diffs and reverts**: it holds the
content the user last approved (session start, or the editor's most recent
save). When alxedit2 saves a file, the mirror copy is updated too, so the
baseline always tracks the user's latest approval.

Only "tracked" files are mirrored: dot files and dot folders (unless
explicitly tracked, see :mod:`alxedit2.settings`), files the project
settings ignore, ``__pycache__``/``node_modules``, and files larger than
:data:`MAX_TRACK_BYTES` are skipped.
"""

from __future__ import annotations

import json
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from alxedit2 import settings as project_settings

#: Name of the per-project session directory.
SESS_DIR_NAME = ".alxedit"
#: Sub-directories that are never mirrored or tracked.
IGNORED_DIRS: frozenset[str] = frozenset({"__pycache__", "node_modules"})
#: Files larger than this are not mirrored.
MAX_TRACK_BYTES: int = 1_000_000


@dataclass
class Session:
    """One session as found under ``.alxedit/``."""

    id: str
    label: str
    created: str  # ISO-8601
    updated: str  # ISO-8601
    file_count: int


# --------------------------------------------------------------------------- #
# ids & paths
# --------------------------------------------------------------------------- #


def new_session_id() -> str:
    """A sortable, collision-resistant session id: ``20250118-153012-a3f9``."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


def sessions_dir(root: Path) -> Path:
    return Path(root) / SESS_DIR_NAME


def session_dir(root: Path, sid: str) -> Path:
    return sessions_dir(root) / sid


def _meta_path(root: Path, sid: str) -> Path:
    return session_dir(root, sid) / "session.json"


def _files_dir(root: Path, sid: str) -> Path:
    return session_dir(root, sid) / "files"


def mirror_path(root: Path, sid: str, path: Path) -> Path:
    """Location of *path*'s mirror copy. Raises ``ValueError`` if *path*
    is outside *root* or inside ``.alxedit`` itself."""
    rel = Path(path).resolve().relative_to(Path(root).resolve())
    if rel.parts and rel.parts[0] == SESS_DIR_NAME:
        raise ValueError(f"refusing to mirror the session dir: {path}")
    return _files_dir(root, sid) / rel


# --------------------------------------------------------------------------- #
# metadata
# --------------------------------------------------------------------------- #


def _now() -> str:
    # full precision so sessions created in quick succession still sort
    # in creation order
    return datetime.now().isoformat()


def _write_meta(root: Path, sid: str, meta: dict) -> None:
    meta_path = _meta_path(root, sid)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def create_session(root: Path, label: Optional[str] = None) -> str:
    """Create an (empty) session: directory + ``session.json``. Returns the id."""
    sid = new_session_id()
    now = _now()
    if label is None:
        label = datetime.now().strftime("session %Y-%m-%d %H:%M")
    _write_meta(
        root,
        sid,
        {"id": sid, "label": label, "created": now, "updated": now},
    )
    _files_dir(root, sid).mkdir(parents=True, exist_ok=True)
    return sid


def _read_meta(root: Path, sid: str) -> Optional[dict]:
    try:
        return json.loads(_meta_path(root, sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_sessions(root: Path) -> list[Session]:
    """All sessions under *root*, most recently created first."""
    base = sessions_dir(root)
    if not base.is_dir():
        return []
    out: list[Session] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        meta = _read_meta(root, child.name)
        if meta is None:
            continue
        files = _files_dir(root, child.name)
        count = (
            sum(1 for p in files.rglob("*") if p.is_file())
            if files.is_dir()
            else 0
        )
        out.append(
            Session(
                id=child.name,
                label=str(meta.get("label", child.name)),
                created=str(meta.get("created", "")),
                updated=str(meta.get("updated", "")),
                file_count=count,
            )
        )
    # id is a timestamp too — a stable tie-breaker for same-second sessions
    out.sort(key=lambda s: (s.created, s.id), reverse=True)
    return out


def delete_session(root: Path, sid: str) -> None:
    shutil.rmtree(session_dir(root, sid))


def session_label(root: Path, sid: str) -> str:
    meta = _read_meta(root, sid)
    return str(meta.get("label", sid)) if meta is not None else sid


# --------------------------------------------------------------------------- #
# the mirror
# --------------------------------------------------------------------------- #


def iter_tracked_files(
    root: Path, settings: Optional[project_settings.Settings] = None
) -> list[Path]:
    """All tracked files under *root*.

    Skipped: the session dir, ``__pycache__``/``node_modules``, files
    larger than :data:`MAX_TRACK_BYTES`, and anything the project
    *settings* exclude — dot files/folders unless explicitly tracked,
    and explicit ``ignore`` entries.
    """
    st = settings if settings is not None else project_settings.Settings()
    root = Path(root)
    out: list[Path] = []
    stack: list[Path] = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name == SESS_DIR_NAME:
                continue
            rel = entry.relative_to(root).as_posix()
            if not project_settings.should_track(st, rel):
                continue
            if entry.is_dir():
                if entry.name not in IGNORED_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                try:
                    if entry.stat().st_size <= MAX_TRACK_BYTES:
                        out.append(entry)
                except OSError:
                    pass
    out.sort()
    return out


def copy_to_mirror(root: Path, sid: str, path: Path) -> None:
    """Copy one working-tree file into the mirror."""
    dest = mirror_path(root, sid, path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    _touch_meta(root, sid)


def mirror_exists(root: Path, sid: str, path: Path) -> bool:
    try:
        return mirror_path(root, sid, path).is_file()
    except ValueError:
        return False


def read_mirror_text(root: Path, sid: str, path: Path) -> Optional[str]:
    try:
        return mirror_path(root, sid, path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def read_mirror_bytes(root: Path, sid: str, path: Path) -> Optional[bytes]:
    try:
        return mirror_path(root, sid, path).read_bytes()
    except OSError:
        return None


def write_mirror(root: Path, sid: str, path: Path, text: str) -> None:
    """Update the mirror copy of *path* (called after every editor save)."""
    dest = mirror_path(root, sid, path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    _touch_meta(root, sid)


def delete_mirror(root: Path, sid: str, path: Path) -> None:
    try:
        mirror_path(root, sid, path).unlink(missing_ok=True)
    except ValueError:
        pass


def _touch_meta(root: Path, sid: str) -> None:
    meta = _read_meta(root, sid)
    if meta is not None:
        meta["updated"] = _now()
        _write_meta(root, sid, meta)
