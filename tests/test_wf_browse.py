"""Workflow: a user opens a folder, browses it, reads and edits files.

These tests drive the real UI only: explorer clicks (with lazy
expansion), real typing, ctrl+s saves, tab switching, quitting.
"""

from __future__ import annotations

from pathlib import Path

from alxedit2.app import AlxEditApp

from ui import UI

SIZE = (200, 50)


async def test_open_nested_file_shows_its_content(project: Path) -> None:
    """Browse into src/utils/ and open a depth-2 file: the tab shows the
    file's real content."""
    app = AlxEditApp(root=project)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()
        await u.click_file("src/utils/helper.py")
        assert any("helper.py" in l for l in u.tab_labels())
        assert u.active_area().text == (project / "src/utils/helper.py").read_text()


async def test_edit_save_close_reopen(project: Path) -> None:
    """Edit a file, save it, close the tab, open it again: the new content
    is there (and on disk)."""
    new_text = "remember the milk\nand the eggs\n"
    app = AlxEditApp(root=project)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()
        await u.click_file("notes.txt")
        assert u.active_area().text == "remember the milk\n"

        # genuine keystrokes: cursor to end-of-line, type a character
        await u.pilot.click(u.active_area())
        await u.pilot.press("end", "x")
        await u.pilot.pause()
        assert u.active_area().text == "remember the milkx\n"

        # a multi-line rewrite (the pilot can't type newlines;
        # the *save* below is still the real ctrl+s)
        u.replace_text(new_text)
        await u.save()

        # saved to disk by the app's save action
        assert u.disk("notes.txt") == new_text

        # close and come back: the tab re-reads the disk
        await u.close_tab()
        assert not any("notes.txt" in l for l in u.tab_labels())
        await u.click_file("notes.txt")
        assert u.active_area().text == new_text


async def test_switch_between_open_files(project: Path) -> None:
    """Two files open at once; clicking a file in the explorer switches to
    its tab (and back)."""
    app = AlxEditApp(root=project)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        await u.click_file("src/app.js")
        await u.click_file("config.json")
        assert any("app.js" in l for l in u.tab_labels())
        assert any("config.json" in l for l in u.tab_labels())
        # the most recently opened file is active
        assert u.active_area().text == u.disk("config.json")

        # click app.js in the explorer again -> its tab comes forward
        await u.click_file("src/app.js")
        assert u.active_area().text == u.disk("src/app.js")
        # still two tabs, not three
        assert len(u.tab_labels()) == 2


async def test_editor_uses_the_file_language(project: Path) -> None:
    """Each file opens with its own syntax (python / javascript / json)."""
    app = AlxEditApp(root=project)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()
        def lang() -> str:
            return str(u.active_area().language or "").lower()

        await u.click_file("src/hello.py")
        await u.wait_for(lambda: "python" in lang(), what="python language")
        await u.click_file("src/app.js")
        await u.wait_for(lambda: "javascript" in lang(), what="javascript language")
        await u.click_file("config.json")
        await u.wait_for(lambda: "json" in lang(), what="json language")


async def test_quit_with_ctrl_q(project: Path) -> None:
    """ctrl+q quits the app cleanly (files untouched)."""
    app = AlxEditApp(root=project)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()
        await u.click_file("notes.txt")
        assert app.is_running
        await pilot.press("ctrl+q")
        await u.wait_for(lambda: not app.is_running, what="app to exit")
    # context manager exited without error; the project is intact
    assert u.disk("notes.txt") == "remember the milk\n"
