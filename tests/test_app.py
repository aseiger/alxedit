"""Smoke tests for the alxedit2 IDE layout, tabs, and saving."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import TabPane, TabbedContent

from alxedit2.__main__ import resolve_root
from alxedit2.app import AlxEditApp, Explorer
from alxedit2.languages import language_for_path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hello.py").write_text("def hello():\n    return 'world'\n")
    (tmp_path / "app.js").write_text("const a = 1;\n")
    (tmp_path / ".hidden").write_text("secret\n")
    (tmp_path / "README.md").write_text("# hi\n")
    return tmp_path


def test_resolve_root_rules(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    f = proj / "a.py"
    f.write_text("x\n")

    # a directory argument becomes the working directory
    root, files = resolve_root([proj, f])
    assert root == proj.resolve()
    assert files == [f.expanduser()]

    # --root wins over directory arguments
    other = tmp_path / "other"
    other.mkdir()
    root, _ = resolve_root([proj], root=other)
    assert root == other.resolve()

    # no directory -> current directory
    root, files = resolve_root([])
    assert root == Path.cwd().resolve()
    assert files == []

    # unknown paths are rejected
    with pytest.raises(FileNotFoundError):
        resolve_root([tmp_path / "nope"])


def test_language_mapping() -> None:
    assert language_for_path("foo.py") == "python"
    assert language_for_path("foo.rs") == "rust"
    assert language_for_path("foo.ts") == "javascript"
    assert language_for_path("foo.unknownext") is None


async def test_ide_layout_composes(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        tree = app.query_one(Explorer)
        tabs = app.query_one(TabbedContent)
        assert tree is not None
        assert tabs is not None
        # sidebar takes ~20% of the width
        assert 15 <= tree.region.width <= 25
        # one untitled buffer by default
        assert len(list(tabs.query(TabPane))) == 1


async def test_explorer_lists_repo_files(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        tree = app.query_one(Explorer)
        labels = [str(node.label) for node in tree.root.children]
        joined = "\n".join(labels)
        assert "src" in joined
        assert "app.js" in joined
        assert "README.md" in joined
        # dotfiles are hidden
        assert ".hidden" not in joined


async def test_open_file_sets_language(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = await app.open_path(repo / "src" / "hello.py")
        assert area.language == "python"
        assert "def hello" in area.text
        # opening the same file again must reuse the tab
        area2 = await app.open_path(repo / "src" / "hello.py")
        assert area2 is area
        tabs = app.query_one(TabbedContent)
        assert len(list(tabs.query(TabPane))) == 2  # untitled + hello.py


async def test_edit_marks_dirty_then_saves(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        assert area is not None
        buf = app.buffers[area]
        assert not buf.modified

        area.load_text("const a = 2;\n")
        await pilot.pause()
        assert buf.modified

        await app.action_save()
        assert not buf.modified
        assert (repo / "app.js").read_text() == "const a = 2;\n"
