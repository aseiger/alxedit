"""Workflow: edge cases that a real user will hit — the self-write
grace window, the empty changes screen, re-tracking a file that
changed while untracked, and repeated external writes collapsing into
a single change entry.
"""

from __future__ import annotations

from pathlib import Path

from alxedit2.app import AlxEditApp

from ui import UI

SIZE = (200, 50)


async def test_own_write_grace_ignores_the_echo(project_with_session: Path) -> None:
    """The app ignores external changes that land within 3s of its own
    save of the same file (that is just our own write echoing back).
    After the window, the same write is a real change."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # save the file ourselves...
        await u.click_file("src/app.js")
        u.replace_text("const a = 5;\n")
        await u.save()
        await u.settle(5)
        assert u.disk("src/app.js") == "const a = 5;\n"

        # ...immediately (inside the grace window) an external write
        # to the same file is assumed to be our own echo
        u.ext_write("src/app.js", "const a = 6;\n")
        u.tick()
        await u.settle(10)
        assert not u.changes_lit()

        # ...but after the window has passed, the next write is a real
        # external change
        await u.wait_real(3.3)
        u.ext_write("src/app.js", "const a = 7;\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="change after grace")


async def test_empty_changes_screen_shows_a_hint(
    project_with_session: Path,
) -> None:
    """With no pending changes, the changes screen says so instead of
    showing a bare empty list."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        await u.open_changes()
        await u.wait_for(
            lambda: "(no external changes tracked)" in u.rendered(),
            what="empty hint",
        )
        await u.close_changes()


async def test_retrack_resurrects_the_pending_diff(project_with_session: Path) -> None:
    """Untrack a file, let it change, re-track: the diff against the
    ORIGINAL baseline comes back (the mirror was not re-snapshotted),
    and rejecting still restores the original content."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        original = u.disk("notes.txt")

        await u.open_node_menu(u.node_at("notes.txt"))
        await u.press_modal("#menu-untrack")
        assert "○" in u.node_label("notes.txt")

        # external edit while untracked: invisible
        u.ext_write("notes.txt", "totally different\n")
        u.tick()
        await u.settle(10)
        assert not u.changes_lit()

        # re-track: the change resurfaces against the original baseline
        await u.open_node_menu(u.node_at("notes.txt"))
        await u.press_modal("#menu-track")
        assert "T" in u.node_label("notes.txt")
        await u.wait_for(u.changes_lit, what="resurrected change")

        await u.open_changes()
        await u.wait_for(lambda: "notes.txt" in u.rendered(), what="listed")

        # rejecting restores the ORIGINAL baseline, not the untracked edit
        await u.reject_selected()
        await u.wait_for(lambda: not u.changes_lit(), what="settled")
        assert u.disk("notes.txt") == original


async def test_multiple_writes_collapse_to_one_change(
    project_with_session: Path,
) -> None:
    """Two external writes to the same file between reviews are one
    change entry, and approving settles on the final content."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        u.ext_write("notes.txt", "first edit\n")
        u.ext_write("notes.txt", "second edit\n")
        u.tick()
        await u.wait_for(u.changes_lit, what="change")

        await u.open_changes()
        rendered = u.rendered()
        # exactly one entry for the file (not one per write)
        assert rendered.count("notes.txt") == 1

        # approving adopts the final content and settles
        await u.approve_selected()
        await u.wait_for(lambda: not u.changes_lit(), what="settled")
        assert u.disk("notes.txt") == "second edit\n"
