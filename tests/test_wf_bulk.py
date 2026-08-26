"""Workflow: bulk track/untrack — shift+click a range in the explorer,
then ctrl+click opens the bulk menu (Track N / Untrack N / Clear).

Applied rules are the same ``.alxeditrc`` rules the single-entry menu
writes; the session store is never touched, no matter what the range
spans.
"""

from __future__ import annotations

from pathlib import Path

from alxedit2.app import AlxEditApp
from alxedit2 import settings as cfg

from ui import UI

SIZE = (200, 50)


def rule_paths(rc_text: str) -> list[str]:
    return [path for _, path in cfg.parse(rc_text).rules]


async def test_bulk_untrack_a_range(project_with_session: Path) -> None:
    """Shift+click two files, bulk Untrack: both get ignore rules,
    flip to ○, and external edits to both are invisible."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        await u.ensure_expanded("src")
        await u.shift_click(u.node_at("src/hello.py"))
        await u.shift_click(u.node_at("src/app.js"))
        # both entries carry the selection mark
        assert "✓" in u.node_label("src/hello.py")
        assert "✓" in u.node_label("src/app.js")

        await u.open_bulk_menu()
        assert "Track (2)" in u.rendered()
        assert "Untrack (2)" in u.rendered()
        await u.press_modal("#sel-untrack")

        rc = u.rc()
        assert "ignore src/hello.py" in rc
        assert "ignore src/app.js" in rc
        # the selection is cleared after applying
        assert "✓" not in u.node_label("src/hello.py")
        assert "○" in u.node_label("src/hello.py")
        assert "○" in u.node_label("src/app.js")

        u.ext_write("src/hello.py", "def hello():\n    return 3\n")
        u.ext_write("src/app.js", "const a = 3;\n")
        u.tick()
        await u.settle(10)
        assert not u.changes_lit()


async def test_bulk_track_mixed_entries(project_with_session: Path) -> None:
    """Bulk Track over a dotfile and a regular file: only the dotfile
    needs an explicit rule (non-dot files track by default); both flip
    to T and the dotfile's external edits now surface."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        # .env and config.json are adjacent at the root
        await u.shift_click(u.node_at(".env"))
        await u.shift_click(u.node_at("config.json"))
        await u.open_bulk_menu()
        assert "Track (2)" in u.rendered()
        await u.press_modal("#sel-track")

        rc = u.rc()
        assert "track .env" in rc
        assert "track config.json" not in rc  # default already tracks it
        assert "T" in u.node_label(".env")
        assert "T" in u.node_label("config.json")

        u.ext_write(".env", "SECRET=8\n")
        u.tick()
        await u.wait_for(u.changes_lit, what=".env now tracked")


async def test_bulk_clear_aborts_without_applying(project_with_session: Path) -> None:
    """Clear in the bulk menu only drops the selection — no rules are
    written and nothing is re-annotated."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        await u.ensure_expanded("src")
        await u.shift_click(u.node_at("src/hello.py"))
        await u.shift_click(u.node_at("src/app.js"))
        await u.open_bulk_menu()
        await u.press_modal("#sel-clear")

        # selection gone, tracking untouched
        assert "✓" not in u.node_label("src/hello.py")
        assert "T" in u.node_label("src/hello.py")
        assert rule_paths(u.rc()) == []


async def test_bulk_range_over_the_store_skips_it(project_with_session: Path) -> None:
    """A range that spans the session store untracks everything except
    the store — the store is never written to, even in bulk."""
    app = AlxEditApp(root=project_with_session)
    async with app.run_test(size=SIZE) as pilot:
        u = UI(app, pilot)
        await u.wait_tree()

        sid = u.session_ids()[0]
        # from the store node down to .alxeditrc:
        # .alxedit, docs, src, vendor, .alxeditrc
        await u.shift_click(u.node_at(".alxedit"))
        await u.shift_click(u.node_at(".alxeditrc"))
        await u.open_bulk_menu()
        await u.press_modal("#sel-untrack")

        paths = rule_paths(u.rc())
        # the store itself got no rule
        assert not any(
            p == ".alxedit" or p.startswith(".alxedit/") for p in paths
        )
        # ...but the rest of the range did
        assert "vendor" in paths
        assert "src" in paths
        assert "docs" in paths

        # the store is untouched: its mirror files are still tracked
        # baselines, and the store node still renders.
        await u.ensure_expanded(f".alxedit/{sid}")
        await u.ensure_expanded(f".alxedit/{sid}/files/src")
        assert u.node_at(f".alxedit/{sid}/files/src/app.js") is not None
        # and tampering with the store still never surfaces
        u.ext_write(f".alxedit/{sid}/files/src/app.js", "const a = 42;\n")
        u.tick()
        await u.settle(10)
        assert not u.changes_lit()
