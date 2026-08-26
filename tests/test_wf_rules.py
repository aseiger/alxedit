"""Workflow: tracking rules — what the session mirror (the diff/revert
baseline) covers, controlled from the explorer menu and the Settings
screen.

The explorer always shows every file; tracking only decides whether a
file is part of the baseline and change tracking. Rules live in
``.alxeditrc`` (``track``/``ignore`` lines, last matching rule wins);
the tree annotates each entry with ``T`` (tracked) or ``○`` (not).
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button

from alxedit2.app import AlxEditApp

from ui import UI

SIZE = (200, 50)


async def test_track_dotfile_from_the_menu(project_with_session: Path) -> None:
    """.env is untracked by default (○); Track from its menu includes it
    — and external edits to it now surface as changes."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        assert "○" in u.node_label(".env")

        await u.open_node_menu(u.node_at(".env"))
        # the menu offers Track (not Untrack) for an untracked entry
        assert u.has_button("#menu-track")
        assert not u.has_button("#menu-untrack")
        await u.press_modal("#menu-track")

        # the rule is persisted ...
        assert "track .env" in u.rc()
        # ... and the glyph flips to tracked
        assert "T" in u.node_label(".env")

        # an external edit to the (now tracked) dotfile is a real change
        u.ext_write(".env", "SECRET=2\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="tracked dotfile change")
        await u.open_changes()
        await u.wait_for(lambda: ".env" in u.rendered(), what=".env listed")
        await u.approve_selected()
        await u.wait_for(lambda: not u.changes_lit(), what="settled")


async def test_untrack_file_hides_it_from_tracking(project_with_session: Path) -> None:
    """Untrack a regular file: the glyph flips to ○ and external edits
    to it never surface anymore."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        assert "T" in u.node_label("notes.txt")

        await u.open_node_menu(u.node_at("notes.txt"))
        assert u.has_button("#menu-untrack")
        await u.press_modal("#menu-untrack")

        assert "ignore notes.txt" in u.rc()
        assert "○" in u.node_label("notes.txt")

        # external edits are invisible now
        u.ext_write("notes.txt", "changed externally\n")
        u.tick()
        await u.settle(10)
        assert not u.changes_lit()


async def test_untrack_folder_is_recursive(project_with_session: Path) -> None:
    """Untracking a folder covers everything below it, and tracking
    state is re-annotated throughout the subtree."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        await u.open_node_menu(u.node_at("src"))
        await u.press_modal("#menu-untrack")
        assert "ignore src" in u.rc()

        await u.ensure_expanded("src")
        assert "○" in u.node_label("src/app.js")
        assert "○" in u.node_label("src/hello.py")
        await u.ensure_expanded("src/utils")
        assert "○" in u.node_label("src/utils/helper.py")

        # none of the subtree is tracked anymore
        u.ext_write("src/app.js", "const a = 9;\n")
        u.ext_write("src/hello.py", "def hello():\n    return 99\n")
        u.tick()
        await u.settle(10)
        assert not u.changes_lit()


async def test_carve_file_out_of_untracked_folder(project_with_session: Path) -> None:
    """Untrack a folder, then Track one file inside it: the file is
    tracked again (last rule wins) while its siblings stay untracked."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        await u.open_node_menu(u.node_at("src"))
        await u.press_modal("#menu-untrack")
        assert "ignore src" in u.rc()

        await u.ensure_expanded("src")
        await u.open_node_menu(u.node_at("src/app.js"))
        await u.press_modal("#menu-track")

        # both rules present, file rule after the folder rule
        rc = u.rc()
        assert "ignore src" in rc
        assert "track src/app.js" in rc
        assert "T" in u.node_label("src/app.js")
        assert "○" in u.node_label("src/hello.py")

        # the carved-out file is tracked; its sibling is not
        u.ext_write("src/app.js", "const a = 5;\n")
        u.ext_write("src/hello.py", "def hello():\n    return 5\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="carved file change")
        await u.open_changes()
        await u.wait_for(
            lambda: "src/app.js" in u.rendered(), what="app.js listed"
        )
        assert "src/hello.py" not in u.rendered()


async def test_folder_action_clears_carve_outs(project_with_session: Path) -> None:
    """A folder action is decisive: it clears every rule below the
    folder, so a previously carved-out file goes with the folder."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # set up: untracked folder with one tracked file inside
        await u.open_node_menu(u.node_at("src"))
        await u.press_modal("#menu-untrack")
        await u.ensure_expanded("src")
        await u.open_node_menu(u.node_at("src/app.js"))
        await u.press_modal("#menu-track")
        assert "ignore src" in u.rc()
        assert "track src/app.js" in u.rc()
        assert "T" in u.node_label("src/app.js")
        assert "○" in u.node_label("src/hello.py")

        # the folder is (partially) tracked again, so its menu offers
        # Untrack — and pressing it drops the carve-out with the folder
        await u.open_node_menu(u.node_at("src"))
        assert u.has_button("#menu-untrack")
        await u.press_modal("#menu-untrack")
        rc = u.rc()
        assert "ignore src" in rc
        assert "track src/app.js" not in rc
        assert "○" in u.node_label("src/app.js")
        assert "○" in u.node_label("src/hello.py")

        # the whole folder is invisible to tracking again
        u.ext_write("src/app.js", "const a = 4;")
        u.ext_write("src/hello.py", "def hello():\n    return 4\n")
        u.tick()
        await u.settle(10)
        assert not u.changes_lit()


async def test_settings_screen_glob_rule(project_with_session: Path) -> None:
    """The Settings screen adds rules (globs included); ``*.log`` covers
    .log files at any depth, and everything else still tracks."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        u.app.query_one("#btn-settings", Button).press()
        await u.wait_for(
            lambda: u.screen_name() == "SettingsScreen", what="settings"
        )
        await u.pilot.click("#settings-path")
        await u.type("*.log")
        btn = u.app.screen.query_one("#settings-add-ignore", Button)
        await u.wait_for(lambda: btn.display, what="add-ignore shown")
        btn.press()
        # the rule is persisted (and listed on the screen)
        await u.wait_for(lambda: "ignore *.log" in u.rc(), what="rule persisted")
        await u.press_modal("#settings-done")

        # .log files anywhere are invisible; a .txt change still surfaces
        u.ext_write("debug.log", "log line\n")
        u.ext_write("src/deep/nested.log", "log line\n")
        u.ext_write("notes.txt", "changed\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="notes.txt change")
        await u.open_changes()
        await u.wait_for(lambda: "notes.txt" in u.rendered(), what="listed")
        assert "debug.log" not in u.rendered()
        assert "nested.log" not in u.rendered()


async def test_settings_screen_removes_a_rule(project_with_session: Path) -> None:
    """A rule added in Settings can be removed again (the x button);
    tracking behavior follows the removal."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # add 'ignore notes.txt' via the Settings screen
        u.app.query_one("#btn-settings", Button).press()
        await u.wait_for(
            lambda: u.screen_name() == "SettingsScreen", what="settings"
        )
        await u.pilot.click("#settings-path")
        await u.type("notes.txt")
        btn = u.app.screen.query_one("#settings-add-ignore", Button)
        btn.press()
        await u.wait_for(
            lambda: "ignore notes.txt" in u.rc(), what="rule persisted"
        )

        # remove it again (the x on the ignore row)
        u.app.screen.query_one("#settings-remove", Button).press()
        await u.wait_for(
            lambda: "ignore notes.txt" not in u.rc(), what="rule removed"
        )
        await u.press_modal("#settings-done")

        # tracked again: external edits surface
        u.ext_write("notes.txt", "changed\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="change surfaces again")
