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

        app.action_save()  # now sync: runs the save as a worker
        await pilot.pause()
        assert not buf.modified
        assert (repo / "app.js").read_text() == "const a = 2;\n"


# --------------------------------------------------------------------------- #
# external change tracking
# --------------------------------------------------------------------------- #


async def test_external_edit_of_open_clean_file_shows_diff_then_adopts(
    repo: Path,
) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        buf = app.buffers[area]

        # an agent edits the file outside the editor
        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()

        # the tab immediately shows the inline diff review
        assert buf.external is True
        assert area in app._inline_diff
        assert area.is_read_only  # review mode while hunks are pending
        assert "⌫ const a = 1" in area.text  # removed line: red ghost
        assert "const a = 999" in area.text  # added line: real, green
        rec = app._changes[repo / "app.js"]
        assert rec.status == "modified"
        assert rec.baseline_text == "const a = 1;\n"

        # esc: a clean buffer adopts the new disk content
        await pilot.press("escape")
        await pilot.pause()
        assert area not in app._inline_diff
        assert area.text == "const a = 999;\n"
        assert buf.saved_text == "const a = 999;\n"
        assert buf.external is False


async def test_external_edit_of_dirty_file_shows_diff_then_restores(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        buf = app.buffers[area]

        area.load_text("const a = 'mine';\n")
        await pilot.pause()
        assert buf.modified

        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()

        # diff appears immediately: the agent's line is a ghost, the user's
        # line stays real content
        assert buf.external is True
        assert area in app._inline_diff
        assert area.is_read_only  # review mode while hunks are pending
        assert "⌫ const a = 999" in area.text
        assert "const a = 'mine'" in area.text
        rec = app._changes[repo / "app.js"]
        assert rec.baseline_text == "const a = 1;\n"

        # esc: a dirty buffer keeps the user's unsaved text
        await pilot.press("escape")
        await pilot.pause()
        assert area not in app._inline_diff
        assert area.text == "const a = 'mine';\n"
        assert buf.modified


async def test_new_file_created_outside_is_added_and_revert_deletes_it(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        agent_file = repo / "agent_notes.txt"
        agent_file.write_text("agent wrote this\n")
        app._watch_tick()
        await pilot.pause()

        rec = app._changes[agent_file]
        assert rec.status == "added"
        assert rec.baseline_text is None

        app.revert_path(agent_file)
        await pilot.pause()
        assert not agent_file.exists()
        assert agent_file not in app._changes


async def test_modified_file_revert_restores_original_content(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        target = repo / "src" / "hello.py"
        original = target.read_text()

        target.write_text("def hello():\n    return 'hacked'\n")
        app._watch_tick()
        await pilot.pause()
        assert app._changes[target].status == "modified"

        app.revert_path(target)
        await pilot.pause()
        assert target.read_text() == original
        assert target not in app._changes


async def test_deleted_file_is_tracked_and_revert_restores_it(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        target = repo / "README.md"
        original = target.read_text()

        target.unlink()
        app._watch_tick()
        await pilot.pause()
        rec = app._changes[target]
        assert rec.status == "deleted"
        assert rec.baseline_text == original

        app.revert_path(target)
        await pilot.pause()
        assert target.read_text() == original


async def test_own_save_is_not_flagged_as_external(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        area.load_text("const a = 42;\n")
        await pilot.pause()

        app.action_save()
        await pilot.pause()
        app._watch_tick()
        await pilot.pause()
        assert repo / "app.js" not in app._changes
        assert (repo / "app.js").read_text() == "const a = 42;\n"


async def test_new_file_saved_by_editor_appears_in_tree(repo: Path) -> None:
    from alxedit2.app import Explorer

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        assert area is not None

        # "save as" a brand-new file
        (repo / "fresh.py").write_text("x = 1\n")
        app._note_self_write(repo / "fresh.py", "x = 1\n")
        await pilot.pause()

        tree = app.query_one(Explorer)
        names = {node.data.path.name for node in tree.root.children}
        assert "fresh.py" in names


async def test_inline_diff_in_editor_and_esc_restores(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area

        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()

        # the diff appears automatically, no key press needed
        assert area in app._inline_diff
        assert area.is_read_only  # review mode while hunks are pending
        assert "⌫ const a = 1" in area.text
        assert "const a = 999" in area.text

        # ctrl+d also exits (esc is used by some widgets)
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert area not in app._inline_diff
        assert not area.is_read_only
        assert area.text == "const a = 999;\n"  # clean buffer adopted disk

        # adopting the change approves it — nothing left to diff
        assert repo / "app.js" not in app._changes
        app.action_toggle_diff()
        await pilot.pause()
        assert area not in app._inline_diff


async def test_save_from_inline_diff_strips_ghost_lines(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        buf = app.buffers[area]
        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()

        # the diff appears automatically on the active tab
        assert area in app._inline_diff
        assert "⌫ const a = 1" in area.text

        # saving from the diff view writes the real content only — the
        # ghost line (old content) must not be written back
        app.action_save()
        await pilot.pause()
        assert (repo / "app.js").read_text() == "const a = 999;\n"
        assert area not in app._inline_diff
        assert area.text == "const a = 999;\n"
        assert buf.modified is False
        assert repo / "app.js" not in app._changes


async def test_hunk_theirs_mine_clean_buffer(repo: Path) -> None:
    (repo / "app.js").write_text("one\ntwo\nthree\n")
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        (repo / "app.js").write_text("one\nTWO\nthree\nfour\n")
        app._watch_tick()
        await pilot.pause()

        state = app._inline_diff[area]
        assert len(state.hunks) == 2  # replace(two→TWO) and insert(four)
        bar = app.query_one("#hunkbar")
        assert bar.display

        # hunk 1: take theirs (the agent's "TWO" line)
        app._on_hunk_button("hunk-0-theirs")
        await pilot.pause()
        # hunk 2: keep mine (drop the agent's "four" line)
        app._on_hunk_button("hunk-1-mine")
        await pilot.pause()

        # all decided: review ends, the tab is editable, content resolved
        assert area not in app._inline_diff
        assert not area.is_read_only
        assert not bar.display
        assert area.text == "one\nTWO\nthree\n"

        app.action_save()
        await pilot.pause()
        assert (repo / "app.js").read_text() == "one\nTWO\nthree\n"


async def test_hunk_mine_keeps_dirty_edits(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        area.load_text("const a = 'mine';\n")
        await pilot.pause()

        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()
        assert len(app._inline_diff[area].hunks) == 1

        # keep my line (reject the agent's)
        app._on_hunk_button("hunk-0-mine")
        await pilot.pause()
        assert area not in app._inline_diff
        assert not area.is_read_only
        assert area.text == "const a = 'mine';\n"

        app.action_save()
        await pilot.pause()
        assert (repo / "app.js").read_text() == "const a = 'mine';\n"


async def test_hunk_theirs_takes_agent_line_on_dirty_buffer(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        area.load_text("const a = 'mine';\n")
        await pilot.pause()

        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()

        # take the agent's line instead
        app._on_hunk_button("hunk-0-theirs")
        await pilot.pause()
        assert area not in app._inline_diff
        assert area.text == "const a = 999;\n"

        app.action_save()
        await pilot.pause()
        assert (repo / "app.js").read_text() == "const a = 999;\n"


async def test_save_from_inline_diff_dirty_writes_user_text(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        buf = app.buffers[area]
        area.load_text("const a = 'mine';\n")
        await pilot.pause()
        assert buf.modified

        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()
        assert area in app._inline_diff

        # saving keeps the user's content, drops the agent's ghost line
        app.action_save()
        await pilot.pause()
        assert (repo / "app.js").read_text() == "const a = 'mine';\n"
        assert area not in app._inline_diff
        assert area.text == "const a = 'mine';\n"
        assert buf.modified is False


async def test_changes_screen_lists_tracked_changes(repo: Path) -> None:
    from textual.widgets import ListItem, ListView

    from alxedit2.app import ChangesScreen

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        (repo / "agent.txt").write_text("hi\n")
        (repo / "app.js").write_text("const a = 5;\n")
        app._watch_tick()
        await pilot.pause()

        await app.push_screen(ChangesScreen())
        await pilot.pause()
        lv = app.screen.query_one("#changes-list", ListView)
        assert len(list(lv.query(ListItem))) == 2
