"""Workflow: session store safety — the ``.alxedit`` directory holds
each session's baseline copies. It is a read-only source of truth:

* it always shows in the explorer (even when untracked);
* its entries' menus offer no rename/delete/track actions;
* store tabs open read-only and saving is refused;
* external writes inside the store never surface as changes;
* deleting a session removes its store directory.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button

from alxedit2.app import AlxEditApp

from ui import UI

SIZE = (200, 50)


async def test_store_menu_offers_no_mutations(project_with_session: Path) -> None:
    """Ctrl+click a baseline copy: the menu explains the store is
    read-only and offers nothing but Close — no rename/delete/track."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        sid = u.session_ids()[0]
        await u.ensure_expanded(f".alxedit/{sid}")
        await u.ensure_expanded(f".alxedit/{sid}/files/src")
        node = u.node_at(f".alxedit/{sid}/files/src/app.js")

        await u.open_node_menu(node)
        assert "session store — read-only" in u.rendered()
        for selector in (
            "#menu-rename",
            "#menu-delete",
            "#menu-track",
            "#menu-untrack",
        ):
            assert not u.has_button(selector), selector
        await u.press_modal("#menu-cancel")
        # nothing changed
        assert u.disk(f".alxedit/{sid}/files/src/app.js") == "const a = 1;\n"


async def test_store_tab_is_read_only_and_save_is_refused(
    project_with_session: Path,
) -> None:
    """Opening a baseline copy gives a read-only tab (🔒); the editor
    refuses to save through it, so the baseline copy is untouched."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        sid = u.session_ids()[0]
        store_rel = f".alxedit/{sid}/files/src/app.js"
        await u.click_file(store_rel)

        # the tab carries the read-only indicator
        assert any("🔒" in label for label in u.tab_labels())
        area = u.active_area()
        assert area.read_only

        original = u.disk(store_rel)
        # the user still types into the editor...
        area.load_text("const a = 999;\n")
        # ...but saving is refused
        await u.save()
        await u.settle(5)
        assert u.disk(store_rel) == original


async def test_store_writes_never_surface_as_changes(
    project_with_session: Path,
) -> None:
    """Someone tampers with a baseline copy behind the app's back:
    it must never show up as an external change to review."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        sid = u.session_ids()[0]
        u.ext_write(f".alxedit/{sid}/files/src/app.js", "const a = 777;\n")
        u.tick()
        await u.settle(10)
        assert not u.changes_lit()
        await u.open_changes()
        await u.wait_for(
            lambda: "(no external changes tracked)" in u.rendered(),
            what="empty list",
        )
        await u.close_changes()


async def test_root_menu_has_no_delete_or_track(project_with_session: Path) -> None:
    """The project root can be renamed (into a new folder) but never
    deleted, and tracking has no meaning there — no toggle offered."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        await u.open_node_menu(u.ex().root)
        delete = u.app.screen.query_one("#menu-delete", Button)
        assert delete.disabled
        assert not u.has_button("#menu-track")
        assert not u.has_button("#menu-untrack")
        await u.press_modal("#menu-cancel")


async def test_deleting_a_session_removes_its_store(project_with_session: Path) -> None:
    """Deleting a session from the picker removes its whole store
    directory (baseline copies and session.json)."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        root = project_with_session
        sid = u.session_ids()[0]
        store_dir = root / ".alxedit" / sid
        assert store_dir.is_dir()

        # create a second session so v1 is no longer active,
        # activate it, then delete v1 from the picker
        await u.new_session(label="v2")
        await u.open_sessions()
        await u.open_session_at(0)  # v2 (newest first)
        assert u.session_ids()[0] != sid

        await u.open_sessions()
        await u.delete_session_at(1)

        assert sid not in u.session_ids()
        assert not store_dir.exists()
