"""Per-project settings in ``.alxeditrc`` at the project root.

The file is plain text (also hand-editable), one directive per line,
``#`` starts a comment::

    ignore <path>   never mirror/track this file or folder
    track  <path>   mirror/track this dot file/dot folder despite the default

Paths are relative to the project root, ``/``-separated, case-insensitive.
An entry matches the file or folder itself and, for a folder, everything
below it.

Defaults (no file, or no directive for a path):

- regular files and folders are tracked;
- dot files and dot folders (``.env``, ``.github/``, ...) are **not**
  tracked unless listed with ``track``;
- ``ignore`` always wins.

"Tracked" means: copied into the session mirror (the diff/revert
baseline) and included in change tracking. The explorer always shows
every file regardless of tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Name of the per-project settings file.
RC_NAME = ".alxeditrc"


@dataclass(frozen=True)
class Settings:
    """A parsed ``.alxeditrc``: normalized ignore/track path lists."""

    ignore: tuple[str, ...] = ()
    track: tuple[str, ...] = ()


def normalize(entry: str | Path) -> str:
    """Canonical form of a path entry: ``/``-separated, no empty/``.``
    parts, no leading ``./`` or trailing slash."""
    parts = [
        part
        for part in str(entry).replace("\\", "/").strip().split("/")
        if part not in ("", ".")
    ]
    return "/".join(parts)


def _matches(entry: str, rel: str) -> bool:
    """True if *rel* (root-relative, ``/``-separated) is *entry* itself or
    below it. Case-insensitive (Windows paths)."""
    e, r = entry.casefold(), rel.casefold()
    return r == e or r.startswith(e + "/")


def is_dot(rel: str) -> bool:
    """True if any path component is a dot component (``.env``, ``a/.b``)."""
    return any(part.startswith(".") for part in rel.split("/") if part)


def should_track(settings: Settings, rel: str) -> bool:
    """Whether a root-relative path is mirrored/tracked."""
    if any(_matches(entry, rel) for entry in settings.ignore):
        return False
    if is_dot(rel):
        return any(_matches(entry, rel) for entry in settings.track)
    return True


def parse(text: str) -> Settings:
    """Parse ``.alxeditrc`` content. Unknown directives and invalid
    entries (empty, escaping the root) are skipped."""
    ignore: list[str] = []
    track: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        verb, arg = parts[0].lower(), normalize(parts[1])
        if not arg or ".." in arg.split("/"):
            continue
        if verb == "ignore":
            ignore.append(arg)
        elif verb == "track":
            track.append(arg)
    # de-duplicate, keep order
    return Settings(tuple(dict.fromkeys(ignore)), tuple(dict.fromkeys(track)))


def load(root: Path) -> Settings:
    """Project settings from ``<root>/.alxeditrc`` (empty if absent)."""
    try:
        return parse((Path(root) / RC_NAME).read_text(encoding="utf-8"))
    except OSError:
        return Settings()


def save(root: Path, settings: Settings) -> None:
    """Write ``<root>/.alxeditrc`` (round-trips :func:`load`)."""
    lines = [
        "# alxedit2 project settings",
        "# the explorer always shows every file; these control what the",
        "# session mirror (diff/revert baseline) tracks",
        "#",
        "# ignore <path>  never mirror/track this file or folder",
        "# track  <path>  mirror/track this dot file/dot folder despite the default",
        "",
    ]
    lines += [f"ignore {entry}" for entry in settings.ignore]
    lines += [f"track {entry}" for entry in settings.track]
    (Path(root) / RC_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
