"""Smoke tests for the alxedit2 IDE layout, tabs, and saving."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from textual.widgets import (
    Button,
    Input,
    ListView,
    TabPane,
    TabbedContent,
    TextArea,
)
from textual.widgets._tabbed_content import ContentTabs

from alxedit2 import sessions
from alxedit2 import settings as project_settings
from alxedit2.__main__ import resolve_root
from alxedit2.app import AlxEditApp, Explorer, PaneSizer, SettingsScreen, SessionScreen
from alxedit2.languages import language_for_path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A project directory, including an existing session (the normal state
    of a folder the user has opened before)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hello.py").write_text("def hello():\n    return 'world'\n")
    (tmp_path / "app.js").write_text("const a = 1;\n")
    (tmp_path / ".hidden").write_text("secret\n")
    (tmp_path / ".alxeditrc").write_text("# no extra rules in the test fixture\n")
    (tmp_path / "README.md").write_text("# hi\n")
    sid = sessions.create_session(tmp_path)
    for f in sessions.iter_tracked_files(tmp_path, project_settings.load(tmp_path)):
        sessions.copy_to_mirror(tmp_path, sid, f)
    return tmp_path


def _new_session(root: Path) -> str:
    """Create a session mirroring the current tree (what the UI's 'New'
    button does)."""
    sid = sessions.create_session(root)
    for f in sessions.iter_tracked_files(root, project_settings.load(root)):
        sessions.copy_to_mirror(root, sid, f)
    return sid


def _mouse_move(app, x: float, y: float) -> None:
    from textual import events

    app.post_message(
        events.MouseMove(
            None, x, y, 0, 0, 1, False, False, False,
            screen_x=x, screen_y=y,
        )
    )


def _mouse_up(app, x: float, y: float) -> None:
    from textual import events

    app.post_message(
        events.MouseUp(
            None, x, y, 0, 0, 1, False, False, False,
            screen_x=x, screen_y=y,
        )
    )


async def test_sizers_flank_their_panes(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar")
        tabs = app.query_one("#tabs")
        sidebar_sizer = app.query_one("#sidebar-sizer", PaneSizer)
        assert sidebar_sizer.pane_id == "sidebar"
        assert sidebar_sizer.side == "left"
        assert sidebar.region.x < sidebar_sizer.region.x < tabs.region.x
        assert sidebar_sizer.region.width == 1
        # the hunk panel (and its sizer) are hidden until a review opens
        hunk_sizer = app.query_one("#hunk-sizer", PaneSizer)
        hunkbar = app.query_one("#hunkbar")
        assert hunk_sizer.pane_id == "hunkbar"
        assert hunk_sizer.side == "right"
        assert not hunk_sizer.display
        assert not hunkbar.display


async def test_sidebar_resizes_with_hotkeys(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        before = app.pane_value("sidebar")
        await pilot.press("alt+right")
        await pilot.pause()
        assert app.pane_value("sidebar") == before + 4
        await pilot.press("alt+left")
        await pilot.pause()
        assert app.pane_value("sidebar") == before


async def test_pane_widths_clamped(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.set_pane_value("sidebar", 1)
        assert app.pane_value("sidebar") == 10.0
        app.set_pane_value("sidebar", 99)
        assert app.pane_value("sidebar") == 60.0
        app.set_pane_value("hunkbar", 1)
        assert app.pane_value("hunkbar") == 15.0
        app.set_pane_value("hunkbar", 99)
        assert app.pane_value("hunkbar") == 60.0


async def test_dragging_sizer_resizes_sidebar(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        sizer = app.query_one("#sidebar-sizer", PaneSizer)
        x0 = sizer.region.x
        before = app.pane_value("sidebar")
        await pilot.mouse_down(sizer)
        await pilot.pause()
        assert app._resizing_pane == "sidebar"
        # drag 10 columns to the right -> sidebar widens
        _mouse_move(app, x0 + 10, 5)
        await pilot.pause()
        assert app.pane_value("sidebar") > before
        _mouse_up(app, x0 + 10, 5)
        await pilot.pause()
        assert app._resizing_pane is None
        assert "resizing" not in sizer.classes


async def test_dragging_hunk_sizer_widens_hunkbar(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app._show_hunkbar(True)
        await pilot.pause()
        sizer = app.query_one("#hunk-sizer", PaneSizer)
        hunkbar = app.query_one("#hunkbar")
        # sizer sits immediately left of the hunk panel
        assert sizer.region.x + 1 == hunkbar.region.x
        before = app.pane_value("hunkbar")
        x0 = sizer.region.x
        await pilot.mouse_down(sizer)
        await pilot.pause()
        assert app._resizing_pane == "hunkbar"
        # drag 8 columns to the LEFT -> right-hand pane widens
        _mouse_move(app, x0 - 8, 5)
        await pilot.pause()
        assert app.pane_value("hunkbar") == before + 8
        _mouse_up(app, x0 - 8, 5)
        await pilot.pause()
        assert app._resizing_pane is None


async def test_hunkbar_resizes_with_hotkeys(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        before = app.pane_value("hunkbar")
        await pilot.press("alt+shift+left")
        await pilot.pause()
        assert app.pane_value("hunkbar") == before + 4
        await pilot.press("alt+shift+right")
        await pilot.pause()
        assert app.pane_value("hunkbar") == before


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
        # no buffer by default — the tab area starts empty
        assert len(list(tabs.query(TabPane))) == 0


async def test_starts_empty_until_a_buffer_is_requested(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.active_area is None  # no untitled tab on startup

        # ctrl+n creates the first buffer
        await pilot.press("ctrl+n")
        await pilot.pause()
        area = app.active_area
        assert area is not None
        assert app._tabbed.active_pane is not None


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
        # dotfiles are shown, including the project settings file ...
        assert ".hidden" in joined
        assert ".alxeditrc" in joined
        # ... and the session store itself (the diff baseline, for
        # inspection) is part of the tree as well
        assert ".alxedit" in joined


async def test_explorer_annotates_tracked_and_untracked(repo: Path) -> None:
    """Entries show whether the change tracker covers them: ● tracked,
    ○ untracked; folders reflect their contents."""
    (repo / ".github").mkdir()
    (repo / ".github" / "ci.yml").write_text("x")
    (repo / "assets").mkdir()
    (repo / "assets" / "big.png").write_text("x")

    def line_for(app: AlxEditApp, name: str) -> str:
        """The rendered tree line whose label is *name* (trailing tracking
        glyph and change marker stripped before matching; the 📄/📁 icon
        and tree guides are ignored), or '' if not visible."""
        for strip in app.screen._compositor.render_strips(app.screen.size):
            t = strip.text.rstrip()
            label = t
            # trailing "+N/-M" change marker, if present
            if "/-" in label:
                i = label.rfind(" +")
                if i != -1:
                    label = label[:i].rstrip()
            # trailing tracking glyph ("T" or "○"), if present
            if label.endswith(" T") or label.endswith(" ○"):
                label = label[:-2].rstrip()
            if label.rstrip().endswith(name):
                return t
        return ""

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(200, 30)) as pilot:
        await pilot.pause()
        tree = app.query_one(Explorer)
        await _wait_for(pilot, lambda: bool(tree.root.children))
        for _ in range(10):
            await pilot.pause()

        assert " T" in line_for(app, "app.js")  # tracked
        assert " T" in line_for(app, "src")  # folder holding tracked files
        assert " T" in line_for(app, "assets")  # folder with a tracked file
        assert " ○" in line_for(app, ".hidden")  # untracked dot file
        assert " ○" in line_for(app, ".alxeditrc")  # untracked dot file
        assert " ○" in line_for(app, ".alxedit")  # the session store is never
        assert " ○" in line_for(app, ".github")  # untracked dot folder

    # a 'track' rule flips the glyph
    project_settings.save(repo, project_settings.Settings(track=(".hidden",)))
    app2 = AlxEditApp(root=repo)
    async with app2.run_test(size=(200, 30)) as pilot:
        await pilot.pause()
        tree = app2.query_one(Explorer)
        await _wait_for(pilot, lambda: bool(tree.root.children))
        for _ in range(10):
            await pilot.pause()
        assert " T" in line_for(app2, ".hidden")


async def test_ctrl_click_toggle_tracking(repo: Path) -> None:
    """Ctrl+click -> Track/Untrack edits .alxeditrc and flips the glyph:
    untracking adds an ignore rule; tracking a dot file adds a track
    rule; the menu offers the opposite action on the next open."""

    async def find_node(ex: Explorer, name: str):
        for _ in range(40):
            node = next(
                (c for c in ex.root.children if c.data and c.data.path.name == name),
                None,
            )
            if node is not None:
                return node
            await pilot.pause()
        return None

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(200, 30)) as pilot:
        ex = app.query_one(Explorer)
        await _wait_for(pilot, lambda: bool(ex.root.children))
        for _ in range(10):
            await pilot.pause()

        def rc() -> str:
            return (repo / ".alxeditrc").read_text()

        def line_for(name: str) -> str:
            for strip in app.screen._compositor.render_strips(app.screen.size):
                t = strip.text.rstrip()
                label = t
                if "/-" in label:
                    i = label.rfind(" +")
                    if i != -1:
                        label = label[:i].rstrip()
                if label.endswith(" T") or label.endswith(" ○"):
                    label = label[:-2].rstrip()
                if label.rstrip().endswith(name):
                    return t
            return ""

        # 1. app.js is tracked by default -> the menu offers 'Untrack'
        node = await find_node(ex, "app.js")
        await _ctrl_click_node(pilot, app, ex, node)
        assert app.screen.__class__.__name__ == "NodeMenuScreen"
        app.screen.query_one("#menu-untrack", Button).press()
        await pilot.pause()
        for _ in range(10):
            await pilot.pause()
        assert "ignore app.js" in rc()
        assert not app._is_tracked_path(repo / "app.js")
        assert " ○" in line_for("app.js")

        # 2. ... and the menu now offers 'Track' again
        await _ctrl_click_node(pilot, app, ex, node)
        assert app.screen.__class__.__name__ == "NodeMenuScreen"
        app.screen.query_one("#menu-track", Button).press()
        await pilot.pause()
        for _ in range(10):
            await pilot.pause()
        assert "ignore app.js" not in rc()
        assert app._is_tracked_path(repo / "app.js")
        assert " T" in line_for("app.js")

        # 3. a dot file is untracked by default -> 'Track .hidden' adds
        #    a track rule
        hnode = await find_node(ex, ".hidden")
        await _ctrl_click_node(pilot, app, ex, hnode)
        assert app.screen.__class__.__name__ == "NodeMenuScreen"
        app.screen.query_one("#menu-track", Button).press()
        await pilot.pause()
        for _ in range(10):
            await pilot.pause()
        assert "track .hidden" in rc()
        assert app._is_tracked_path(repo / ".hidden")
        assert " T" in line_for(".hidden")

        # 4. ... and now 'Untrack' works on it too (ignore wins)
        await _ctrl_click_node(pilot, app, ex, hnode)
        assert app.screen.__class__.__name__ == "NodeMenuScreen"
        app.screen.query_one("#menu-untrack", Button).press()
        await pilot.pause()
        for _ in range(10):
            await pilot.pause()
        assert "ignore .hidden" in rc()
        assert not app._is_tracked_path(repo / ".hidden")
        assert " ○" in line_for(".hidden")

        # 5. a folder: Untrack 'src' adds 'ignore src' and flips the
        #    folder glyph (and everything below it)
        snode = await find_node(ex, "src")
        await _ctrl_click_node(pilot, app, ex, snode)
        assert app.screen.__class__.__name__ == "NodeMenuScreen"
        app.screen.query_one("#menu-untrack", Button).press()
        await pilot.pause()
        for _ in range(10):
            await pilot.pause()
        assert "ignore src" in rc()
        assert not app._is_tracked_path(repo / "src" / "hello.py")
        assert " ○" in line_for("src")

        # 6. ... and Track brings the folder (and its files) back
        await _ctrl_click_node(pilot, app, ex, snode)
        assert app.screen.__class__.__name__ == "NodeMenuScreen"
        app.screen.query_one("#menu-track", Button).press()
        await pilot.pause()
        for _ in range(10):
            await pilot.pause()
        assert "ignore src" not in rc()
        assert app._is_tracked_path(repo / "src" / "hello.py")
        assert " T" in line_for("src")


async def test_ctrl_click_menu_on_session_store_is_readonly(repo: Path) -> None:
    """Ctrl+click an entry inside .alxedit offers no file operations —
    the baseline copy must not be renamed, deleted, tracked, or created
    over; only a read-only notice and a way to close."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        ex = app.query_one(Explorer)
        await _wait_for(pilot, lambda: bool(ex.root.children))
        for _ in range(10):
            await pilot.pause()
        node = next(
            c
            for c in ex.root.children
            if c.data is not None and c.data.path.name == ".alxedit"
        )
        await _ctrl_click_node(pilot, app, ex, node)
        assert app.screen.__class__.__name__ == "NodeMenuScreen"
        buttons = {b.id for b in app.screen.query(Button)}
        assert buttons == {"menu-cancel"}
        app.screen.query_one("#menu-cancel", Button).press()
        await pilot.pause()


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
        assert len(list(tabs.query(TabPane))) == 1  # just hello.py


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


async def test_opening_changed_file_goes_straight_to_diff(repo: Path) -> None:
    from alxedit2.app import HunkBar

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # the agent edits a file that is not open
        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()
        assert (repo / "app.js") in app._changes

        # opening it now goes directly into the diff review
        area = await app.open_path(repo / "app.js")
        await pilot.pause()
        assert area in app._inline_diff
        assert app.query_one(HunkBar).display
        # the view holds the agent's content (green) + the session baseline
        assert "999" in area.text


async def test_resolve_theirs_after_open_commits_to_baseline(repo: Path) -> None:
    """Agent edits a closed file; user opens it and resolves the hunk to
    'theirs'. Approving is a commit: the resolved content becomes the session
    baseline (mirror) immediately — the dot clears, the change settles out of
    the pending list, and the async TextArea.Changed that follows must not
    re-dirty the buffer.
    """
    (repo / "app.js").write_text("const a = 1;\n")
    app = AlxEditApp(root=repo)  # app.js NOT pre-opened
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        # Agent edits while the file is closed.
        (repo / "app.js").write_text("const a = 42;\n")
        app._watch_tick()
        await pilot.pause()
        # Opening it goes straight to the inline diff.
        area = await app.open_path(repo / "app.js")
        await pilot.pause()
        buf = app.buffers[area]
        assert area in app._inline_diff
        assert buf.saved_text == "const a = 42;\n"
        state = app._inline_diff[area]
        assert len(state.hunks) >= 1
        # Resolve every hunk to 'theirs' (accept the agent's text).
        for i in range(len(state.hunks)):
            app._on_hunk_button(f"hunk-{i}-theirs")
            await pilot.pause()
        # Approving is a commit: dot clears, change settles, baseline updated.
        assert area not in app._inline_diff
        assert buf.modified is False
        assert (repo / "app.js") not in app._changes
        assert app._baseline_text(repo / "app.js") == "const a = 42;\n"
        from textual.widgets._tabbed_content import ContentTabs

        pane = app._panes[area]
        tabs = app._tabbed.get_child_by_type(ContentTabs)
        assert "●" not in str(tabs.get_content_tab(pane.id).label)


async def test_resolving_all_hunks_settles_pending_change(repo: Path) -> None:
    """Accepting every hunk of an external change settles it out of the
    pending list (like F2 Approve). Left there it would linger, and re-opening
    the diff for it would show a blank view (buffer already equals the disk
    side it was diffed against).
    """
    (repo / "app.js").write_text("const a = 1;\n")
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        (repo / "app.js").write_text("const a = 42;\n")  # agent edits
        app._watch_tick()
        await pilot.pause()
        assert (repo / "app.js") in app._changes

        area = await app.open_path(repo / "app.js")
        await pilot.pause()
        state = app._inline_diff[area]
        assert len(state.hunks) >= 1
        for i in range(len(state.hunks)):
            app._on_hunk_button(f"hunk-{i}-theirs")
            await pilot.pause()

        # Settled: no longer a pending external change; the resolved content
        # is now the session baseline, so the dot clears.
        assert (repo / "app.js") not in app._changes
        assert app.buffers[area].modified is False
        assert app._baseline_text(repo / "app.js") == "const a = 42;\n"
        # ...and it does not reappear on the next watcher pass (the disk has
        # not changed since the change was first detected).
        app._watch_tick()
        await pilot.pause()
        assert (repo / "app.js") not in app._changes
        # Re-requesting the diff no longer drops into a (blank) inline view.
        await app.show_inline_diff(repo / "app.js")
        await pilot.pause()
        assert area not in app._inline_diff


# --------------------------------------------------------------------------- #
# sessions (.alxedit/<sid>/ mirror = diff baseline)
# --------------------------------------------------------------------------- #


def test_sessions_module_create_list_delete(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\n")
    (tmp_path / ".secret").write_text("hidden\n")

    s1 = sessions.create_session(tmp_path, "first")
    s2 = sessions.create_session(tmp_path, "second")
    for f in sessions.iter_tracked_files(tmp_path):
        sessions.copy_to_mirror(tmp_path, s1, f)
        sessions.copy_to_mirror(tmp_path, s2, f)

    found = sessions.list_sessions(tmp_path)
    assert [s.id for s in found] == [s2, s1]  # newest first
    assert found[1].label == "first"
    assert found[1].file_count == 1
    assert sessions.read_mirror_text(tmp_path, s1, tmp_path / "a.txt") == "one\n"

    # the session dir refuses to mirror itself
    with pytest.raises(ValueError):
        sessions.mirror_path(tmp_path, s1, tmp_path / ".alxedit" / s1 / "x")

    sessions.delete_session(tmp_path, s1)
    assert [s.id for s in sessions.list_sessions(tmp_path)] == [s2]


async def test_startup_activates_existing_session(repo: Path) -> None:
    """With a .alxedit folder present, startup activates a session."""
    s0 = sessions.list_sessions(repo)[0].id
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.session_id == s0
        sdir = repo / ".alxedit" / s0
        assert (sdir / "session.json").is_file()
        assert (
            sdir / "files" / "src" / "hello.py"
        ).read_text() == "def hello():\n    return 'world'\n"
        assert (sdir / "files" / "app.js").read_text() == "const a = 1;\n"
        # dotfiles are not mirrored
        assert not (sdir / "files" / ".hidden").exists()


async def test_startup_basic_mode_without_alxedit(tmp_path: Path) -> None:
    """No .alxedit folder -> plain editor: no tree copy, no tracked changes."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hello.py").write_text("def hello():\n    return 'world'\n")
    (tmp_path / "app.js").write_text("const a = 1;\n")

    app = AlxEditApp(root=tmp_path, paths=[tmp_path / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.session_id is None
        assert not (tmp_path / ".alxedit").exists()  # nothing was copied
        assert app.active_area is not None  # the file still opened fine
        assert app._changes == {}

    # a session can still be started later via the Session button
    app2 = AlxEditApp(root=tmp_path)
    async with app2.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app2.action_sessions()
        await pilot.pause()
        assert isinstance(app2.screen, SessionScreen)
        app2.screen.query_one("#sess-new", Button).press()
        await pilot.pause()
        assert app2.session_id is not None
        mirror = tmp_path / ".alxedit" / app2.session_id / "files" / "app.js"
        assert mirror.read_text() == "const a = 1;\n"


async def test_save_updates_session_mirror(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        area.load_text("const a = 42;\n")
        app.action_save()
        await pilot.pause()
        mirror = repo / ".alxedit" / app.session_id / "files" / "app.js"
        assert mirror.read_text() == "const a = 42;\n"


def _select_session(screen: SessionScreen, sid: str) -> None:
    """Point the picker's cursor at the session with id *sid*."""
    lv = screen.query_one("#session-list", ListView)
    lv.index = [s.id for s in screen._sessions].index(sid)


async def test_picker_opens_existing_session(repo: Path) -> None:
    # start from a clean slate: drop the fixture's session
    shutil.rmtree(repo / ".alxedit")
    s1 = _new_session(repo)
    s2 = _new_session(repo)

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # the picker comes up on top with both sessions
        assert isinstance(app.screen, SessionScreen)
        assert len(app.screen._sessions) == 2
        _select_session(app.screen, s1)
        app.screen.query_one("#sess-open", Button).press()
        await pilot.pause()
        assert app.session_id == s1


async def test_picker_new_session_and_delete(repo: Path) -> None:
    # start from a clean slate: drop the fixture's session
    shutil.rmtree(repo / ".alxedit")
    s1 = _new_session(repo)
    s2 = _new_session(repo)
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SessionScreen)
        screen = app.screen

        # delete s1: navigate to it, then press Delete twice to confirm
        _select_session(screen, s1)
        screen.query_one("#sess-delete", Button).press()
        screen.query_one("#sess-delete", Button).press()
        await pilot.pause()
        assert not (repo / ".alxedit" / s1).exists()
        assert (repo / ".alxedit" / s2).is_dir()

        # create a new session from the picker
        screen.query_one("#sess-new", Button).press()
        await pilot.pause()
        assert app.session_id not in (s1, s2)
        sdir = repo / ".alxedit" / app.session_id
        assert (sdir / "files" / "app.js").read_text() == "const a = 1;\n"


async def test_session_switch_changes_baseline(repo: Path) -> None:
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        s1 = app.session_id

        # agent edits outside
        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()
        rec = app._changes[repo / "app.js"]
        assert rec.baseline_text == "const a = 1;\n"  # session-1 baseline

        # adopt it (esc), so the tree holds 999
        await pilot.press("escape")
        await pilot.pause()
        assert (repo / "app.js").read_text() == "const a = 999;\n"

        # new session: baseline is now the 999 content
        app.action_sessions()
        await pilot.pause()
        assert isinstance(app.screen, SessionScreen)
        app.screen.query_one("#sess-new", Button).press()
        await pilot.pause()
        s2 = app.session_id
        assert s2 != s1
        assert (repo / ".alxedit" / s2 / "files" / "app.js").read_text() == (
            "const a = 999;\n"
        )
        assert app._changes == {}

        # agent edits again: the diff is against session 2's baseline
        (repo / "app.js").write_text("const a = 1000;\n")
        app._watch_tick()
        await pilot.pause()
        assert app._changes[repo / "app.js"].baseline_text == "const a = 999;\n"


async def test_cannot_delete_active_session(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        active = app.session_id

        app.action_sessions()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SessionScreen)
        screen.query_one("#sess-delete", Button).press()
        screen.query_one("#sess-delete", Button).press()
        await pilot.pause()
        assert (repo / ".alxedit" / active).is_dir()
        screen.query_one("#sess-cancel", Button).press()
        await pilot.pause()
        assert app.session_id == active


async def test_explorer_refreshes_on_session_create_and_delete(repo: Path) -> None:
    """The explorer picks up .alxedit/<sid> when a session is created and
    drops it when one is deleted — no manual tree refresh needed."""

    async def store_children(ex):
        """The loaded child names of the .alxedit folder (expanding it)."""
        node = next(
            (
                c
                for c in ex.root.children
                if c.data is not None and c.data.path.name == ".alxedit"
            ),
            None,
        )
        if node is None:
            return set()
        if not node.children:
            node.expand()
            if not await _wait_for(pilot, lambda: bool(node.children), tries=20):
                return set()
        return {c.data.path.name for c in node.children if c.data is not None}

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        s0 = app.session_id
        assert s0 is not None  # the fixture session auto-activates
        ex = app.query_one(Explorer)
        await _wait_for(pilot, lambda: bool(ex.root.children))

        # 1. create a session from the Session screen: the explorer picks
        #    up .alxedit/<s1> on its own
        app.action_sessions()
        await pilot.pause()
        assert isinstance(app.screen, SessionScreen)
        app.screen.query_one("#sess-new", Button).press()
        await _wait_for(
            pilot,
            lambda: app.session_id is not None and app.session_id != s0,
            tries=60,
        )
        s1 = app.session_id
        assert s0 in await store_children(ex)
        assert s1 in await store_children(ex)

        # 2. delete s0 (not the active one): it leaves the explorer too
        app.action_sessions()
        await pilot.pause()
        assert isinstance(app.screen, SessionScreen)
        _select_session(app.screen, s0)
        app.screen.query_one("#sess-delete", Button).press()
        app.screen.query_one("#sess-delete", Button).press()
        await pilot.pause()
        assert not (repo / ".alxedit" / s0).exists()
        app.screen.query_one("#sess-cancel", Button).press()
        await pilot.pause()
        kids = await store_children(ex)
        assert s1 in kids
        assert s0 not in kids


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
        # one-line change: a modified pair, both sides marked M
        assert "M const a = 1" in area.text  # old line: yellow, struck
        assert "M const a = 999" in area.text  # new line: yellow
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
        assert "M const a = 999" in area.text
        assert "M const a = 'mine'" in area.text
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


async def test_approve_new_file_adopts_it(repo: Path) -> None:
    """Approve an 'added' file: it stays on disk and the mirror adopts it."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        agent_file = repo / "agent_notes.txt"
        agent_file.write_text("agent wrote this\n")
        app._watch_tick()
        await pilot.pause()
        assert app._changes[agent_file].status == "added"

        app.approve_path(agent_file)
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()

        assert agent_file.exists()
        assert agent_file not in app._changes
        # Approve marks the change as pending; the baseline (mirror) is
        # only updated on save.
        assert not sessions.mirror_exists(repo, app.session_id, agent_file)

        # Saving commits the change to the baseline.
        area = await app.open_path(agent_file)
        await pilot.pause()
        buf = app.buffers[area]
        assert buf.modified  # dot: buffer differs from baseline
        app.action_save()
        await pilot.pause()
        assert sessions.mirror_exists(repo, app.session_id, agent_file)
        assert (
            sessions.read_mirror_text(repo, app.session_id, agent_file)
            == "agent wrote this\n"
        )
        assert not buf.modified  # dot cleared after save


async def test_approve_deleted_file_drops_mirror(repo: Path) -> None:
    """Approve a 'deleted' file: it stays gone and the mirror copy is dropped."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        target = repo / "README.md"
        target.unlink()
        app._watch_tick()
        await pilot.pause()
        assert app._changes[target].status == "deleted"

        app.approve_path(target)
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()

        assert not target.exists()
        assert target not in app._changes
        assert not sessions.mirror_exists(repo, app.session_id, target)


async def test_approve_modified_file_marks_pending(repo: Path) -> None:
    """Approve a 'modified' file: buffer updated, dot shown, baseline unchanged until save."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        target = repo / "app.js"
        original = target.read_text()
        target.write_text("const a = 42;\n")
        app._watch_tick()
        await pilot.pause()
        assert app._changes[target].status == "modified"

        app.approve_path(target)
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()

        assert target.read_text() == "const a = 42;\n"
        assert target not in app._changes
        # Approve does NOT update the mirror — it stays at the original.
        assert sessions.read_mirror_text(repo, app.session_id, target) == original

        # Open the file: the dot shows because buffer != baseline.
        area = await app.open_path(target)
        await pilot.pause()
        buf = app.buffers[area]
        assert area.text == "const a = 42;\n"
        assert buf.modified  # pending: differs from baseline

        # Save commits: mirror updated, dot cleared.
        app.action_save()
        await pilot.pause()
        assert (
            sessions.read_mirror_text(repo, app.session_id, target)
            == "const a = 42;\n"
        )
        assert not buf.modified


async def test_reject_modified_file_restores_baseline(repo: Path) -> None:
    """Reject a 'modified' file (via the confirmed Reject path): restored."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        target = repo / "app.js"
        original = target.read_text()
        target.write_text("const a = 42;\n")
        app._watch_tick()
        await pilot.pause()
        assert app._changes[target].status == "modified"

        app.reject_path(target)
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()

        assert target.read_text() == original
        assert target not in app._changes


async def test_reject_cancel_keeps_change(repo: Path) -> None:
    """Declining the Reject confirm leaves the change tracked and on disk."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        target = repo / "app.js"
        target.write_text("const a = 42;\n")
        app._watch_tick()
        await pilot.pause()
        assert app._changes[target].status == "modified"

        app.reject_path(target)
        await pilot.pause()
        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()

        assert target.read_text() == "const a = 42;\n"
        assert target in app._changes


async def test_approve_all(repo: Path) -> None:
    """Approve all: every tracked change is adopted at once."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        new_file = repo / "new.txt"
        new_file.write_text("one\n")
        (repo / "app.js").write_text("const a = 42;\n")
        (repo / "README.md").unlink()
        app._watch_tick()
        await pilot.pause()
        assert len(app._changes) == 3

        app.approve_all()
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()

        assert app._changes == {}
        assert new_file.exists()
        assert (repo / "app.js").read_text() == "const a = 42;\n"
        assert not (repo / "README.md").exists()
        assert not sessions.mirror_exists(repo, app.session_id, repo / "README.md")


async def test_reject_all(repo: Path) -> None:
    """Reject all: additions removed, edits + deletions restored."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        new_file = repo / "new.txt"
        new_file.write_text("one\n")
        appjs = repo / "app.js"
        original_js = appjs.read_text()
        readme = repo / "README.md"
        original_readme = readme.read_text()
        appjs.write_text("const a = 42;\n")
        readme.unlink()
        app._watch_tick()
        await pilot.pause()
        assert len(app._changes) == 3

        app.reject_all()
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()

        assert app._changes == {}
        assert not new_file.exists()
        assert appjs.read_text() == original_js
        assert readme.read_text() == original_readme


async def test_changes_screen_shows_approve_reject_buttons(repo: Path) -> None:
    """The F2 window exposes Approve / Reject (and the all-variants)."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        (repo / "agent.txt").write_text("hi\n")
        app._watch_tick()
        await pilot.pause()

        app.action_changes()
        await pilot.pause()
        screen = app.screen
        for button_id in ("btn-approve", "btn-reject", "btn-approve-all", "btn-reject-all"):
            assert screen.query_one(f"#{button_id}", Button)


async def test_changes_button_highlights_while_pending(repo: Path) -> None:
    """The top-bar Changes button is highlighted while the session has
    pending changes, and returns to normal once they are all resolved.
    """
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        btn = app.query_one("#btn-changes", Button)
        assert not btn.has_class("has-changes")

        # An external edit appears -> the button lights up.
        (repo / "app.js").write_text("const a = 42;\n")
        app._watch_tick()
        await pilot.pause()
        assert btn.has_class("has-changes")

        # Resolving the change clears the highlight again.
        app.reject_all()
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()
        assert not btn.has_class("has-changes")


async def test_save_resolving_change_clears_changes_button(repo: Path) -> None:
    """Saving an open file that has a pending external change resolves it, so
    the Changes button must clear (not just the reject/approve paths).
    """
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        btn = app.query_one("#btn-changes", Button)
        assert not btn.has_class("has-changes")

        # Agent edits the already-open file -> pending change, button lights up.
        (repo / "app.js").write_text("const a = 42;\n")
        app._watch_tick()
        await pilot.pause()
        assert btn.has_class("has-changes")

        # Saving adopts the change -> resolved -> button clears again.
        app.action_save()
        await pilot.pause()
        assert app._changes == {}
        assert not btn.has_class("has-changes")
        assert (repo / "app.js").read_text() == "const a = 42;\n"


async def test_explorer_shows_plus_minus_markers(repo: Path) -> None:
    from rich.style import Style

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        target = repo / "app.js"  # "const a = 1;" — one line

        # agent rewrites the one line into three: +3 / -1 (like git diffstat)
        target.write_text("const a = 9;\nconst b = 2;\nconst c = 3;\n")
        app._watch_tick()
        await pilot.pause()

        # marker counts are computed against the session copy
        assert app._tree_markers.get(target) == (3, 1)

        # and they show up in the tree node label
        tree = app.query_one(Explorer)
        node = next(n for n in tree.root.children if n.data.path == target)
        label = tree.render_label(node, Style(), Style())
        assert "+3" in label.plain
        assert "-1" in label.plain

        # a file matching the session copy has no marker
        quiet = next(n for n in tree.root.children if n.data.path == (repo / "README.md"))
        assert "+" not in tree.render_label(quiet, Style(), Style()).plain

        # after revert, the file matches the session copy again -> marker gone
        app.revert_path(target)
        await pilot.pause()
        assert target not in app._tree_markers


async def test_tree_marker_repaints_without_interaction(repo: Path) -> None:
    """The marker appears in the *rendered* tree right after an external edit.

    Regression: Tree caches its rendered lines, so a plain refresh() repainted
    the stale label and the marker only showed up after clicking the panel.
    Uses a depth-2 file so the score must also survive the guide truncation.
    """
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        # wait for the async directory loader
        for _ in range(40):
            await pilot.pause()
            ex = app.query_one(Explorer)
            if ex.root is not None and ex.root.children:
                break
        ex.root.children[0].expand()  # open "src/"
        for _ in range(20):
            await pilot.pause()
            if any(
                c.data and c.data.path.name == "hello.py"
                for c in ex.root.children[0].children
            ):
                break

        def rendered() -> str:
            strips = app.screen._compositor.render_strips(app.screen.size)
            return "\n".join(s.text for s in strips)

        assert "+1/-1" not in rendered()

        (repo / "src/hello.py").write_text("def hello():\n    return 'changed'\n")
        app._watch_tick()
        for _ in range(10):
            await pilot.pause()

        assert "+1/-1" in rendered(), "marker must paint without any click"

        # revert -> the score vanishes from the rendered tree too
        (repo / "src/hello.py").write_text("def hello():\n    return 'world'\n")
        app._watch_tick()
        for _ in range(10):
            await pilot.pause()
        assert "+1/-1" not in rendered()


def _node_at(tree, path: Path):
    """The tree node for *path*, walking from the root (if the trail loaded)."""
    node = tree.root
    base = tree.root.data.path
    rel = path.relative_to(base)
    for i in range(len(rel.parts)):
        target = base.joinpath(*rel.parts[: i + 1])
        node = next(
            (
                c
                for c in node.children
                if c.data is not None and c.data.path == target
            ),
            None,
        )
        if node is None:
            return None
    return node


async def _wait_for(pilot, predicate, tries: int = 40) -> bool:
    for _ in range(tries):
        await pilot.pause()
        if predicate():
            return True
    return predicate()


async def test_change_in_collapsed_folder_expands_it(repo: Path) -> None:
    """A change landing in a collapsed folder re-opens its trail.

    The explorer hides collapsed folders, so a change inside one would be
    invisible (marker and all) unless the trail expands on its own.
    """
    deep = repo / "nested" / "deep"
    deep.mkdir(parents=True)
    (deep / "notes.txt").write_text("line one\n")
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        tree = app.query_one(Explorer)
        assert await _wait_for(pilot, lambda: bool(tree.root.children))
        nested = _node_at(tree, repo / "nested")
        assert nested is not None

        # open the trail once (loads the nodes), then collapse it
        nested.expand()
        assert await _wait_for(
            pilot, lambda: _node_at(tree, repo / "nested" / "deep") is not None
        )
        deep_node = _node_at(tree, repo / "nested" / "deep")
        deep_node.expand()
        assert await _wait_for(
            pilot,
            lambda: _node_at(tree, repo / "nested" / "deep" / "notes.txt")
            is not None,
        )
        nested.collapse()
        deep_node.collapse()
        await pilot.pause()
        assert nested.is_collapsed and deep_node.is_collapsed

        # an untouched collapsed folder stays collapsed
        src = _node_at(tree, repo / "src")
        assert src is not None and src.is_collapsed

        # the agent edits the deep file
        (deep / "notes.txt").write_text("line one\nline two\n")
        app._watch_tick()
        assert await _wait_for(pilot, lambda: nested.is_expanded)

        # the trail re-opened so the changed file (and its marker) is visible
        assert repo / "nested" / "deep" / "notes.txt" in app._changes
        assert nested.is_expanded and deep_node.is_expanded
        assert (
            _node_at(tree, repo / "nested" / "deep" / "notes.txt") is not None
        )
        # and the quiet folder was left alone
        assert src.is_collapsed


async def test_change_in_never_expanded_folder_expands_it(repo: Path) -> None:
    """A change in a folder the user never opened must still be revealed.

    Its children were never lazy-loaded, so the walk has to load each level
    as it goes.
    """
    hidden = repo / "vault" / "secrets"
    hidden.mkdir(parents=True)
    (hidden / "pass.txt").write_text("hunter2\n")
    # fresh baseline: the fixture's session predates this test's setup,
    # so re-mirror the current disk state (a single session -> it
    # activates straight away at startup)
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        tree = app.query_one(Explorer)
        assert await _wait_for(pilot, lambda: bool(tree.root.children))
        vault = _node_at(tree, repo / "vault")
        assert vault is not None
        assert vault.is_collapsed
        # never expanded: the secret folder was never even loaded
        assert _node_at(tree, repo / "vault" / "secrets") is None

        (hidden / "pass.txt").write_text("hunter3\n")
        app._watch_tick()
        assert await _wait_for(
            pilot,
            lambda: _node_at(tree, repo / "vault" / "secrets" / "pass.txt")
            is not None,
        )

        assert repo / "vault" / "secrets" / "pass.txt" in app._changes
        assert vault.is_expanded
        secrets = _node_at(tree, repo / "vault" / "secrets")
        assert secrets is not None and secrets.is_expanded


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
        # no default buffer anymore — create one, like ctrl+n would
        await app.action_new_buffer()
        area = app.active_area
        assert area is not None

        # "save as" a brand-new file
        (repo / "fresh.py").write_text("x = 1\n")
        app._note_self_write(repo / "fresh.py", "x = 1\n")
        await pilot.pause()

        tree = app.query_one(Explorer)
        # DirectoryTree.reload_node is async (loads via a worker): await a
        # stable state instead of racing the in-flight reload.
        await tree.reload_node(tree.root)
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
        assert "M const a = 1" in area.text
        assert "M const a = 999" in area.text

        # ctrl+d also exits (esc is used by some widgets)
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert area not in app._inline_diff
        assert not area.is_read_only
        assert area.text == "const a = 999;\n"  # clean buffer adopted disk

        # abandoning the review is NOT a decision: the change stays
        # pending in the ledger and the session mirror is untouched
        assert repo / "app.js" in app._changes
        assert (
            sessions.read_mirror_text(repo, app.session_id, repo / "app.js")
            == "const a = 1;\n"
        )
        # the adopted text is uncommitted, so the dot rides along
        assert app.buffers[area].modified

        # saving now commits the adopted content to the baseline
        app.action_save()
        await pilot.pause()
        assert (
            sessions.read_mirror_text(repo, app.session_id, repo / "app.js")
            == "const a = 999;\n"
        )
        assert repo / "app.js" not in app._changes
        assert not app.buffers[area].modified

        app.action_toggle_diff()
        await pilot.pause()
        assert area not in app._inline_diff


async def test_diff_view_keeps_syntax_highlighting(repo: Path) -> None:
    """The review view must not kill the file's own syntax highlighting."""
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        assert area.language == "javascript"

        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()
        assert area in app._inline_diff

        # the grammar stays on, and the active theme carries BOTH the
        # token styles and the diff styles (composite "alxdiff" theme)
        assert area.language == "javascript"
        assert area.theme != "css"
        syntax = area._theme.syntax_styles
        assert "keyword" in syntax  # a grammar token style
        assert "diff_modold" in syntax  # a diff line style

        # on the "M const a = 1" line the grammar spans and our diff spans
        # coexist: token colors show through the line background
        spans = area._highlights[0]
        names = {name for _, _, name in spans}
        assert "keyword" in names  # "const", from the JS grammar
        assert "diff_modold" in names  # our background + strike
        assert (0, 1, "diff_mark_mod") in spans  # bold marker, 1 byte

        # exit restores the original language + theme
        await pilot.press("escape")
        await pilot.pause()
        assert area not in app._inline_diff
        assert area.language == "javascript"
        assert area.theme == "css"


async def test_diff_kinds_rendered_distinctly(repo: Path) -> None:
    """+ green = addition, ⌫ red strike = deletion, M yellow = modified."""
    (repo / "kinds.txt").write_text("alpha\nbeta\ngamma\ndelta\nepsilon\n")
    # fresh baseline: the fixture's session predates this test's setup,
    # so re-mirror the current disk state (a single session -> it
    # activates straight away at startup)
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo, paths=[repo / "kinds.txt"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area

        # one external edit producing all three kinds: beta -> BETA
        # (modified pair), delta removed (deletion), zeta added (addition)
        (repo / "kinds.txt").write_text("alpha\nBETA\ngamma\nepsilon\nzeta\n")
        app._watch_tick()
        await pilot.pause()

        assert area in app._inline_diff
        state = app._inline_diff[area]
        assert len(state.hunks) == 3

        lines = area.text.splitlines()
        assert "M beta" in lines  # old line of the modified pair
        assert "M BETA" in lines  # new line of the modified pair
        assert "⌫ delta" in lines  # pure deletion
        assert "+ zeta" in lines  # pure addition

        # the pair is adjacent; the blocks keep file order
        assert (
            lines.index("M beta") + 1 == lines.index("M BETA")
            < lines.index("⌫ delta")
            < lines.index("+ zeta")
        )

        def style_of(line: str) -> str:
            return area._highlights[lines.index(line)][0][2]

        assert style_of("M beta") == "diff_modold"
        assert style_of("M BETA") == "diff_mod"
        assert style_of("⌫ delta") == "diff_del"
        assert style_of("+ zeta") == "diff_add"

        # each diff line also carries a bold marker on its first character
        def mark_of(line: str) -> str:
            return area._highlights[lines.index(line)][1][2]

        assert mark_of("M beta") == "diff_mark_mod"
        assert mark_of("⌫ delta") == "diff_mark_del"  # "⌫" is 3 bytes
        assert mark_of("+ zeta") == "diff_mark_add"


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
        assert "M const a = 1" in area.text

        # saving from the diff view writes the real content only — the
        # old line of the modified pair must not be written back
        app.action_save()
        await pilot.pause()
        assert (repo / "app.js").read_text() == "const a = 999;\n"
        assert area not in app._inline_diff
        assert area.text == "const a = 999;\n"
        assert buf.modified is False
        assert repo / "app.js" not in app._changes


async def test_hunk_theirs_mine_clean_buffer(repo: Path) -> None:
    (repo / "app.js").write_text("one\ntwo\nthree\n")
    # fresh baseline: the fixture's session predates this test's setup,
    # so re-mirror the current disk state (a single session -> it
    # activates straight away at startup)
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
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


async def test_hunk_jump_moves_editor_to_block(repo: Path) -> None:
    """Clicking a hunk's label jumps the editor to that change block."""
    (repo / "app.js").write_text("one\ntwo\nthree\n")
    # fresh baseline: the fixture's session predates this test's setup,
    # so re-mirror the current disk state (a single session -> it
    # activates straight away at startup)
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        (repo / "app.js").write_text("one\nTWO\nthree\nfour\n")
        app._watch_tick()
        await pilot.pause()

        state = app._inline_diff[area]
        assert len(state.hunks) == 2
        # Start line of each hunk in the current (both-sides) view.
        assert app._hunk_start_line(state, 0) == 1  # the "M two" modified line
        assert app._hunk_start_line(state, 1) == 4  # the "four" line

        app._on_hunk_button("hunk-0-jump")
        await pilot.pause()
        assert area.cursor_location == (1, 0)

        app._on_hunk_button("hunk-1-jump")
        await pilot.pause()
        assert area.cursor_location == (4, 0)


async def test_resolved_hunk_leaves_the_list(repo: Path) -> None:
    """A resolved hunk drops off the hunk bar; the remaining hunks' jump
    offsets adapt to the re-rendered view."""
    (repo / "app.js").write_text("one\ntwo\nthree\n")
    # fresh baseline: the fixture's session predates this test's setup,
    # so re-mirror the current disk state (a single session -> it
    # activates straight away at startup)
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        (repo / "app.js").write_text("one\nTWO\nthree\nfour\n")
        app._watch_tick()
        await pilot.pause()

        state = app._inline_diff[area]
        bar = app.query_one("#hunkbar")

        def jump_ids() -> list[str]:
            return [
                b.id for b in bar.query(Button) if b.id and b.id.endswith("-jump")
            ]

        assert jump_ids() == ["hunk-0-jump", "hunk-1-jump"]

        # Resolve hunk 0 (keep the agent's "TWO") -> it leaves the list.
        app._on_hunk_button("hunk-0-theirs")
        await pilot.pause()
        assert jump_ids() == ["hunk-1-jump"]

        # Hunk 1's offset shifted up: the "M two" modified line is gone.
        assert app._hunk_start_line(state, 1) == 3
        app._on_hunk_button("hunk-1-jump")
        await pilot.pause()
        assert area.cursor_location == (3, 0)


async def test_resolving_a_hunk_advances_to_next_pending(repo: Path) -> None:
    """Resolving a hunk auto-advances the editor to the next pending change."""
    NL = chr(10)
    (repo / "app.js").write_text("one" + NL + "two" + NL + "three" + NL)
    # fresh baseline: the fixture's session predates this test's setup,
    # so re-mirror the current disk state (a single session -> it
    # activates straight away at startup)
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        (repo / "app.js").write_text("one" + NL + "TWO" + NL + "three" + NL + "four" + NL)
        app._watch_tick()
        await pilot.pause()

        state = app._inline_diff[area]
        assert len(state.hunks) == 2
        # Resolve hunk 0 -> the editor should land on hunk 1 (next pending below).
        app._on_hunk_button("hunk-0-theirs")
        await pilot.pause()
        assert state.current_hunk == 1
        assert area.cursor_location == (app._hunk_start_line(state, 1), 0)


async def test_current_hunk_indicator_set_on_jump_cleared_on_scroll(repo: Path) -> None:
    """The change most recently jumped to is highlighted; a real scroll —
    not a direct handler call — drops it via the armed watcher."""
    NL = chr(10)
    # Long enough that the diff view overflows a 30-row viewport, so the
    # jump (and the scroll back) really move the viewport.
    base = ["line %03d" % i for i in range(1, 61)]
    (repo / "app.js").write_text(NL.join(base) + NL)
    # fresh baseline: the fixture's session predates this test's setup,
    # so re-mirror the current disk state (a single session -> it
    # activates straight away at startup)
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        edited = base.copy()
        edited[50] = "EDITED LINE 51"
        (repo / "app.js").write_text(NL.join(edited) + NL)
        app._watch_tick()
        await pilot.pause()

        state = app._inline_diff[area]
        assert len(state.hunks) == 1
        # The scroll-clear watcher is armed for this area.
        assert area in app._scroll_watched

        def highlight_names() -> set:
            return {
                name
                for spans in area._highlights.values()
                for (_, _, name) in spans
            }

        # Jumping to the hunk marks it "shown" and paints the _cur styles.
        app._on_hunk_button("hunk-0-jump")
        await pilot.pause()
        assert state.current_hunk == 0
        assert "diff_mod_cur" in highlight_names()
        assert "diff_modold_cur" in highlight_names()
        # The jump really scrolled the viewport — that is what makes the
        # assertion above meaningful: the jump's own scroll fired the
        # watcher, yet the freshly set highlight survived it.
        assert area.scroll_y > 0

        # The user scrolls the editor (public API, immediate) -> the armed
        # watcher drops the "shown" highlight.
        area.scroll_home(animate=False, immediate=True)
        await pilot.pause()
        assert state.current_hunk is None
        assert not any(n.endswith("_cur") for n in highlight_names())


async def test_hunk_all_mine_clean_buffer_no_dot(repo: Path) -> None:
    """Resolve every hunk to 'mine' (original text): buffer matches the
    session baseline (mirror) → no unsaved dot. Saving reverts the disk.
    """
    (repo / "app.js").write_text("one\ntwo\nthree\n")
    # fresh baseline: the fixture's session predates this test's setup,
    # so re-mirror the current disk state (a single session -> it
    # activates straight away at startup)
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        (repo / "app.js").write_text("one\nTWO\nthree\nfour\n")
        app._watch_tick()
        await pilot.pause()

        state = app._inline_diff[area]
        assert len(state.hunks) == 2

        # Resolve both hunks to 'mine', keeping the original lines.
        app._on_hunk_button("hunk-0-mine")
        await pilot.pause()
        app._on_hunk_button("hunk-1-mine")
        await pilot.pause()

        assert area not in app._inline_diff
        assert area.text == "one\ntwo\nthree\n"
        # Buffer matches the session baseline (mirror) → no dot.
        assert app.buffers[area].modified is False
        from textual.widgets._tabbed_content import ContentTabs

        pane = app._panes[area]
        tabs = app._tabbed.get_child_by_type(ContentTabs)
        assert "●" not in str(tabs.get_content_tab(pane.id).label)

        # Saving reverts the disk back to the baseline content.
        app.action_save()
        await pilot.pause()
        assert (repo / "app.js").read_text() == "one\ntwo\nthree\n"
        assert app.buffers[area].modified is False


async def test_hunk_all_theirs_commits_to_baseline(repo: Path) -> None:
    """Resolving every hunk to 'theirs' (accepting the agent's text) commits
    immediately: the resolved content becomes the session baseline (mirror),
    the dot clears, and the change settles out of the pending list."""
    (repo / "app.js").write_text("one\ntwo\nthree\n")
    # fresh baseline: the fixture's session predates this test's setup,
    # so re-mirror the current disk state (a single session -> it
    # activates straight away at startup)
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        (repo / "app.js").write_text("one\nTWO\nthree\nfour\n")
        app._watch_tick()
        await pilot.pause()

        state = app._inline_diff[area]
        assert len(state.hunks) == 2

        # Resolve both hunks to 'theirs', accepting the agent's lines.
        app._on_hunk_button("hunk-0-theirs")
        await pilot.pause()
        app._on_hunk_button("hunk-1-theirs")
        await pilot.pause()

        assert area not in app._inline_diff
        assert area.text == "one\nTWO\nthree\nfour\n"
        # Approving is a commit: dot clears, change settles, baseline updated.
        assert app.buffers[area].modified is False
        assert (repo / "app.js") not in app._changes
        assert app._baseline_text(repo / "app.js") == "one\nTWO\nthree\nfour\n"
        from textual.widgets._tabbed_content import ContentTabs

        pane = app._panes[area]
        tabs = app._tabbed.get_child_by_type(ContentTabs)
        assert "●" not in str(tabs.get_content_tab(pane.id).label)
        # Disk already matches (agent wrote it); a save is now a no-op.
        app.action_save()
        await pilot.pause()
        assert (repo / "app.js").read_text() == "one\nTWO\nthree\nfour\n"
        assert app.buffers[area].modified is False


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


async def test_restart_flags_files_that_differ_from_mirror(repo: Path) -> None:
    """Files edited while alxedit2 was closed are flagged on re-activation:
    markers, F2 list and counter all track "differs from the session copy".
    """
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        sid = app.session_id

        # the agent works while the app is "closed"
        (repo / "app.js").write_text("const a = 2;\nconst b = 3;\n")
        (repo / "brandnew.txt").write_text("one\ntwo\nthree\n")
        (repo / "src" / "hello.py").unlink()

        # re-open the same session (simulates a restart)
        app._activate_session(sid)
        await pilot.pause()

        recs = {p.name: r for p, r in app._changes.items()}
        assert recs["app.js"].status == "modified"
        assert recs["brandnew.txt"].status == "added"
        assert recs["hello.py"].status == "deleted"

        assert app._tree_markers[repo / "app.js"] == (2, 1)
        assert app._tree_markers[repo / "brandnew.txt"] == (3, 0)
        assert app._tree_markers[repo / "src" / "hello.py"] == (0, 2)
        assert repo / "README.md" not in app._tree_markers


async def test_restart_clean_tree_flags_nothing(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app._activate_session(app.session_id)
        await pilot.pause()
        assert app._changes == {}
        assert app._tree_markers == {}


async def test_tab_shows_unsaved_marker(repo: Path) -> None:
    """The tab label carries a ● while the buffer is dirty, gone after save."""
    from textual.widgets._tabbed_content import ContentTabs

    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        tabs = app._tabbed.get_child_by_type(ContentTabs)
        tab = tabs.get_content_tab(app._panes[area].id)
        assert "●" not in str(tab.label)

        await pilot.press("end", "x")
        await pilot.pause()
        assert "●" in str(tab.label)

        app.action_save()
        await pilot.pause()
        assert "●" not in str(tab.label)
        assert (repo / "app.js").read_text().endswith("x\n")


async def test_new_folder_creates_directory(repo: Path) -> None:
    """ctrl+shift+n creates a folder where the cursor is; tree picks it up."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        for _ in range(40):
            await pilot.pause()
            ex = app.query_one(Explorer)
            if ex.root is not None and ex.root.children:
                break
        await pilot.press("ctrl+shift+n")
        await pilot.pause()
        await pilot.press(*list("stuff and such"))
        await pilot.press("enter")
        names = []
        for _ in range(20):
            await pilot.pause()
            names = [c.data.path.name for c in ex.root.children if c.data]
            if "stuff and such" in names:
                break
        assert (repo / "stuff and such").is_dir()
        assert "stuff and such" in names


async def test_delete_folder_removes_directory(repo: Path) -> None:
    """ctrl+shift+x deletes the highlighted folder after confirmation, and
    session-tracked files inside stay flagged as deleted (revertable)."""
    victim = repo / "victim"
    victim.mkdir()
    inner = victim / "inner.txt"
    inner.write_text("one\ntwo\n")
    # track the new folder in the session so its deletion is revertable
    sid = sessions.list_sessions(repo)[0].id
    sessions.copy_to_mirror(repo, sid, inner)

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        for _ in range(40):
            await pilot.pause()
            ex = app.query_one(Explorer)
            if ex.root is not None and any(
                c.data and c.data.path.name == "victim"
                for c in ex.root.children
            ):
                break
        node = next(
            c for c in ex.root.children if c.data.path.name == "victim"
        )
        ex.move_cursor(node)
        await pilot.pause()

        await pilot.press("ctrl+shift+x")
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()

        for _ in range(20):
            await pilot.pause()
            if not victim.exists():
                break
        assert not victim.exists()
        # inner file was session-tracked -> flagged deleted, revertable
        assert inner in app._changes
        assert app._changes[inner].status == "deleted"
        # and the folder is gone from the tree listing
        names = [c.data.path.name for c in ex.root.children if c.data]
        assert "victim" not in names


async def _ctrl_click_node(pilot, app: AlxEditApp, ex: Explorer, node) -> None:
    region = ex._get_label_region(node._line)
    await pilot.click(Explorer, offset=(region.x + 2, region.y), control=True)
    await pilot.pause()


async def test_ctrl_click_file_menu_renames(repo: Path) -> None:
    """Ctrl+click a file -> Rename renames it on disk and in the tree."""
    old = repo / "oldname.txt"
    old.write_text("hello\n")
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        ex = None
        node = None
        for _ in range(40):
            await pilot.pause()
            ex = app.query_one(Explorer)
            node = next(
                (c for c in ex.root.children
                 if c.data and c.data.path.name == "oldname.txt"),
                None,
            )
            if node is not None:
                break
        assert node is not None

        await _ctrl_click_node(pilot, app, ex, node)
        assert app.screen.__class__.__name__ == "NodeMenuScreen"
        app.screen.query_one("#menu-rename", Button).press()
        await pilot.pause()
        await pilot.press(*list("newname.txt"))
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()
            if not old.exists():
                break
        assert not old.exists()
        assert (repo / "newname.txt").exists()
        names = [c.data.path.name for c in ex.root.children if c.data]
        assert "newname.txt" in names and "oldname.txt" not in names


async def test_ctrl_click_file_new_file_here(repo: Path) -> None:
    """Ctrl+click a file -> 'New file here' creates an empty file in that
    directory, shows it in the tree, and opens it."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        ex = None
        node = None
        for _ in range(40):
            await pilot.pause()
            ex = app.query_one(Explorer)
            node = next(
                (c for c in ex.root.children
                 if c.data and c.data.path.name == "app.js"),
                None,
            )
            if node is not None:
                break
        assert node is not None

        await _ctrl_click_node(pilot, app, ex, node)
        app.screen.query_one("#menu-new-file", Button).press()
        await pilot.pause()
        await pilot.press(*list("notes.txt"))
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()
            if (repo / "notes.txt").exists():
                break
        assert (repo / "notes.txt").exists()
        names = [c.data.path.name for c in ex.root.children if c.data]
        assert "notes.txt" in names


async def test_ctrl_click_folder_delete(repo: Path) -> None:
    """Ctrl+click a folder -> Delete (with confirm) removes it; tracked
    files inside stay flagged deleted/revertable."""
    victim = repo / "victim2"
    victim.mkdir()
    inner = victim / "inner.txt"
    inner.write_text("one\ntwo\n")
    # track the new folder in the session so its deletion is revertable
    sid = sessions.list_sessions(repo)[0].id
    sessions.copy_to_mirror(repo, sid, inner)
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        ex = None
        node = None
        for _ in range(40):
            await pilot.pause()
            ex = app.query_one(Explorer)
            node = next(
                (c for c in ex.root.children
                 if c.data and c.data.path.name == "victim2"),
                None,
            )
            if node is not None:
                break
        assert node is not None

        await _ctrl_click_node(pilot, app, ex, node)
        app.screen.query_one("#menu-delete", Button).press()
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        for _ in range(20):
            await pilot.pause()
            if not victim.exists():
                break
        assert not victim.exists()
        assert inner in app._changes
        assert app._changes[inner].status == "deleted"
        names = [c.data.path.name for c in ex.root.children if c.data]
        assert "victim2" not in names


async def test_ctrl_click_folder_new_folder_here(repo: Path) -> None:
    """Ctrl+click a folder -> 'New folder here' creates it inside."""
    outer = repo / "outer"
    outer.mkdir()
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        ex = None
        node = None
        for _ in range(40):
            await pilot.pause()
            ex = app.query_one(Explorer)
            node = next(
                (c for c in ex.root.children
                 if c.data and c.data.path.name == "outer"),
                None,
            )
            if node is not None:
                break
        assert node is not None

        await _ctrl_click_node(pilot, app, ex, node)
        app.screen.query_one("#menu-new-folder", Button).press()
        await pilot.pause()
        await pilot.press(*list("inner"))
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()
            if (outer / "inner").is_dir():
                break
        assert (outer / "inner").is_dir()


async def test_help_mentions_unsaved_dot_after_resolution(repo: Path) -> None:
    """F1 help explains the unsaved dot shown once a review is resolved."""
    from textual.widgets import Static

    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("f1")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "HelpScreen"
        joined = "\n".join(str(s.content) for s in screen.query(Static))
        assert "all decided → editable again" in joined
        assert "● tab = change not in baseline yet" in joined
        assert "ctrl+s commits it" in joined


# --------------------------------------------------------------------------- #
# settings (.alxeditrc): what the session mirror tracks
# --------------------------------------------------------------------------- #


async def test_topbar_settings_button_opens_screen(repo: Path) -> None:
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.query_one("#btn-settings", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)


async def test_dotfile_changes_not_tracked_by_default(repo: Path) -> None:
    """Dot files are visible in the explorer but external edits to them
    are not flagged (the mirror never contains them)."""
    (repo / ".env").write_text("A=1\n")
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        tree = app.query_one(Explorer)
        await _wait_for(pilot, lambda: bool(tree.root.children))
        assert _node_at(tree, repo / ".env") is not None  # still visible
        (repo / ".env").write_text("A=2\n")
        app._watch_tick()
        await pilot.pause()
        assert repo / ".env" not in app._changes


async def test_tracked_dotfile_is_mirrored_and_flagged(repo: Path) -> None:
    """'track .env' opts it in: it lands in the mirror and external
    edits to it are reviewable."""
    (repo / ".env").write_text("A=1\n")
    project_settings.save(repo, project_settings.Settings(track=(".env",)))
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo, paths=[repo / ".env"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = app.active_area
        sid = app.session_id
        assert sessions.mirror_exists(repo, sid, repo / ".env")
        (repo / ".env").write_text("A=2\n")
        app._watch_tick()
        await pilot.pause()
        assert repo / ".env" in app._changes
        # the inline diff shows the tracked baseline vs the agent's line
        assert area in app._inline_diff
        assert "M A=1" in area.text


async def test_ignored_file_is_not_mirrored_nor_flagged(repo: Path) -> None:
    """'ignore' opts any file/folder out: not copied to the mirror, and
    external edits to it are not flagged."""
    (repo / "assets").mkdir()
    (repo / "assets" / "big.png").write_text("fake-image-bytes" * 100)
    project_settings.save(repo, project_settings.Settings(ignore=("assets",)))
    shutil.rmtree(repo / ".alxedit")
    _new_session(repo)
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        sid = app.session_id
        assert not sessions.mirror_exists(repo, sid, repo / "assets" / "big.png")
        (repo / "assets" / "big.png").write_text("changed")
        app._watch_tick()
        await pilot.pause()
        assert repo / "assets" / "big.png" not in app._changes


async def test_ignoring_settles_a_pending_change(repo: Path) -> None:
    """Turning off tracking for a flagged file removes it from the
    pending changes (and it is not reported as a tracked deletion)."""
    app = AlxEditApp(root=repo, paths=[repo / "app.js"])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # the session must be active before _watch_tick will flag anything
        await _wait_for(pilot, lambda: app.session_id is not None)
        (repo / "app.js").write_text("const a = 999;\n")
        app._watch_tick()
        await pilot.pause()
        assert repo / "app.js" in app._changes
        app._apply_settings(project_settings.Settings(ignore=("app.js",)))
        await pilot.pause()
        assert repo / "app.js" not in app._changes


async def test_settings_screen_edits_apply_and_persist(repo: Path) -> None:
    """Add/remove rows persist to .alxeditrc immediately and update the
    app's tracking; Done closes the screen."""
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.action_settings()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)

        # add an ignore entry
        inp = screen.query_one("#settings-path", Input)
        inp.value = "assets"
        screen.query_one("#settings-add-ignore", Button).press()
        await pilot.pause()
        assert app._settings.ignore == ("assets",)
        assert project_settings.load(repo).ignore == ("assets",)

        # it now has a remove button; remove it again
        removes = [
            b for b in screen.query(Button) if b.id == "settings-remove"
        ]
        assert any(b.name == "ignore:0" for b in removes)
        [b for b in removes if b.name == "ignore:0"][0].press()
        await pilot.pause()
        assert app._settings.ignore == ()
        assert project_settings.load(repo).ignore == ()

        # add a track entry (dot file opt-in)
        inp.value = ".env"
        screen.query_one("#settings-add-track", Button).press()
        await pilot.pause()
        assert app._settings.track == (".env",)
        assert project_settings.load(repo).track == (".env",)

        # invalid input is rejected (escapes the root / empty)
        inp.value = "../outside"
        screen.query_one("#settings-add-ignore", Button).press()
        await pilot.pause()
        assert app._settings.ignore == ()

        # done closes the screen
        screen.query_one("#settings-done", Button).press()
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)


async def test_session_store_files_open_read_only(repo: Path) -> None:
    """Files under .alxedit/ (the session mirrors, i.e. the diff
    baseline) open read-only: inspectable, not editable. Plain project
    files stay editable."""
    sessions_ = sessions.list_sessions(repo)
    assert len(sessions_) == 1
    mirror = repo / ".alxedit" / sessions_[0].id / "files" / "app.js"
    app = AlxEditApp(root=repo)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        area = await app.open_path(mirror)
        assert area.read_only
        # the tab carries a lock mark
        tabs = app._tabbed.get_child_by_type(ContentTabs)
        label = tabs.get_content_tab(app._panes[area].id).label
        assert "🔒" in label.plain
        before = area.text
        await pilot.press(*list("x"))
        await pilot.pause()
        assert area.text == before  # typing is blocked
        # saving a read-only tab is refused, not a no-op write
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert mirror.read_text() == "const a = 1;\n"
        # a plain project file stays editable, and has no lock mark
        area2 = await app.open_path(repo / "app.js")
        assert not area2.read_only
        label2 = tabs.get_content_tab(app._panes[area2].id).label
        assert "🔒" not in label2.plain
