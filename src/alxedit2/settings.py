"""Per-project settings in ``.alxeditrc`` at the project root.

The file is plain text (also hand-editable), one directive per line,
``#`` starts a comment::

    ignore <path>   never mirror/track this file or folder
    track  <path>   mirror/track this dot file/dot folder despite the default

Paths are relative to the project root, ``/``-separated, case-insensitive.
A rule on a folder is **recursive**: it covers the folder and everything
below it.

Precedence: rules are evaluated in file order and the **last matching
rule wins** — the most recent Track/Untrack on a path is the one in
effect (like later lines overriding earlier ones in ``.gitignore``).
So untracking a folder is always decisive, and you can still pick
individual files back out of an ignored folder afterwards::

    ignore src        # untrack the whole folder (recursive)
    track src/app.py  # ...but keep this one file

Entries may use glob characters (``*``, ``?``, ``[``); ``*`` crosses
directory boundaries::

    ignore *.log      every .log file, wherever it is
    ignore dist/*     everything under dist/
    track  .github/*  the workflows inside the dot folder

Defaults (no rule matches the path):

- regular files and folders are tracked;
- dot files and dot folders (``.env``, ``.github/``, ...) are **not**
  tracked unless listed with ``track``.

"Tracked" means: copied into the session mirror (the diff/revert
baseline) and included in change tracking. The explorer always shows
every file regardless of tracking.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

#: Name of the per-project settings file.
RC_NAME = ".alxeditrc"

#: One rule: ``("ignore" | "track", normalized root-relative path)``.
Rule = tuple[str, str]


@dataclass(frozen=True)
class Settings:
    """A parsed ``.alxeditrc`` as an ordered list of rules.

    The LAST rule matching a path is the one that applies (most recent
    action wins); a rule on a folder covers everything below it. With
    no matching rule, regular paths are tracked and dot paths are not.
    """

    rules: tuple[Rule, ...] = ()

    @property
    def ignore(self) -> tuple[str, ...]:
        """The ``ignore`` paths, in rule order (for display and compat)."""
        return tuple(path for verb, path in self.rules if verb == "ignore")

    @property
    def track(self) -> tuple[str, ...]:
        """The ``track`` paths, in rule order (for display and compat)."""
        return tuple(path for verb, path in self.rules if verb == "track")


def normalize(entry: str | Path) -> str:
    """Canonical form of a path entry: ``/``-separated, no empty/``.``
    parts, no leading ``./`` or trailing slash."""
    parts = [
        part
        for part in str(entry).replace("\\", "/").strip().split("/")
        if part not in ("", ".")
    ]
    return "/".join(parts)


def _is_glob(entry: str) -> bool:
    """True if the entry uses glob characters (``*``, ``?``, ``[``)."""
    return any(ch in entry for ch in ("*", "?", "["))


def _glob_match(entry: str, rel: str) -> bool:
    """Glob-match *rel* against *entry*.

    The relative path itself and each of its folder prefixes are tried,
    and ``*`` crosses directory boundaries (fnmatch semantics), so
    ``*.log`` catches ``src/deep/a.log`` and ``dist/*`` catches
    ``dist/x/y.js``. Case-insensitive (Windows paths).
    """
    pattern = entry.casefold()
    parts = rel.casefold().split("/")
    for i in range(len(parts), 0, -1):
        if fnmatch.fnmatchcase("/".join(parts[:i]), pattern):
            return True
    return False


def _matches(entry: str, rel: str) -> bool:
    """Whether the rule *entry* covers *rel* (root-relative, ``/``-separated).

    Literal entries match the path itself or anything below it; glob
    entries (see :func:`_is_glob`) match the path or a folder prefix
    against the glob. Case-insensitive (Windows paths).
    """
    if _is_glob(entry):
        return _glob_match(entry, rel)
    e, r = entry.casefold(), rel.casefold()
    return r == e or r.startswith(e + "/")


def is_dot(rel: str) -> bool:
    """True if any path component is a dot component (``.env``, ``a/.b``)."""
    return any(part.startswith(".") for part in rel.split("/") if part)


def should_track(settings: Settings, rel: str) -> bool:
    """Whether a root-relative path is mirrored/tracked.

    The last matching rule in file order wins (most recent action);
    with no matching rule, dot paths are untracked and everything
    else is tracked.
    """
    decision: str | None = None
    for verb, entry in settings.rules:
        if _matches(entry, rel):
            decision = verb
    if decision is None:
        return not is_dot(rel)
    return decision == "track"


def can_have_tracked_below(settings: Settings, rel: str) -> bool:
    """Whether any ``track`` rule could cover a path *below* *rel*.

    Lets the mirror walk decide that an excluded folder can still
    contain tracked files (a later, more specific ``track``) and must
    be descended into.
    """
    r = rel.casefold()
    for verb, entry in settings.rules:
        if verb != "track":
            continue
        if _is_glob(entry):
            return True  # conservative: a glob might reach below
        if entry.casefold().startswith(r + "/"):
            return True
    return False


def parse(text: str) -> Settings:
    """Parse ``.alxeditrc`` content into an ordered rule list.

    Unknown directives and invalid entries (empty, escaping the root)
    are skipped. File order is preserved — it decides precedence (the
    last matching rule wins). Glob characters (``*``, ``?``, ``[``)
    make the entry match as a glob (see :func:`_matches`)."""
    rules: list[Rule] = []
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
            rules.append(("ignore", arg))
        elif verb == "track":
            rules.append(("track", arg))
    return Settings(rules=tuple(rules))


def load(root: Path) -> Settings:
    """Project settings from ``<root>/.alxeditrc`` (empty if absent)."""
    try:
        return parse((Path(root) / RC_NAME).read_text(encoding="utf-8"))
    except OSError:
        return Settings()


def save(root: Path, settings: Settings) -> None:
    """Write ``.alxeditrc`` (round-trips :func:`load`)."""
    lines = [
        "# alxedit2 project settings",
        "# the explorer always shows every file; these control what the",
        "# session mirror (diff/revert baseline) tracks",
        "#",
        "# ignore <path>  never mirror/track this file or folder",
        "# track  <path>  mirror/track this despite the default",
        "# a rule on a folder covers everything below it, and the LAST",
        "# rule for a path wins — e.g. 'ignore build' then 'track build/app.py'",
        "# paths may use globs (* ? [..]) that also match across folders,",
        "# e.g. 'ignore *.log' or 'track .github/*'",
        "",
    ]
    lines += [f"{verb} {path}" for verb, path in settings.rules]
    (Path(root) / RC_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
