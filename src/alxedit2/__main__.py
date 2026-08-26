"""Command line entry point for alxedit2."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .app import AlxEditApp


def resolve_root(
    paths: list[Path], root: Path | None = None
) -> tuple[Path, list[Path]]:
    """Decide (working directory, files to open) from CLI arguments.

    Rules:
    - ``root`` (from ``--root``) wins if given.
    - else the first directory in ``paths`` becomes the working directory.
    - else the current directory.
    """
    expanded = [p.expanduser() for p in paths]
    dirs = [p for p in expanded if p.is_dir()]
    files = [p for p in expanded if p.is_file()]
    missing = [p for p in expanded if not (p.is_dir() or p.is_file())]
    if missing:
        raise FileNotFoundError(f"path not found: {missing[0]}")

    if root is not None:
        resolved = root.expanduser().resolve()
    elif dirs:
        resolved = dirs[0].resolve()
    else:
        resolved = Path.cwd().resolve()
    if not resolved.is_dir():
        raise NotADirectoryError(f"not a directory: {resolved}")
    return resolved, files


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="alxedit2",
        description=(
            "A personal IDE-style TUI file editor: "
            "file explorer + tabbed, syntax-highlighted editor."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"alxedit2 {__version__}",
        help="print the version and exit",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=(
            "files to open, and/or a directory to use as the working "
            "directory (the first directory given becomes the working dir)"
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "working directory for the file explorer "
            "(default: first directory argument, else the current directory)"
        ),
    )
    args = parser.parse_args()

    try:
        root, files = resolve_root(args.paths, args.root)
    except (FileNotFoundError, NotADirectoryError) as exc:
        parser.error(str(exc))

    app = AlxEditApp(root=root, paths=files)
    app.run()


if __name__ == "__main__":
    main()
