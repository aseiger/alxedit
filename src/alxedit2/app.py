"""alxedit2 — IDE-style TUI: file explorer sidebar + tabbed, syntax-highlighted editor.

Also tracks changes made to the project tree from *outside* the editor
(e.g. an AI agent writing files) and offers diff + revert for them.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar, Iterable, Optional

from rich.style import Style
from rich.text import Text

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DirectoryTree,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TabPane,
    TabbedContent,
    TextArea,
)
from textual.widgets._tabbed_content import ContentTabs

from textual._text_area_theme import TextAreaTheme

from .languages import language_for_path


# --------------------------------------------------------------------------- #
# inline diff theme (colored +/− lines inside the editor)
# --------------------------------------------------------------------------- #

#: TextArea theme that maps our diff highlight names to colors. No tree-sitter
#: grammar is involved — we fill the highlight map ourselves.
_DIFF_THEME = TextAreaTheme(
    "alxdiff",
    syntax_styles={
        "diff_add": Style(color="green"),
        "diff_del": Style(color="red", strike=True),
    },
)


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #


@dataclass
class Buffer:
    """Editor state for one open tab."""

    path: Optional[Path]
    """Where the buffer lives on disk, or ``None`` for an untitled buffer."""

    saved_text: str
    """Text as last saved; the buffer is dirty when the TextArea differs."""

    modified: bool = False

    external: bool = False
    """Set when the file on disk changed outside alxedit2 since our last save."""

    clean_at_diff: bool = False
    """Was the buffer clean when the inline diff took over the tab?"""

    @property
    def title(self) -> str:
        return "untitled" if self.path is None else self.path.name


@dataclass
class ChangeRecord:
    """One externally-made change we are tracking."""

    path: Path
    status: str  # "added" | "modified" | "deleted"
    baseline_text: Optional[str]
    """Last content the user approved; ``None`` = the file did not exist then."""

    seen_at: float


#: Prefix of "ghost" lines in the inline diff view: content that exists only
#: on the other side (baseline or disk) and is stripped again on save.
GHOST_PREFIX = "⌫"


@dataclass
class Hunk:
    """One contiguous change block between the two sides.

    ``main`` is the content a plain save would keep (green side);
    ``ghost`` is the other side (red, struck-through side). ``decision``:
    ``None`` = pending, ``True`` = accepted (keep ``main``), ``False`` =
    rejected (keep ``ghost``).
    """

    main: tuple[str, ...]
    ghost: tuple[str, ...]
    decision: Optional[bool] = None


def build_blocks(main: list[str], ghost: list[str]) -> list:
    """Split two versions into file-ordered blocks: context (``list[str]``)
    and :class:`Hunk` (contiguous change)."""
    blocks: list = []
    matcher = difflib.SequenceMatcher(a=main, b=ghost, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            blocks.append(list(main[i1:i2]))
        else:
            blocks.append(Hunk(main=tuple(main[i1:i2]), ghost=tuple(ghost[j1:j2])))
    return blocks


def _hunk_take(state: "InlineDiffState", block: Hunk, take_theirs: bool) -> tuple[str, ...]:
    """The side a button decision keeps.

    The two sides are: **theirs** = the external (agent) content, **mine** =
    what the user had. For a clean buffer the user's side is the old
    baseline (``ghost``) and theirs is the disk (``main``); for a dirty
    buffer it is inverted: mine = ``main`` (unsaved edits), theirs =
    ``ghost`` (the agent's lines).
    """
    keep_main = state.clean_before == take_theirs
    return block.main if keep_main else block.ghost


def render_blocks(state: "InlineDiffState") -> tuple[list[str], set[int], set[int]]:
    """Render the block model into view lines.

    Pending hunks show both sides (ghost lines get the :data:`GHOST_PREFIX`,
    the plain-save side is marked green); decided hunks show only the kept
    side (accepted = green, rejected = plain). Returns
    ``(lines, added_indices, ghost_indices)``.
    """
    lines: list[str] = []
    adds: set[int] = set()
    ghosts: set[int] = set()
    for block in state.blocks:
        if not isinstance(block, Hunk):
            lines.extend(block)
            continue
        if block.decision is None:
            for line in block.ghost:
                ghosts.add(len(lines))
                lines.append(f"{GHOST_PREFIX} {line}")
            for line in block.main:
                adds.add(len(lines))
                lines.append(line)
        elif block.decision is True:
            # "theirs" kept: the external content, shown green
            for line in _hunk_take(state, block, True):
                adds.add(len(lines))
                lines.append(line)
        else:
            # "mine" kept: the user's content, back to plain
            lines.extend(_hunk_take(state, block, False))
    return lines, adds, ghosts


def resolved_text(state: "InlineDiffState") -> str:
    """Content a plain save would write.

    Pending hunks keep the ``main`` side — the disk for a clean buffer, the
    user's own text for a dirty one — so a save never loses unsaved edits.
    Decided hunks keep whichever side their button picked.
    """
    parts: list[str] = []
    for block in state.blocks:
        if isinstance(block, Hunk):
            if block.decision is None:
                parts.extend(block.main)
            else:
                parts.extend(_hunk_take(state, block, block.decision))
        else:
            parts.extend(block)
    text = "\n".join(parts)
    return text + "\n" if text else ""


@dataclass
class InlineDiffState:
    """What an inline-diff tab is showing, and how to get back out of it."""

    backup_text: str
    """The buffer content before the diff view took over."""

    language: object
    theme: str
    path: Path
    clean_before: bool
    """Was the buffer clean when the diff view started?"""

    other: tuple[str, ...]
    """The "other side" lines (baseline or disk) at the last review rebuild."""

    blocks: list
    """File-ordered ``list[str]`` context blocks and :class:`Hunk``s."""

    @property
    def hunks(self) -> list[Hunk]:
        return [b for b in self.blocks if isinstance(b, Hunk)]


# --------------------------------------------------------------------------- #
# widgets
# --------------------------------------------------------------------------- #


class Explorer(DirectoryTree):
    """Directory tree for the working directory; dotfiles are hidden."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if not path.name.startswith(".")]


def display_path(path: Path) -> str:
    """Shorten a path for display, using ``~`` for the home directory."""
    home = Path.home()
    if path == home:
        return "~"
    try:
        return "~" + path.relative_to(home).as_posix()
    except ValueError:
        return path.as_posix()


class HunkBar(Vertical):
    """Right-hand panel: one accept/reject row per change block (hunk)."""

    DEFAULT_CSS = """
    HunkBar {
        width: 30;
        border-left: solid $primary-background;
        background: $panel;
        padding: 0 1;
    }
    HunkBar Button {
        border: none !important;
        padding: 0 1 !important;
        height: 1 !important;
        min-width: 0 !important;
    }
    HunkBar .hunk-title {
        text-style: bold;
        color: $text-muted;
        text-overflow: ellipsis;
        margin-bottom: 1;
    }
    HunkBar .hunk-row {
        height: 1;
        margin-bottom: 1;
    }
    HunkBar .hunk-label {
        width: 10;
        color: $text-muted;
        text-overflow: ellipsis;
    }
    HunkBar .hunk-row Button {
        margin-right: 1;
    }
    """



class Statusbar(Static):
    """Slim bottom status line: working dir, file, dirty dot, cursor, size, language."""

    DEFAULT_CSS = """
    Statusbar {
        height: 1;
        dock: bottom;
        background: $panel;
        color: $text-muted;
    }
    """

    def render(self) -> Text:
        app = self.app
        buffers = getattr(app, "buffers", None)
        if buffers is None:
            return Text(" alxedit2", style="bold")
        root = getattr(app, "root", None)
        root_str = f" {display_path(root)}" if root else ""
        area = getattr(app, "active_area", None)
        buf = buffers.get(area) if area is not None else None

        if area is None or buf is None:
            out = Text()
            out.append(root_str, style="bold")
            out.append("  —  pick a file from the explorer", style="dim")
            return out

        file_str = f" ·  {buf.path.name if buf.path is not None else 'untitled'}"
        if buf.modified:
            file_str += " ●"
        n_ext = len(getattr(app, "_changes", {}))
        if n_ext:
            file_str += f"  ⚑{n_ext}"
        left = root_str + file_str
        row, col = area.cursor_location
        lines = area.text.count("\n") + 1
        language = area.language or "plain"
        right = f"ln {row + 1} col {col + 1}  ·  {lines} lines  ·  {language}"

        width = max(self.size.width, 20)
        gap = max(1, width - len(left) - len(right))
        out = Text()
        out.append(root_str, style="bold")
        out.append(file_str, style="bold")
        out.append(" " * gap)
        out.append(right, style="dim")
        return out


# --------------------------------------------------------------------------- #
# modal screens
# --------------------------------------------------------------------------- #


class ConfirmScreen(ModalScreen[Optional[bool]]):
    """A Yes/No prompt; ``None`` is returned when dismissed with Esc/backdrop."""

    CSS = """
    ConfirmScreen {
        align: center middle;
        height: auto;
        width: auto;
        min-width: 44;
    }
    ConfirmScreen .confirm--message {
        width: 100%;
        text-align: center;
        padding: 0 2 2 2;
    }
    ConfirmScreen .confirm--buttons {
        height: 3;
        align: center middle;
    }
    ConfirmScreen Button {
        width: 12;
        margin: 0 1;
    }
    """

    def __init__(self, message: str, confirm_label: str = "Yes") -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        yield Label(self._message, classes="confirm--message")
        with Horizontal(classes="confirm--buttons"):
            yield Button(self._confirm_label, variant="primary", id="confirm")
            yield Button("No", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class SaveAsScreen(ModalScreen[Optional[Path]]):
    """Ask for a path; returns it, or ``None`` on cancel."""

    CSS = """
    SaveAsScreen {
        align: center middle;
        height: auto;
        width: auto;
        min-width: 60;
    }
    SaveAsScreen .saveas--label {
        padding: 0 2 1 2;
    }
    SaveAsScreen .saveas--buttons {
        height: 3;
        align: center middle;
    }
    SaveAsScreen Button {
        width: 10;
        margin: 0 1;
    }
    """

    def __init__(self, initial: str) -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        yield Label("Save as", classes="saveas--label")
        yield Input(value=self._initial, placeholder="path to save to", id="saveas-path")
        with Horizontal(classes="saveas--buttons"):
            yield Button("Save", variant="primary", id="save")
            yield Button("Cancel", id="cancel")

    def _do_save(self) -> None:
        value = self.query_one("#saveas-path", Input).value.strip()
        if value:
            self.dismiss(Path(value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._do_save()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._do_save()


class HelpScreen(ModalScreen[None]):
    """Quick reference for keys and mouse actions."""

    CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen .help--box {
        width: 58;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }
    HelpScreen .help--title {
        text-style: bold;
        text-align: center;
        padding-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        rows = (
            "ctrl+n        new buffer\n"
            "ctrl+s        save (save-as if untitled)\n"
            "f4 / ctrl+w   close tab\n"
            "ctrl+q        quit\n"
            "f2            external changes (review & revert)\n"
            "esc / ctrl+d  abandon a review (keeps your side; reviews\n"
            "              appear automatically when an open file\n"
            "              changes outside)\n"
            "              green = side a save keeps, ⌫ red = the other\n"
            "              right panel: 'theirs' (agent) / 'mine' (yours)\n"
            "              per change block; all decided → editable again\n"
            "f1            this help\n"
            "\n"
            "mouse         click a file in the explorer to open it,\n"
            "              click a folder to expand it, click tabs\n"
            "              to switch, wheel to scroll"
        )
        with Vertical(classes="help--box"):
            yield Label("alxedit2", classes="help--title")
            yield Static(rows)


# --------------------------------------------------------------------------- #
# change tracking screens
# --------------------------------------------------------------------------- #


class _ChangesList(ListView):
    """ListView whose Enter posts a ``ChangeChosen`` event.

    ``ListView`` itself binds Enter to row selection, which would shadow a
    screen-level Enter binding, so we rebind it here and let the screen react
    to the event.
    """

    class ChangeChosen(events.Event):
        """Posted when the user presses Enter on the changes list."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "change_chosen", "Diff", show=False),
    ]

    def action_change_chosen(self) -> None:
        self.post_message(self.ChangeChosen())


class ChangesScreen(Screen):
    """List of changes made to the tree from outside alxedit2.

    enter: review the change in the editor · o: open · r: revert · q/esc: back
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("o", "open", "Open", show=False),
        Binding("r", "revert", "Revert", show=False),
        Binding("q,escape", "back", "Back", show=False),
    ]

    CSS = """
    ChangesScreen #changes-head {
        dock: top;
        height: 3;
        padding: 0 2;
        text-style: bold;
    }
    ChangesScreen #changes-foot {
        dock: bottom;
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    ChangesScreen ListView {
        height: 1fr;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(" external changes — made outside alxedit2 ", id="changes-head")
        yield _ChangesList(id="changes-list")
        yield Label(
            "↑/↓ select · enter = review in editor (esc abandons) · o open · r revert · q back",
            id="changes-foot",
        )

    async def on__changes_list_change_chosen(
        self, event: _ChangesList.ChangeChosen
    ) -> None:
        event.stop()
        await self.action_view_diff()

    def _rebuild(self) -> None:
        app = self.app
        lv = self.query_one("#changes-list", ListView)
        lv.clear()
        records = sorted(app._changes.values(), key=lambda r: r.path)
        if not records:
            lv.append(ListItem(Label("  (no external changes tracked)")))
            return
        for rec in records:
            char, style = {
                "added": ("A", "bold green"),
                "modified": ("M", "bold yellow"),
                "deleted": ("D", "bold red"),
            }[rec.status]
            label = Text()
            label.append(f" {char}  ", style=style)
            label.append(app._rel(rec.path))
            lv.append(ListItem(Label(label)))

    def _selected(self) -> Optional[Path]:
        records = sorted(self.app._changes.values(), key=lambda r: r.path)
        if not records:
            return None
        lv = self.query_one("#changes-list", ListView)
        # In Textual 8 the highlighted index is Optional — it is None until
        # the first up/down press, so fall back to the first record.
        idx = lv.index if lv.index is not None and 0 <= lv.index < len(records) else 0
        return records[idx].path

    async def action_view_diff(self) -> None:
        """Open the file in the editor with an inline diff (esc goes back)."""
        path = self._selected()
        if path is None:
            return
        try:
            await self.app.show_inline_diff(path)
        except (OSError, ValueError) as exc:
            self.app.notify(str(exc), title="Diff", severity="error")
            return
        self.app.pop_screen()

    async def action_open(self) -> None:
        path = self._selected()
        if path is None:
            return
        try:
            await self.app.open_path(path)
        except (OSError, ValueError) as exc:
            self.app.notify(str(exc), title="Open", severity="error")
        self.app.pop_screen()

    def action_revert(self) -> None:
        path = self._selected()
        if path is not None:
            self.app.revert_path(path)

    def action_back(self) -> None:
        self.dismiss()

    def on_mount(self) -> None:
        self.app._subscribe_changes(self._rebuild)
        self._rebuild()

    def on_unmount(self) -> None:
        self.app._unsubscribe_changes(self._rebuild)


# --------------------------------------------------------------------------- #
# app
# --------------------------------------------------------------------------- #


class AlxEditApp(App):
    """A personal IDE-style TUI file editor."""

    CSS = """
    #topbar {
        height: 3;
        dock: top;
        align: left middle;
        border-bottom: solid $primary-background;
    }
    #topbar .sidebar-label {
        width: 20%;
        content-align: center middle;
        text-style: bold;
        color: $text-muted;
    }
    #topbar Button {
        margin: 0 0 0 1;
    }
    #middle {
        height: 1fr;
        layout: horizontal;
    }
    #sidebar {
        width: 20%;
        border-right: solid $primary-background;
    }
    #sidebar Explorer {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    #tabs {
        width: 1fr;
        height: 1fr;
    }
    #hunkbar {
        display: none;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+n", "new_buffer", "New buffer", show=False),
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("ctrl+shift+s", "save_as", "Save as", show=False),
        Binding("f4,ctrl+w", "close_buffer", "Close tab", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
        Binding("f2", "changes", "Changes", show=False),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(
        self,
        root: Path,
        paths: Optional[list[Path]] = None,
        watch_interval: float = 0.8,
    ) -> None:
        super().__init__()
        self.root = root
        self.initial_paths: list[Path] = list(paths or [])
        self.watch_interval = watch_interval
        #: Open buffers, keyed by their TextArea.
        self.buffers: dict[TextArea, Buffer] = {}
        #: TextArea -> the TabPane that hosts it.
        self._panes: dict[TextArea, TabPane] = {}
        self._pane_counter = 0
        #: path -> (size, mtime_ns) as of the last watcher tick.
        self._snap: dict[Path, tuple[int, int]] = {}
        #: path -> last content the user approved (session start / last save).
        self._approved: dict[Path, Optional[str]] = {}
        #: path -> tracked external change.
        self._changes: dict[Path, ChangeRecord] = {}
        #: path -> monotonic time of our own write (self-write guard).
        self._self_writes: dict[Path, float] = {}
        self._change_listeners: set = set()
        self._tree_reload_pending = False
        #: TextArea -> (text, language, show_line_numbers, theme) captured when
        #: the tab shows an inline diff.
        self._inline_diff: dict[TextArea, InlineDiffState] = {}

    # ------------------------------------------------------------------ #
    # layout
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Label("files", classes="sidebar-label")
            yield Button("New", compact=True, id="btn-new")
            yield Button("Save", compact=True, id="btn-save")
            yield Button("Close", compact=True, id="btn-close")
            yield Button("Changes", compact=True, id="btn-changes")
        with Horizontal(id="middle"):
            with Horizontal(id="sidebar"):
                yield Explorer(self.root)
            with Horizontal(id="tabs"):
                yield TabbedContent(id="tabbed")
            yield HunkBar(id="hunkbar")
        yield Statusbar()

    async def on_mount(self) -> None:
        self._init_snapshot()
        for path in self.initial_paths:
            try:
                await self.open_path(path)
            except (OSError, ValueError) as exc:
                self.notify(str(exc), title="Open", severity="error")
        if not self._panes:
            await self.action_new_buffer()
        area = self.active_area
        if area is not None:
            area.focus()
        self.set_interval(self.watch_interval, self._watch_tick)

    # ------------------------------------------------------------------ #
    # lookups
    # ------------------------------------------------------------------ #

    @property
    def _tabbed(self) -> TabbedContent:
        return self.query_one("#tabbed", TabbedContent)

    @property
    def _statusbar(self) -> Statusbar:
        return self.query_one(Statusbar)

    @property
    def active_area(self) -> Optional[TextArea]:
        """The TextArea of the active tab, if any."""
        pane = self._tabbed.active_pane
        if pane is None:
            return None
        areas = pane.query(TextArea)
        return areas[0] if areas else None

    # ------------------------------------------------------------------ #
    # buffers & tabs
    # ------------------------------------------------------------------ #

    async def _add_pane(self, area: TextArea, title: str) -> TabPane:
        self._pane_counter += 1
        pane = TabPane(title, area, id=f"pane-{self._pane_counter}")
        await self._tabbed.add_pane(pane)
        self._panes[area] = pane
        self._activate(pane)
        return pane

    def _activate(self, pane: TabPane) -> None:
        self._tabbed.active = pane.id
        area = self.active_area
        if area is not None:
            area.focus()

    def _retab(self, area: TextArea) -> None:
        """Sync the tab label with the buffer (name + dirty dot)."""
        buf = self.buffers.get(area)
        pane = self._panes.get(area)
        if buf is None or pane is None or not pane.id:
            return
        title = buf.title
        if area in self._inline_diff:
            # While the diff is on screen, show the pre-diff dirty state.
            if buf.clean_at_diff:
                pass
            elif buf.modified:
                title += "●"
        elif buf.modified:
            title += "●"
            title += "●"
        if buf.external:
            title += "†"
        if area in self._inline_diff:
            title += " ⇄"
        try:
            tabs = self._tabbed.get_child_by_type(ContentTabs)
            tabs.get_content_tab(pane.id).update(title)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # file actions
    # ------------------------------------------------------------------ #

    async def open_path(self, path: str | Path) -> TextArea:
        """Open *path* in a tab, or activate its tab if it is already open."""
        target = Path(path).expanduser().resolve()
        if not target.is_file():
            raise FileNotFoundError(f"not a file: {target}")

        for area, buf in self.buffers.items():
            if buf.path is not None and _same_file(buf.path, target):
                self._activate(self._panes[area])
                return area

        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"not a text file: {target}") from exc

        area = TextArea(
            text=text,
            language=language_for_path(target),
            show_line_numbers=True,
            soft_wrap=False,
        )
        self.buffers[area] = Buffer(path=target, saved_text=text)
        await self._add_pane(area, target.name)
        self._statusbar.refresh()
        return area

    async def action_new_buffer(self) -> None:
        area = TextArea(
            placeholder="new buffer — start typing, or pick a file from the explorer",
            show_line_numbers=True,
            soft_wrap=False,
        )
        self.buffers[area] = Buffer(path=None, saved_text="")
        await self._add_pane(area, "untitled")

    def _run_modal(self, coro) -> None:
        """Run a coroutine that may push a modal screen.

        ``push_screen(wait_for_dismiss=True)`` is only allowed from a worker,
        so any action that awaits a modal must go through ``run_worker``.
        """
        self.run_worker(coro, group="modal", exclusive=True)

    def action_save(self) -> None:
        self._run_modal(self._do_save())

    async def _do_save(self) -> None:
        area = self.active_area
        if area is None:
            return
        buf = self.buffers[area]
        if buf.path is None:
            await self._do_save_as()
            return
        # Saving from the diff view is fine: pending hunks resolve to the
        # side a plain save would keep (green + context); decided hunks keep
        # whichever side their button picked.
        state = self._inline_diff.get(area)
        text = resolved_text(state) if state is not None else area.text
        try:
            buf.path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self.notify(str(exc), title="Save", severity="error")
            return
        buf.saved_text = text
        buf.modified = False
        buf.external = False
        self._note_self_write(buf.path, text)
        self._approved[buf.path] = text
        self._changes.pop(buf.path, None)
        if area in self._inline_diff:
            self._leave_inline_diff(area)
        self._retab(area)
        self._statusbar.refresh()
        self.notify(f"saved {buf.path}", title="Save")

    def action_save_as(self) -> None:
        self._run_modal(self._do_save_as())

    async def _do_save_as(self) -> None:
        area = self.active_area
        if area is None:
            return
        buf = self.buffers[area]
        initial = buf.path.name if buf.path is not None else "untitled"
        target = await self.push_screen_wait(SaveAsScreen(initial))
        if target is None:
            return
        target = Path(target).expanduser().resolve()
        state = self._inline_diff.get(area)
        text = resolved_text(state) if state is not None else area.text
        try:
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            self.notify(str(exc), title="Save", severity="error")
            return
        old_path, buf.path = buf.path, target
        buf.saved_text = text
        buf.modified = False
        buf.external = False
        self._note_self_write(target, text)
        self._approved[target] = text
        if old_path is not None:
            self._changes.pop(old_path, None)
        if area in self._inline_diff:
            self._leave_inline_diff(area)
        self._retab(area)
        self._statusbar.refresh()
        self.notify(f"saved {target}", title="Save")

    def action_close_buffer(self) -> None:
        self._run_modal(self._do_close_buffer())

    async def _do_close_buffer(self) -> None:
        area = self.active_area
        if area is None:
            return
        if area in self._inline_diff:
            self._exit_inline_diff(area)
        buf = self.buffers[area]
        pane = self._panes.get(area)
        if pane is None:
            return
        if buf.modified:
            answer = await self.push_screen_wait(
                ConfirmScreen(f"Close {buf.title} without saving?")
            )
            if not answer:
                return
        if len(self._panes) <= 1:
            # Never leave zero tabs: reset to a fresh empty buffer.
            area.load_text("")
            area.language = None
            self.buffers[area] = Buffer(path=None, saved_text="")
            self._retab(area)
            self._statusbar.refresh()
            return
        self._panes.pop(area, None)
        self.buffers.pop(area, None)
        await self._tabbed.remove_pane(pane.id)
        self._statusbar.refresh()

    # ------------------------------------------------------------------ #
    # misc actions
    # ------------------------------------------------------------------ #

    def action_quit(self) -> None:
        self._run_modal(self._do_quit())

    async def _do_quit(self) -> None:
        if any(buf.modified for buf in self.buffers.values()):
            answer = await self.push_screen_wait(
                ConfirmScreen("Unsaved changes — quit anyway?")
            )
            if not answer:
                return
        self.exit()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_changes(self) -> None:
        """Open the external-changes list (inline diff + revert)."""
        self.push_screen(ChangesScreen())

    # --- inline diff ---------------------------------------------------- #

    async def show_inline_diff(self, path: Path) -> None:
        """Open *path* (if needed) and show its tab in the inline diff view."""
        target = Path(path).expanduser().resolve()
        area, buf = self._buffer_for(target)
        if buf is None or area is None:
            area = await self.open_path(target)
        rec = self._changes.get(target)
        if rec is None:
            self.notify("no tracked change for this file", title="Diff")
            return
        self._enter_inline_diff(area, rec)

    def _enter_inline_diff(self, area: TextArea, rec: ChangeRecord) -> None:
        if area in self._inline_diff:
            return
        buf = self.buffers.get(area)
        clean_before = not buf.modified if buf is not None else True
        # One full editor view per file: the side a plain save would keep is
        # the "main" side (green); the other side appears as red, struck-
        # through ghost lines (⌫). Each contiguous change block (hunk) can be
        # accepted or rejected via the hunk bar on the right.
        disk_lines = self._read_lines(rec.path)
        if clean_before:
            main, other = disk_lines, (rec.baseline_text or "").splitlines()
        else:
            main, other = area.text.splitlines(), disk_lines
        self._show_inline_view(area, rec.path, main, other, clean_before)

    def _show_inline_view(
        self,
        area: TextArea,
        path: Path,
        main: list[str],
        other: list[str],
        clean_before: bool,
    ) -> None:
        """Render (or re-render) the inline diff view and the hunk bar."""
        state = self._inline_diff.get(area)
        if state is None:
            state = InlineDiffState(
                backup_text=area.text,
                language=area.language,
                theme=area.theme,
                path=path,
                clean_before=clean_before,
                other=(),
                blocks=[],
            )
            self._inline_diff[area] = state
        state.path = path
        state.other = tuple(other)
        state.blocks = build_blocks(main, other)
        buf = self.buffers.get(area)
        if buf is not None:
            # Remember the pre-diff state: loading the view below would
            # otherwise flag the buffer as modified.
            buf.clean_at_diff = clean_before
        # No grammar (language=None) so Textual's own highlighter stays out of
        # the way; we paint the diff lines ourselves below.
        area.language = None
        self._rerender(area, state)
        area.register_theme(_DIFF_THEME)
        area.theme = "alxdiff"
        self._set_readonly(area, state)
        self._show_hunkbar(True)
        self._refresh_hunkbar()
        self._retab(area)
        self._statusbar.refresh()

    def _rerender(self, area: TextArea, state: InlineDiffState) -> None:
        """Rebuild the editor text + colors from the block model."""
        lines, adds, ghosts = render_blocks(state)
        area.load_text("\n".join(lines) + "\n")
        self._paint_inline_diff(area, adds, ghosts)

    @staticmethod
    def _paint_inline_diff(
        area: TextArea, adds: set[int], ghosts: set[int]
    ) -> None:
        """Paint the inline diff: green = new/kept side, red strike = ghost."""
        highlights = area._highlights
        highlights.clear()
        for index in ghosts:
            highlights[index].append((0, None, "diff_del"))
        for index in adds:
            highlights[index].append((0, None, "diff_add"))

    @staticmethod
    def _set_readonly(area: TextArea, state: InlineDiffState) -> None:
        # While any hunk is pending the tab is under review (the hunk bar
        # decides); once every hunk is decided it is a normal editor again.
        area.is_read_only = any(hunk.decision is None for hunk in state.hunks)

    def _show_hunkbar(self, show: bool) -> None:
        self.query_one("#hunkbar", HunkBar).display = show

    def _refresh_hunkbar(self) -> None:
        """Rebuild the hunk bar rows for the active diff."""
        bar = self.query_one("#hunkbar", HunkBar)
        bar.remove_children()
        area = self.active_area
        state = self._inline_diff.get(area) if area is not None else None
        if state is None:
            return
        pending = sum(1 for hunk in state.hunks if hunk.decision is None)
        bar.mount(Label(f"hunks — {pending} pending", classes="hunk-title"))
        for index, hunk in enumerate(state.hunks):
            bar.mount(
                Horizontal(
                    Label(
                        f"#{index + 1} +{len(hunk.main)} −{len(hunk.ghost)}",
                        classes="hunk-label",
                    ),
                    Button(
                        "theirs",
                        compact=True,
                        variant="success" if hunk.decision else "default",
                        id=f"hunk-{index}-theirs",
                    ),
                    Button(
                        "mine",
                        compact=True,
                        variant="error" if hunk.decision is False else "default",
                        id=f"hunk-{index}-mine",
                    ),
                    classes="hunk-row",
                )
            )

    def _on_hunk_button(self, button_id: str) -> None:
        """Decide one change block from the hunk bar: theirs / mine."""
        parts = button_id.split("-")  # hunk-<n>-theirs | hunk-<n>-mine
        if (
            len(parts) != 3
            or not parts[1].isdigit()
            or parts[2] not in ("theirs", "mine")
        ):
            return
        area = self.active_area
        state = self._inline_diff.get(area) if area is not None else None
        if state is None:
            return
        index = int(parts[1])
        if index >= len(state.hunks):
            return
        state.hunks[index].decision = parts[2] == "theirs"
        self._rerender(area, state)
        if all(hunk.decision is not None for hunk in state.hunks):
            self._finish_review(area, state)
            self.notify("all hunks resolved — tab is editable", title="Diff")
        else:
            self._set_readonly(area, state)
            self._refresh_hunkbar()

    def _finish_review(self, area: TextArea, state: InlineDiffState) -> None:
        """Every hunk decided: drop the diff state; the tab is a live editor."""
        self._inline_diff.pop(area, None)
        area.is_read_only = False
        area.language = state.language
        area.theme = state.theme
        self._show_hunkbar(False)
        self._retab(area)
        self._statusbar.refresh()

    def _exit_inline_diff(self, area: TextArea) -> None:
        """esc / ctrl+d: abandon the review of the inline diff."""
        state = self._inline_diff.pop(area, None)
        if state is None:
            return
        buf = self.buffers.get(area)
        if state.clean_before:
            # Clean buffer: adopt the (externally changed) file on disk.
            disk_text = self._read_text(state.path)
            text = disk_text if disk_text is not None else state.backup_text
            area.load_text(text)
            if buf is not None:
                buf.saved_text = text
                buf.external = False
            self._approved[state.path] = text
            self._changes.pop(state.path, None)
        else:
            area.load_text(state.backup_text)
        area.is_read_only = False
        area.language = state.language
        area.theme = state.theme
        self._show_hunkbar(False)
        self._retab(area)
        self._statusbar.refresh()

    def _leave_inline_diff(self, area: TextArea) -> None:
        """After a save: the resolved content stays; the diff state drops."""
        state = self._inline_diff.pop(area, None)
        if state is None:
            return
        buf = self.buffers.get(area)
        area.load_text(buf.saved_text if buf is not None else "")
        area.is_read_only = False
        area.language = state.language
        area.theme = state.theme
        self._show_hunkbar(False)

    @staticmethod
    def _read_lines(path: Path) -> list[str]:
        try:
            return path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []

    def action_toggle_diff(self) -> None:
        """Toggle the inline diff in the active tab (esc / ctrl+d)."""
        area = self.active_area
        if area is None:
            return
        if area in self._inline_diff:
            self._exit_inline_diff(area)
            return
        buf = self.buffers[area]
        if buf.path is None or buf.path not in self._changes:
            self.notify("no tracked change for this file (f2 lists them)", title="Diff")
            return
        self._enter_inline_diff(area, self._changes[buf.path])

    # ------------------------------------------------------------------ #
    # external change tracking (AI agents, other editors, git, ...)
    # ------------------------------------------------------------------ #

    #: Sub-directories that are never tracked or shown.
    IGNORED_DIRS: ClassVar[frozenset[str]] = frozenset({"__pycache__", "node_modules"})
    #: Files larger than this are ignored by the watcher.
    MAX_TRACK_BYTES: ClassVar[int] = 1_000_000
    #: Writes newer than this (seconds) are assumed to be our own.
    SELF_WRITE_GRACE: ClassVar[float] = 3.0

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def _read_text(self, path: Path) -> Optional[str]:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _iter_tracked_files(self) -> list[Path]:
        out: list[Path] = []
        stack = [self.root]
        while stack:
            directory = stack.pop()
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    if entry.name not in self.IGNORED_DIRS:
                        stack.append(entry)
                elif entry.is_file():
                    try:
                        if entry.stat().st_size <= self.MAX_TRACK_BYTES:
                            out.append(entry)
                    except OSError:
                        pass
        return out

    def _init_snapshot(self) -> None:
        """Capture the starting state — this is what "revert" restores."""
        for path in self._iter_tracked_files():
            try:
                st = path.stat()
            except OSError:
                continue
            self._snap[path] = (st.st_size, st.st_mtime_ns)
            self._approved[path] = self._read_text(path)

    def _watch_tick(self) -> None:
        try:
            seen: dict[Path, tuple[int, int]] = {}
            for path in self._iter_tracked_files():
                try:
                    st = path.stat()
                except OSError:
                    continue
                seen[path] = (st.st_size, st.st_mtime_ns)
            self._process_seen(seen)
        except Exception:
            pass

    def _process_seen(self, seen: dict[Path, tuple[int, int]]) -> None:
        prev = self._snap
        now = time.monotonic()
        modified = [p for p, sig in seen.items() if prev.get(p) != sig]
        deleted = [p for p in prev if p not in seen]
        self._snap = seen
        if not modified and not deleted:
            return
        tree_touched = False
        changed = False
        for path in modified:
            if now - self._self_writes.get(path, -1e9) < self.SELF_WRITE_GRACE:
                continue  # our own save
            if path not in prev:
                tree_touched = True
            self._handle_modified(path, now)
            changed = True
        for path in deleted:
            if now - self._self_writes.get(path, -1e9) < self.SELF_WRITE_GRACE:
                continue  # our own revert (deleting an agent-added file)
            tree_touched = True
            self._handle_deleted(path, now)
            changed = True
        if tree_touched:
            self._refresh_tree()
        if changed:
            self._emit_changes()

    def _handle_modified(self, path: Path, now: float) -> None:
        new_text = self._read_text(path)
        area, buf = self._buffer_for(path)
        if buf is not None and area is not None:
            baseline = buf.saved_text
            # Any external change to an open file immediately swaps the tab to
            # the inline diff — clean or dirty; your content is backed up and
            # comes back with esc / ctrl+d.
            if new_text is not None and new_text == buf.saved_text:
                buf.external = False
            else:
                rec = self._changes.get(path) or ChangeRecord(
                    path, "modified", baseline, now
                )
                self._changes[path] = rec
                if area in self._inline_diff:
                    # The diff is already on screen (agent still writing) —
                    # refresh it live instead of leaving a stale view.
                    state = self._inline_diff[area]
                    disk_lines = self._read_lines(path)
                    if state.clean_before:
                        main, other = disk_lines, list(state.other)
                    else:
                        main = [
                            line
                            for line in area.text.splitlines()
                            if not line.startswith(GHOST_PREFIX)
                        ]
                        other = disk_lines
                    self._show_inline_view(
                        area, path, main, other, state.clean_before
                    )
                else:
                    self._enter_inline_diff(area, rec)
            buf.external = True
            self._retab(area)
        else:
            baseline = self._approved.get(path)
        status = "modified" if (buf is not None or path in self._approved) else "added"
        rec = self._changes.get(path)
        if rec is None:
            self._changes[path] = ChangeRecord(path, status, baseline, now)
            self.notify(f"{status}: {self._rel(path)}", title="external change")
        else:
            rec.status = status
            if buf is not None:
                rec.baseline_text = baseline

    def _handle_deleted(self, path: Path, now: float) -> None:
        area, buf = self._buffer_for(path)
        if buf is not None and area is not None:
            baseline = buf.saved_text
            buf.external = True
            self._retab(area)
        else:
            baseline = self._approved.get(path)
        rec = self._changes.get(path)
        if rec is None:
            if buf is None and baseline is None:
                return  # never approved/tracked; nothing to offer
            self._changes[path] = ChangeRecord(path, "deleted", baseline, now)
            self.notify(f"deleted: {self._rel(path)}", title="external change")
        else:
            rec.status = "deleted"

    def _buffer_for(
        self, path: Path
    ) -> tuple[Optional[TextArea], Optional[Buffer]]:
        for area, buf in self.buffers.items():
            if buf.path is not None and _same_file(buf.path, path):
                return area, buf
        return None, None

    def _refresh_tree(self) -> None:
        if self._tree_reload_pending:
            return
        try:
            tree = self.query_one(Explorer)
        except Exception:
            return
        self._tree_reload_pending = True

        def _reload() -> None:
            self._tree_reload_pending = False
            # reload_node keeps the expanded state; reload() would collapse all.
            tree.reload_node(tree.root)

        self.call_later(_reload)

    def _note_self_write(self, path: Path, text: str) -> None:
        """Record that *we* wrote this path, so the watcher stays quiet."""
        was_new = path not in self._snap
        self._self_writes[path] = time.monotonic()
        self._approved[path] = text
        try:
            st = path.stat()
            self._snap[path] = (st.st_size, st.st_mtime_ns)
        except OSError:
            pass
        self._changes.pop(path, None)
        area, buf = self._buffer_for(path)
        if buf is not None and area is not None and buf.external:
            buf.external = False
            self._retab(area)
        if was_new:
            # New file (or new directory) — make sure the explorer shows it.
            self._refresh_tree()
        self._emit_changes()

    # --- revert --------------------------------------------------------- #

    def revert_path(self, path: Path) -> None:
        self._run_modal(self._do_revert(Path(path).expanduser().resolve()))

    async def _do_revert(self, path: Path) -> None:
        rec = self._changes.get(path)
        if rec is None:
            self.notify("no tracked change for this file", title="Revert")
            return
        area, buf = self._buffer_for(path)
        if buf is not None and buf.modified and rec.status in ("modified", "added"):
            answer = await self.push_screen_wait(
                ConfirmScreen("Revert discards your unsaved edits — continue?")
            )
            if not answer:
                return
        diff_state = None
        if area is not None and area in self._inline_diff:
            diff_state = self._inline_diff.pop(area)
        try:
            if rec.status == "added":
                # File did not exist when we started: revert = delete it.
                if path.exists():
                    path.unlink()
                if buf is not None and area is not None:
                    area.load_text("")
                    buf.saved_text = ""
                    buf.modified = False
                self._approved.pop(path, None)
            else:
                if rec.baseline_text is None:
                    self.notify(
                        "original content was not captured — cannot revert",
                        title="Revert",
                        severity="error",
                    )
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rec.baseline_text, encoding="utf-8")
                if buf is not None and area is not None:
                    area.load_text(rec.baseline_text)
                    buf.saved_text = rec.baseline_text
                    buf.modified = False
                self._approved[path] = rec.baseline_text
        except OSError as exc:
            self.notify(str(exc), title="Revert", severity="error")
            return
        if buf is not None and area is not None:
            if diff_state is not None:
                area.language = diff_state.language
                area.theme = diff_state.theme
            area.is_read_only = False
            buf.external = False
            self._retab(area)
            self._show_hunkbar(False)
        self._changes.pop(path, None)
        # Re-sync the snapshot so the watcher doesn't flag our own write.
        try:
            if path.exists():
                st = path.stat()
                self._snap[path] = (st.st_size, st.st_mtime_ns)
            else:
                self._snap.pop(path, None)
        except OSError:
            pass
        self._self_writes[path] = time.monotonic()
        self.notify(f"reverted {self._rel(path)}", title="Revert")
        self._emit_changes()

    # --- change-list subscribers ----------------------------------------- #

    def _subscribe_changes(self, callback: Callable[[], None]) -> None:
        self._change_listeners.add(callback)

    def _unsubscribe_changes(self, callback: Callable[[], None]) -> None:
        self._change_listeners.discard(callback)

    def _emit_changes(self) -> None:
        for callback in list(self._change_listeners):
            try:
                callback()
            except Exception:
                pass
        try:
            self._statusbar.refresh()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # events
    # ------------------------------------------------------------------ #

    @on(DirectoryTree.FileSelected)
    async def _on_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        try:
            await self.open_path(event.path)
        except (OSError, ValueError) as exc:
            self.notify(str(exc), title="Open", severity="error")

    @on(DirectoryTree.DirectorySelected)
    def _on_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        """Single-clicking a folder expands/collapses it, like a file manager."""
        node = event.node
        if node.is_expanded:
            node.collapse()
        else:
            node.expand()

    @on(TextArea.Changed)
    def _on_text_changed(self, event: TextArea.Changed) -> None:
        area = event.text_area
        buf = self.buffers.get(area)
        if buf is None:
            return
        modified = area.text != buf.saved_text
        if modified != buf.modified:
            buf.modified = modified
            self._retab(area)
        self._statusbar.refresh()

    @on(TextArea.SelectionChanged)
    def _on_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self._statusbar.refresh()

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._statusbar.refresh()

    def on_key(self, event: events.Key) -> None:
        """Handle keys that widgets left unconsumed.

        ``esc`` / ``ctrl+d`` exit the active inline diff view; every key also
        keeps the status bar's ln/col fresh.
        """
        if event.key in ("escape", "ctrl+d", "ctrl+left_square_brace"):
            area = self.active_area
            if area is not None and area in self._inline_diff:
                self._exit_inline_diff(area)
                event.stop()
                event.prevent_default()
        self._statusbar.refresh()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("hunk-"):
            self._on_hunk_button(button_id)
            return
        if button_id == "btn-new":
            await self.action_new_buffer()
        elif button_id == "btn-save":
            self.action_save()
        elif button_id == "btn-close":
            self.action_close_buffer()
        elif button_id == "btn-changes":
            self.action_changes()


def _same_file(a: Path, b: Path) -> bool:
    """True if both paths exist and point at the same file."""
    try:
        return a.samefile(b)
    except OSError:
        return False
