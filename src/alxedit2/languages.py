"""Map file extensions to Textual's built-in tree-sitter language names.

Only languages shipped with ``textual[syntax]`` are mapped; anything else
is edited as plain text (no highlighting). TypeScript falls back to the
JavaScript grammar, which highlights most TS source acceptably.
"""

from __future__ import annotations

from pathlib import Path

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    # Python
    ".py": "python",
    ".pyi": "python",
    # Rust
    ".rs": "rust",
    # Shell
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    # JavaScript / TypeScript (TS via the JS grammar — see module docstring)
    ".js": "javascript",
    ".cjs": "javascript",
    ".mjs": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    # Data
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    # Web
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".xml": "xml",
    # Languages
    ".go": "go",
    ".java": "java",
    ".sql": "sql",
    # Docs
    ".md": "markdown",
    ".markdown": "markdown",
}


def language_for_path(path: str | Path) -> str | None:
    """Best-effort highlighting language for *path*, or ``None`` (plain text)."""
    return LANGUAGE_BY_EXTENSION.get(Path(path).suffix.lower())
