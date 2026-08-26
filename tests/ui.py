"""Shared UI helpers for the workflow tests (``test_wf_*.py``).

Everything here drives the *running* app the way a user would — clicks,
key presses, and waits for visible state. The only non-UI operations:

- ``ext_write`` / ``ext_delete``: another program touching files on disk
  (exactly what the watcher exists to catch);
- ``tick``: runs one watcher cycle — the same callback the app's
  2-second timer fires. ``test_wf_tracking.py`` contains one test that
  exercises the real timer end-to-end instead of calling ``tick``;
- ``replace_text``: loads editor text directly for multi-line
  replacements (typing every character through the event loop is
  impractical); the *save* still goes through the UI (ctrl+s), which is
  what writes the disk.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from rich.style import Style
from textual.widgets import Button, TabPane, TextArea
from textual.widgets._tabbed_content import ContentTabs

from alxedit2 import sessions
from alxedit2.app import AlxEditApp, Explorer

# The app composes into Textual's default screen (a plain ``Screen``).
APP_NAME = "Screen"


class UI:
    """Helpers bound to one running ``AlxEditApp`` + pilot."""

    def __init__(self, app: AlxEditApp, pilot) -> None:
        self.app = app
        self.pilot = pilot

    # ------------------------------------------------------------------ #
    # waiting
    # ------------------------------------------------------------------ #
    async def wait_for(self, predicate, tries: int = 80, what: str = "condition") -> None:
        for _ in range(tries):
            if predicate():
                return
            await self.pilot.pause()
        pytest.fail(f"timed out waiting for: {what}")

    async def settle(self, n: int = 6) -> None:
        for _ in range(n):
            await self.pilot.pause()

    # ------------------------------------------------------------------ #
    # visible state
    # ------------------------------------------------------------------ #
    def rendered(self) -> str:
        """Everything on screen right now (all rendered strips)."""
        return "\n".join(
            s.text
            for s in self.app.screen._compositor.render_strips(self.app.screen.size)
        )

    def screen_name(self) -> str:
        return self.app.screen.__class__.__name__

    # ------------------------------------------------------------------ #
    # explorer
    # ------------------------------------------------------------------ #
    def ex(self) -> Explorer:
        return self.app.query_one(Explorer)

    async def wait_tree(self) -> Explorer:
        await self.wait_for(
            lambda: self.ex().root is not None and bool(self.ex().root.children),
            what="explorer tree",
        )
        await self.settle()
        return self.ex()

    async def _wait_child(self, parent, name: str):
        for _ in range(80):
            child = next(
                (c for c in parent.children if c.data and c.data.path.name == name),
                None,
            )
            if child is not None:
                return child
            await self.pilot.pause()
        pytest.fail(f"no explorer node {name!r} under {parent!r}")

    def node_at(self, rel: str):
        """The tree node for *rel* (e.g. ``"src/utils/helper.py"``),
        assuming every folder on the way is already expanded."""
        node = self.ex().root
        for part in rel.split("/"):
            node = next(
                c for c in node.children if c.data and c.data.path.name == part
            )
        return node

    async def ensure_expanded(self, rel_dir: str) -> None:
        """Expand every folder on the way to *rel_dir* (lazy tree)."""
        node = self.ex().root
        for part in rel_dir.split("/"):
            node = await self._wait_child(node, part)
            if not node.is_expanded:
                node.expand()
            await self.wait_for(
                lambda n=node: bool(n.children), what=f"children of {part!r}"
            )

    def node_label(self, rel: str) -> str:
        """The full label the explorer renders for *rel* (glyphs,
        selection mark, change markers included)."""
        return self.ex().render_label(self.node_at(rel), Style(), Style()).plain

    async def click_file(self, rel: str) -> None:
        """Click a file in the explorer (expanding folders as needed) —
        opens its tab, exactly like a user would."""
        parts = rel.split("/")
        if len(parts) > 1:
            await self.ensure_expanded("/".join(parts[:-1]))
        node = self.node_at(rel)
        region = self.ex()._get_label_region(node._line)
        await self.pilot.click(Explorer, offset=(region.x + 2, region.y))
        name = parts[-1]
        await self.wait_for(
            lambda: any(name in l for l in self.tab_labels()),
            what=f"tab for {rel!r}",
        )

    async def open_node_menu(self, node) -> None:
        """Ctrl+click a node — the node's context menu."""
        region = self.ex()._get_label_region(node._line)
        await self.pilot.click(
            Explorer, offset=(region.x + 2, region.y), control=True
        )
        await self.wait_for(
            lambda: self.screen_name() == "NodeMenuScreen", what="node menu"
        )

    async def shift_click(self, node) -> None:
        """Shift+click a node — start/extend the bulk selection range."""
        region = self.ex()._get_label_region(node._line)
        await self.pilot.click(
            Explorer, offset=(region.x + 2, region.y), shift=True
        )
        await self.settle()

    async def open_bulk_menu(self) -> None:
        """Ctrl+click while a range of 2+ is selected — the bulk
        Track/Untrack menu (any entry opens it in that state)."""
        node = self.ex().root
        region = self.ex()._get_label_region(node._line)
        await self.pilot.click(
            Explorer, offset=(region.x + 2, region.y), control=True
        )
        await self.wait_for(
            lambda: self.screen_name() == "SelectionMenuScreen", what="bulk menu"
        )

    def has_button(self, selector: str) -> bool:
        """Whether a button matching *selector* exists on the current screen."""
        try:
            self.app.screen.query_one(selector, Button)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # tabs & editor
    # ------------------------------------------------------------------ #
    def tabs(self) -> ContentTabs:
        """The ContentTabs strip (``#tabbed`` holds it, then the panes)."""
        return self.app.query_one("#tabbed").get_child_by_type(ContentTabs)

    def tab_labels(self) -> list[str]:
        """The visible tab labels (in order), including status marks."""
        tabs = self.tabs()
        panes = self.app.query(TabPane)  # panes live in #tabbed, not the strip
        return [str(tabs.get_content_tab(pane.id).label) for pane in panes]

    def active_area(self) -> TextArea:
        area = self.app.active_area
        assert area is not None, "no active editor tab"
        return area

    def replace_text(self, text: str) -> None:
        """Replace the active tab's content (see module note on typing)."""
        self.active_area().load_text(text)

    async def type(self, text: str) -> None:
        """Type *text* into the focused widget (one key event per char).

        Only reliable for short printable strings — the pilot can't
        reproduce select-all or newlines; use ``replace_text`` for
        multi-line editor content and ``press`` for individual keys.
        """
        await self.pilot.press(*text)
        await self.pilot.pause()

    async def save(self) -> None:
        await self.pilot.press("ctrl+s")
        await self.settle()

    async def close_tab(self) -> None:
        await self.pilot.press("f4")
        await self.settle()

    # ------------------------------------------------------------------ #
    # modal screens
    # ------------------------------------------------------------------ #
    async def press_modal(self, button_id: str, back_to: str = APP_NAME) -> None:
        """Press a button on the current (modal) screen and wait for the
        app to be back at *back_to*.

        Textual's ``Button.press`` silently no-ops while the button is not
        yet displayed, so wait for display first.
        """
        btn = self.app.screen.query_one(button_id, Button)
        await self.wait_for(lambda: btn.display, what=f"{button_id} displayed")
        btn.press()
        await self.wait_for(
            lambda: self.screen_name() == back_to, what=f"back to {back_to}"
        )
        await self.settle()

    async def confirm(self) -> None:
        """Press the confirm button on a ConfirmScreen."""
        await self.wait_for(
            lambda: self.screen_name() == "ConfirmScreen", what="confirm screen"
        )
        btn = self.app.screen.query_one("#confirm", Button)
        await self.wait_for(lambda: btn.display, what="#confirm displayed")
        btn.press()
        await self.settle()

    # ---- changes screen (F2) ------------------------------------------ #
    async def open_changes(self) -> None:
        # press the top-bar button (lives on the app screen, found app-wide)
        btn = self.app.query_one("#btn-changes", Button)
        btn.press()
        await self.wait_for(
            lambda: self.screen_name() == "ChangesScreen", what="changes screen"
        )
        # the fresh screen must be fully displayed before its buttons
        # accept presses
        self.app.screen.query_one("#btn-reject", Button)
        await self.wait_for(
            lambda: self.app.screen.query_one("#btn-reject", Button).display,
            what="changes screen ready",
        )

    async def close_changes(self) -> None:
        await self.pilot.press("q")
        await self.wait_for(lambda: self.screen_name() == APP_NAME, what="app")

    def changes_lit(self) -> bool:
        return self.app.query_one("#btn-changes", Button).has_class("has-changes")

    async def approve_selected(self) -> None:
        await self.press_modal("#btn-approve", back_to="ChangesScreen")
        await self.confirm()
        await self.wait_for(
            lambda: self.screen_name() == "ChangesScreen", what="changes screen"
        )

    async def reject_selected(self) -> None:
        await self.press_modal("#btn-reject", back_to="ChangesScreen")
        await self.confirm()
        await self.wait_for(
            lambda: self.screen_name() == "ChangesScreen", what="changes screen"
        )

    # ---- session picker ------------------------------------------------ #
    async def open_sessions(self) -> None:
        self.app.query_one("#btn-session", Button).press()
        await self.wait_for(
            lambda: self.screen_name() == "SessionScreen", what="session screen"
        )

    async def new_session(self, label: str | None = None) -> None:
        """Create a session from the picker (opens it if needed)."""
        if self.screen_name() != "SessionScreen":
            await self.open_sessions()
        if label:
            await self.pilot.click("#session-label-input")
            await self.type(label)
        await self.press_modal("#sess-new")

    async def open_session_at(self, index: int) -> None:
        for _ in range(index):
            await self.pilot.press("down")
        await self.press_modal("#sess-open")

    async def delete_session_at(self, index: int) -> None:
        for _ in range(index):
            await self.pilot.press("down")
        self.app.screen.query_one("#sess-delete", Button).press()
        await self.settle()  # armed: button now reads "Sure?"
        self.app.screen.query_one("#sess-delete", Button).press()
        await self.settle()

    async def cancel_sessions(self) -> None:
        await self.press_modal("#sess-cancel")

    # ------------------------------------------------------------------ #
    # the "other program" + the watcher
    # ------------------------------------------------------------------ #
    def ext_write(self, rel: str, content: str) -> None:
        """Another program writes a file in the project."""
        p = self.app.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def ext_delete(self, rel: str) -> None:
        (self.app.root / rel).unlink()

    def tick(self) -> None:
        """Run one watcher cycle (the 2s timer's own callback)."""
        self.app._watch_tick()

    async def wait_real(self, seconds: float) -> None:
        """Sleep in *real* time — needed past the app's 3s own-write
        grace period and for the real 2s watcher timer."""
        await asyncio.sleep(seconds)

    # ------------------------------------------------------------------ #
    # project state (on disk)
    # ------------------------------------------------------------------ #
    @property
    def root(self) -> Path:
        return self.app.root

    def disk(self, rel: str) -> str:
        return (self.root / rel).read_text()

    def rc(self) -> str:
        return (self.root / ".alxeditrc").read_text()

    def session_ids(self) -> list[str]:
        return [s.id for s in sessions.list_sessions(self.root)]
