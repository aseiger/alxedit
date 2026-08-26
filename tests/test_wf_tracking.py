"""Workflow: external-change tracking — the app's core promise.

A file changed, added, renamed or deleted *outside* the editor shows
up as a pending change; the user reviews it (F2, inline diff, or
open-in-editor) and either adopts it (Approve) or restores the
session baseline (Reject). Untracked files are invisible to all of
this.

Note on timing: the app deliberately ignores external changes that
land within 3 seconds of its *own* write to the same file
(``SELF_WRITE_GRACE``), so a test that edits right after a save/reject
waits past that window first (``wait_real``). One test exercises the
real 2-second watcher timer instead of the deterministic ``tick``.
"""

from __future__ import annotations

from pathlib import Path

from alxedit2.app import AlxEditApp

from ui import UI

SIZE = (200, 50)


async def test_external_edit_approve_and_save(project_with_session: Path) -> None:
    """Open a file, an external edit lands, the user adopts it from the
    Changes screen, and saving commits the new baseline."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # user has the file open
        await u.click_file("src/app.js")
        assert u.active_area().text == "const a = 1;\n"

        # another program rewrites it
        u.ext_write("src/app.js", "const a = 42;\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending change")
        # the explorer marks the file with +1/-1
        assert "+1" in u.node_label("src/app.js")

        # adopt it from the Changes screen
        await u.open_changes()
        await u.wait_for(lambda: "src/app.js" in u.rendered(), what="src/app.js listed")
        await u.approve_selected()

        # the open tab now shows the disk content, uncommitted (●)
        assert u.active_area().text == "const a = 42;\n"
        assert any("●" in l for l in u.tab_labels())

        # saving commits it: no more dot, no more pending change
        await u.save()
        assert not any("●" in l for l in u.tab_labels())
        await u.wait_for(lambda: not u.changes_lit(), what="no pending change")
        assert u.disk("src/app.js") == "const a = 42;\n"


async def test_external_edit_can_be_rejected(project_with_session: Path) -> None:
    """Same change, different decision: Reject restores the baseline."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        u.ext_write("src/app.js", "const a = 42;\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending change")

        await u.open_changes()
        await u.reject_selected()
        await u.wait_for(lambda: not u.changes_lit(), what="no pending change")
        # the file is back to what the session saw
        assert u.disk("src/app.js") == "const a = 1;\n"


async def test_external_delete_approve_and_reject(project_with_session: Path) -> None:
    """A file deleted outside: Reject brings it back; Approve accepts
    the deletion."""
    root = project_with_session
    original = (root / "src/hello.py").read_text()

    app = AlxEditApp(root=root)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # first pass: reject the deletion -> file restored
        u.ext_delete("src/hello.py")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending deletion")
        await u.open_changes()
        await u.wait_for(lambda: "src/hello.py" in u.rendered(), what="deletion listed")
        await u.reject_selected()
        assert (root / "src/hello.py").read_text() == original
        await u.wait_for(lambda: not u.changes_lit(), what="restored")

        # the app just wrote the file back — wait past its own-write
        # grace before the next external action counts
        await u.wait_real(3.3)

        # second pass: approve the deletion -> it stays gone.
        # (The Changes screen is still open — Reject doesn't close it —
        # and its list re-lit and re-rendered on its own.)
        u.ext_delete("src/hello.py")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending deletion (again)")
        await u.wait_for(lambda: "src/hello.py" in u.rendered(), what="deletion re-listed")
        await u.approve_selected()
        assert not (root / "src/hello.py").exists()
        await u.wait_for(lambda: not u.changes_lit(), what="settled")


async def test_external_new_file_approve_and_reject(project_with_session: Path) -> None:
    """A file created outside: Approve keeps it; Reject deletes it."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # an agent drops a new file in
        u.ext_write("src/agent.txt", "generated\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending addition")
        await u.open_changes()
        await u.wait_for(lambda: "src/agent.txt" in u.rendered(), what="addition listed")

        # keep it
        await u.approve_selected()
        assert (app.root / "src/agent.txt").read_text() == "generated\n"
        await u.wait_for(lambda: not u.changes_lit(), what="settled")

        # ...and a second one is unwanted
        u.ext_write("src/spam.txt", "nope\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending addition (second)")
        await u.open_changes()
        await u.reject_selected()
        assert not (app.root / "src/spam.txt").exists()
        await u.wait_for(lambda: not u.changes_lit(), what="settled (second)")


async def test_untracked_files_are_invisible_to_tracking(project_with_session: Path) -> None:
    """Dotfiles are untracked by default: external changes to them never
    surface."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()
        assert "○" in u.node_label(".env")

        u.ext_write(".env", "SECRET=2\n")
        u.tick()
        await u.settle(10)
        assert not u.changes_lit()

        # and nothing to see in the Changes screen either
        await u.open_changes()
        await u.wait_for(lambda: ".env" not in u.rendered(), what=".env absent")
        await u.close_changes()


async def test_several_changes_listed_and_all_rejected(project_with_session: Path) -> None:
    """Three files change at once: all three are listed, and 'Reject
    all' restores the whole batch."""
    root = project_with_session
    before = {
        "src/app.js": (root / "src/app.js").read_text(),
        "src/hello.py": (root / "src/hello.py").read_text(),
        "notes.txt": (root / "notes.txt").read_text(),
    }

    app = AlxEditApp(root=root)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        u.ext_write("src/app.js", "const a = 999;\n")
        u.ext_write("src/hello.py", "def hello():\n    return 'changed'\n")
        u.ext_write("notes.txt", "totally different\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending changes")

        await u.open_changes()
        await u.wait_for(
            lambda: all(
                name in u.rendered() for name in ("src/app.js", "src/hello.py", "notes.txt")
            ),
            what="all three changes listed",
        )

        await u.press_modal("#btn-reject-all", back_to="ChangesScreen")
        await u.confirm()
        await u.wait_for(lambda: not u.changes_lit(), what="batch rejected")
        for rel, orig in before.items():
            assert (root / rel).read_text() == orig


async def test_change_in_nested_folder_is_reviewable(project_with_session: Path) -> None:
    """A change in a lazily-loaded nested folder is found, reviewed via
    open-in-editor, adopted, and saved."""
    root = project_with_session
    original = (root / "src/utils/helper.py").read_text()

    app = AlxEditApp(root=root)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        u.ext_write("src/utils/helper.py", "def helper():\n    return 43\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending change")

        # the change is listed with its full relative path
        await u.open_changes()
        await u.wait_for(
            lambda: "src/utils/helper.py" in u.rendered(), what="nested path listed"
        )

        # 'o' opens it in the editor — and because the file has a pending
        # external change it goes straight into the read-only diff review
        # (old line ghosted, new line live)
        await u.pilot.press("o")
        await u.wait_for(lambda: u.screen_name() == "Screen", what="back in app")
        assert any("helper.py" in l for l in u.tab_labels())
        area = u.active_area()
        assert area.read_only
        assert "return 42" in area.text  # the old side, ghosted
        assert "return 43" in area.text  # the new side

        # esc leaves the review: the tab is editable with the disk content
        await u.pilot.press("escape")
        await u.wait_for(lambda: not area.read_only, what="editable again")
        assert area.text == "def helper():\n    return 43\n"

        # save commits the change against the baseline
        await u.save()
        await u.wait_for(lambda: not u.changes_lit(), what="settled")
        assert (root / "src/utils/helper.py").read_text() == "def helper():\n    return 43\n"


async def test_watcher_fires_on_its_own_timer(project_with_session: Path) -> None:
    """Without any manual tick: the app's real 2-second timer catches
    the external change on its own."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        u.ext_write("src/app.js", "const a = 7;\n")
        # no tick() here — wait for the real timer (2s interval)
        for _ in range(150):  # up to ~15s of real time
            if u.changes_lit():
                break
            await u.wait_real(0.1)
        assert u.changes_lit()


async def test_inline_diff_review_is_read_only(project_with_session: Path) -> None:
    """Entering a change from the Changes screen opens a read-only
    inline review; esc exits it."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        u.ext_write("src/app.js", "const a = 42;\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending change")

        await u.open_changes()
        # focus the list (Enter on a focused button would activate it)
        u.app.screen.query_one("#changes-list").focus()
        await u.pilot.press("enter")
        await u.wait_for(lambda: u.screen_name() == "Screen", what="back in app")
        area = u.active_area()
        assert area.read_only
        # the review shows the diff (old line struck, new line added)
        assert "const a = 1" in area.text
        assert "const a = 42" in area.text

        # esc exits the review; the tab is editable again
        await u.pilot.press("escape")
        await u.settle()
        assert not area.read_only
