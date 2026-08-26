"""Workflow: sessions — baselines the user can switch between.

Covers: creating a session from the picker, the tracking glyphs it
produces, switching between two sessions with different baselines,
rejecting an external change against the *current* baseline, and
deleting a session (including the store-tab cleanup and the
active-session guard).
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button

from alxedit2 import sessions
from alxedit2.app import AlxEditApp

from ui import APP_NAME, UI

SIZE = (200, 50)


async def test_new_session_and_the_tracking_glyphs(project: Path) -> None:
    """The tree annotates tracking state (T = covered, ○ = skipped) and
    the session picker creates a baseline; both are visible to the user."""
    app = AlxEditApp(root=project)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # default tracking, before any session: ordinary files T,
        # dotfiles ○
        assert "T" in u.node_label("notes.txt")
        assert "T" in u.node_label("src")
        assert "○" in u.node_label(".env")

        # create a baseline from the picker
        await u.open_sessions()
        await u.new_session(label="baseline")

        # the tracking annotations follow the settings (not the
        # session) ... and a session now exists on disk
        assert "T" in u.node_label("notes.txt")
        assert "○" in u.node_label(".env")
        assert len(u.session_ids()) == 1


async def test_switch_sessions_changes_the_baseline(project_with_session: Path) -> None:
    """Two sessions can have different baselines for the same file.

    A session snapshots the disk at creation, and saving moves the
    active session's baseline — so two sessions created at different
    times judge the same file differently: Reject under each one
    restores *that* session's baseline.
    """
    root = project_with_session

    app = AlxEditApp(root=root)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # 1) user edit in the editor, saved -> v1's baseline moves to 100
        await u.click_file("src/app.js")
        u.replace_text("const a = 100;\n")
        await u.save()
        assert u.disk("src/app.js") == "const a = 100;\n"

        # 2) an external program moves the file to some other content
        await u.wait_real(3.3)  # let the app's own-write grace elapse
        u.ext_write("src/app.js", "const a = 2;\n")
        u.tick()
        # v1's baseline is 100 -> pending change
        await u.wait_for(u.changes_lit, what="pending change in v1")

        # 3) snapshot this state (disk = 2) as v2 -> v2's baseline is 2.
        # The app.js tab is still open with the pre-change content, so
        # creating the session asks before discarding those edits.
        if u.screen_name() != "SessionScreen":
            await u.open_sessions()
        await u.pilot.click("#session-label-input")
        await u.type("v2")
        btn = u.app.screen.query_one("#sess-new", Button)
        await u.wait_for(lambda: btn.display, what="#sess-new displayed")
        btn.press()
        await u.confirm()
        await u.wait_for(lambda: u.screen_name() == APP_NAME, what="back to app")
        # the disk now matches v2's baseline -> out of the change list
        assert len(u.session_ids()) == 2
        await u.wait_for(lambda: not u.changes_lit(), what="clean in v2")

        # 4) the agent puts the file back to 100 -> pending under v2 (2)
        await u.wait_real(3.3)
        u.ext_write("src/app.js", "const a = 100;\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="pending change in v2")

        # 5) reject under v2 -> restored to v2's baseline (2), not v1's
        await u.open_changes()
        assert "src/app.js" in u.rendered()
        await u.reject_selected()
        assert u.disk("src/app.js") == "const a = 2;\n"
        await u.wait_for(lambda: not u.changes_lit(), what="clean again")
        await u.close_changes()

        # 6) switch back to v1 (the older entry, newest-first list)
        n = len(u.session_ids())
        await u.open_sessions()
        await u.open_session_at(n - 1)
        # the file (2) now differs from v1's baseline (100) -> pending;
        # rejecting under v1 restores v1's baseline (100) instead
        await u.wait_for(u.changes_lit, what="pending change in v1 again")
        await u.open_changes()
        await u.reject_selected()
        assert u.disk("src/app.js") == "const a = 100;\n"
        await u.wait_for(lambda: not u.changes_lit(), what="clean in v1")


async def test_delete_session_closes_its_store_tabs(project_with_session: Path) -> None:
    """Session-store files open read-only; deleting the session
    closes those tabs automatically."""
    root = project_with_session
    sid1 = sessions.list_sessions(root)[0].id

    app = AlxEditApp(root=root)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # open one of session 1's store files: read-only, with the lock.
        # (mirror files live under the session's files/ directory)
        await u.click_file(f".alxedit/{sid1}/files/src/app.js")
        labels = u.tab_labels()
        assert any("app.js" in l for l in labels)
        assert any("🔒" in l for l in labels)
        assert u.active_area().read_only

        # create v2 (becomes the active session), then delete session 1
        await u.new_session(label="v2")
        await u.open_sessions()
        await u.open_session_at(0)  # v2 is newest -> top of the list
        await u.open_sessions()
        await u.delete_session_at(1)  # session 1 is now second

        # session 1 is gone from disk ... and its store tab is closed
        assert sid1 not in u.session_ids()
        assert not any("app.js" in l for l in u.tab_labels())


async def test_deleting_the_active_session_is_refused(project_with_session: Path) -> None:
    """The active session can't be deleted from the picker."""
    root = project_with_session
    sid = sessions.list_sessions(root)[0].id
    (root / ".alxedit" / sid / "session.json").write_text(
        '{"label": "the only one"}\n', encoding="utf-8"
    )

    app = AlxEditApp(root=root)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        await u.open_sessions()
        await u.delete_session_at(0)  # delete the active session

        # refused: it still exists on disk, and the picker still lists it
        assert u.session_ids() == [sid]
        assert "the only one" in u.rendered()
        await u.cancel_sessions()
