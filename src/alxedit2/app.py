"""alxedit2 — IDE-style TUI: file explorer sidebar + tabbed, syntax-highlighted editor.

Also tracks changes made to the project tree from *outside* the editor
(e.g. an AI agent writing files) and offers diff + revert for them.
"""

from __future__ import annotations

import asyncio
import difflib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar, Iterable, Optional

from rich.style import Style
from rich.text import Text

from textual import events, on
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
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
    ProgressBar,
    Static,
    TabPane,
    TabbedContent,
    TextArea,
)
from textual.widgets._tabbed_content import ContentTabs

from textual._text_area_theme import TextAreaTheme

from . import sessions
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
    """Editor state for one open tab.

    Two orthogonal status indicators:

    * :attr:`unsaved` — buffer differs from the **disk** (the file name is
      colored red/orange). “Could I lose work if the disk is clobbered?”
    * :attr:`modified` — buffer differs from the **session baseline** (the
      mirror; the ``●`` dot). “Has the session recorded this content?”
    """

    path: Optional[Path]
    """Where the buffer lives on disk, or ``None`` for an untitled buffer."""

    saved_text: str
    """Content at the last save (or open); the anchor for :attr:`unsaved`."""

    baseline: Optional[str] = None
    """Session baseline (mirror) content; ``None`` = no mirror copy yet."""

    unsaved: bool = False
    """Buffer differs from the disk (colored file name)."""

    modified: bool = False
    """Buffer differs from the session baseline (``●`` dot)."""

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
    """Directory tree for the working directory; dotfiles are hidden.

    Files whose on-disk content differs from the session baseline get a
    ``+N/-M`` marker (lines added / lines removed).
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "explorer--marker-add",
        "explorer--marker-del",
    }

    DEFAULT_CSS = """
    Explorer {
        & > .explorer--marker-add {
            color: $success;
            text-style: bold;
        }

        & > .explorer--marker-del {
            color: $error;
            text-style: bold;
        }
    }
    """

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if not path.name.startswith(".")]

    def render_label(self, node, base_style, style):
        """Node label, plus the ``+added/-removed`` change marker if any.

        The file name is colored red/orange while the open buffer is unsaved
        (buffer differs from the disk). When a marker is present we shrink the
        file name (ellipsis) so the score always stays inside the panel — the
        sidebar is narrow and a long file name would otherwise push the marker
        past the edge.
        """
        text = super().render_label(node, base_style, style)
        entry = node.data
        if entry is None:
            return text
        # Colored file name while the open buffer is unsaved (buffer != disk).
        unsaved_paths = getattr(self.app, "_unsaved_paths", None)
        if unsaved_paths and entry.path in unsaved_paths:
            text.stylize("bold #ffa62b")  # mutates in place; returns None
        markers = getattr(self.app, "_tree_markers", None)
        if not markers:
            return text
        counts = markers.get(entry.path)
        if counts is None:
            return text
        added, removed = counts
        marker_parts = [
            (f"  +{added}", "explorer--marker-add"),
            (f"/-{removed}", "explorer--marker-del"),
        ]
        marker_len = sum(len(chunk) for chunk, _ in marker_parts)
        # The tree paints ~4 cells of guide per depth level in front of the
        # label; budget the rest of the panel (minus a cell of slack).
        depth = 0
        ancestor = node
        while ancestor.parent is not None:
            depth += 1
            ancestor = ancestor.parent
        limit = self.size.width - 4 * depth - 1
        if limit >= 2 and len(text) + marker_len > limit:
            keep = limit - marker_len
            if keep >= 3:
                text = text[: keep - 1]
                text.append("…")
            elif keep >= 0:
                text = Text()
        for chunk, name in marker_parts:
            text.append(
                chunk,
                self.get_component_rich_style(
                    name, partial=True, default=Style()
                ),
            )
        return text

    async def _on_click(self, event: events.Click) -> None:
        """Control-click opens the node's context menu (rename / delete /
        new file / new folder) instead of the tree's select/expand.

        Textual dispatches ``_on_click`` up the whole MRO, so ours runs
        before DirectoryTree's; ``prevent_default`` stops the walk so the
        tree's select/expand (and the file open) never happens.
        Plain clicks fall through to the tree's own handler.
        """
        if event.ctrl:
            event.prevent_default()
            node = self.get_node_at_line(event.style.meta.get("line", -1))
            if node is not None:
                self.app.action_node_menu(node)


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
        sid = getattr(app, "session_id", None)
        session_str = ""
        if root is not None and sid:
            try:
                session_str = f"  [{sessions.session_label(root, sid)}]"
            except Exception:
                session_str = ""
        area = getattr(app, "active_area", None)
        buf = buffers.get(area) if area is not None else None

        if area is None or buf is None:
            out = Text()
            out.append(root_str, style="bold")
            out.append(session_str, style="bold")
            out.append("  —  pick a file from the explorer", style="dim")
            return out

        name = buf.path.name if buf.path is not None else "untitled"
        n_ext = len(getattr(app, "_changes", {}))
        left = root_str + session_str + f" ·  {name}"
        if buf.modified:
            left += " ●"
        if n_ext:
            left += f"  ⚑{n_ext}"
        row, col = area.cursor_location
        lines = area.text.count("\n") + 1
        language = area.language or "plain"
        right = f"ln {row + 1} col {col + 1}  ·  {lines} lines  ·  {language}"

        width = max(self.size.width, 20)
        gap = max(1, width - len(left) - len(right))
        out = Text()
        out.append(root_str, style="bold")
        out.append(session_str, style="bold")
        # File name colored red/orange while unsaved (buffer != disk); ● marks
        # a buffer not committed to the session baseline (buffer != mirror).
        out.append(f" ·  {name}", style="bold #ffa62b" if buf.unsaved else "bold")
        if buf.modified:
            out.append(" ●", style="bold #ffa62b")
        if n_ext:
            out.append(f"  ⚑{n_ext}", style="bold")
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


class NamePrompt(ModalScreen[Optional[str]]):
    """Ask for a name (new file/folder, rename); returns it or ``None``."""

    CSS = """
    NamePrompt {
        align: center middle;
        height: auto;
        width: auto;
        min-width: 46;
    }
    NamePrompt .prompt--label {
        padding: 0 2 1 2;
    }
    NamePrompt .prompt--buttons {
        height: 3;
        align: center middle;
    }
    NamePrompt Button {
        width: 10;
        margin: 0 1;
    }
    """

    def __init__(
        self, title: str, placeholder: str = "name", ok_label: str = "Create"
    ) -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._ok_label = ok_label

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="prompt--label")
        yield Input(placeholder=self._placeholder, id="prompt-name")
        with Horizontal(classes="prompt--buttons"):
            yield Button(self._ok_label, variant="primary", id="prompt-ok")
            yield Button("Cancel", id="prompt-cancel")

    def _do_ok(self) -> None:
        value = self.query_one("#prompt-name", Input).value.strip()
        if value:
            # only take the last component — no path escapes
            self.dismiss(Path(value).name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "prompt-ok":
            self._do_ok()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._do_ok()


class NodeMenuScreen(ModalScreen[str]):
    """Context menu for an explorer file or folder (control-click).

    Dismissed with one of: ``"rename"``, ``"delete"``, ``"new-file"``,
    ``"new-folder"``, or ``"cancel"``.
    """

    CSS = """
    NodeMenuScreen {
        align: center middle;
        height: auto;
        width: auto;
        min-width: 56;
    }
    NodeMenuScreen .menu--title {
        padding: 0 2 1 2;
        text-style: bold;
    }
    NodeMenuScreen .menu--buttons {
        height: 3;
        align: center middle;
    }
    NodeMenuScreen Button {
        width: 14;
        margin: 0 1;
    }
    """

    def __init__(self, path: Path, is_root: bool = False) -> None:
        super().__init__()
        self._path = path
        self._is_root = is_root

    def compose(self) -> ComposeResult:
        with Vertical(classes="menu--box"):
            yield Label(f"{display_path(self._path)}", classes="menu--title")
            with Horizontal(classes="menu--buttons"):
                yield Button("Rename", id="menu-rename")
                yield Button("Delete", id="menu-delete", disabled=self._is_root)
            with Horizontal(classes="menu--buttons"):
                yield Button("New file here", id="menu-new-file")
                yield Button("New folder here", id="menu-new-folder")
                yield Button("Cancel", id="menu-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "menu-rename": "rename",
            "menu-delete": "delete",
            "menu-new-file": "new-file",
            "menu-new-folder": "new-folder",
        }
        self.dismiss(mapping.get(event.button.id, "cancel"))


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
            "ctrl+click    file/folder menu — rename, delete, or create\n"
            "              a new file/folder there (mouse-first)\n"
            "ctrl+shift+n  new folder where the cursor is (hotkey)\n"
            "ctrl+shift+x  delete the highlighted folder (hotkey)\n"
            "f2            external changes — approve or reject them\n"
            "              (a approve · r reject · A approve all · R reject all)\n"
            "Session btn   top bar — switch / create / delete sessions\n"
            "              (each session snapshots the folder as the\n"
            "              baseline for diffs & reverts)\n"
            "esc / ctrl+d  abandon a review (keeps your side; reviews\n"
            "              appear automatically when an open file\n"
            "              changes outside)\n"
            "              green = side a save keeps, ⌫ red = the other\n"
            "              right panel: 'theirs' (agent) / 'mine' (yours)\n"
            "              per change block; all decided → editable again;\n"
            "              ● tab = change not in baseline yet — ctrl+s commits it\n"
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
# session screens
# --------------------------------------------------------------------------- #


@dataclass
class SessionChoice:
    """Result of the session picker.

    ``action`` is ``"open"`` (open the selected session), ``"new"`` (create
    a new one, with ``label``), or ``"cancel"`` (no change).
    """

    action: str
    sid: Optional[str] = None
    label: Optional[str] = None


class SyncScreen(ModalScreen[None]):
    """Progress popup shown while the tree is mirrored for a new session."""

    CSS = """
    SyncScreen {
        align: center middle;
        height: auto;
        width: auto;
        min-width: 48;
    }
    SyncScreen .sync--box {
        width: 48;
        border: round $accent;
        padding: 1 2;
    }
    SyncScreen #sync-label {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(self, message: str = "Creating session") -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="sync--box"):
            yield Label(f"{self._message}…", id="sync-label")
            yield ProgressBar(id="sync-progress")

    def update(self, done: int, total: int) -> None:
        bar = self.query_one("#sync-progress", ProgressBar)
        bar.total = total
        bar.progress = done
        self.query_one("#sync-label", Label).update(
            f"{self._message}… {done}/{total} files"
        )


class _SessionList(ListView):
    """ListView whose Enter posts a ``SessionChosen`` event (see
    ``_ChangesList`` for why)."""

    class SessionChosen(events.Event):
        """Posted when the user presses Enter on the session list."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "session_chosen", "Open", show=False),
    ]

    def action_session_chosen(self) -> None:
        self.post_message(self.SessionChosen())


class SessionScreen(ModalScreen[SessionChoice]):
    """Pick an existing session, create a new one, or delete one.

    enter/open  open the highlighted session · new  create a new session
    delete      remove the highlighted one (press twice to confirm)
    esc/cancel  back (at startup: opens the most recent session)
    """

    CSS = """
    SessionScreen #session-head {
        text-style: bold;
        padding: 0 2 1 2;
    }
    SessionScreen ListView {
        height: 1fr;
        max-height: 12;
        padding: 0 1;
    }
    SessionScreen #session-label-input {
        margin: 1 2;
    }
    SessionScreen .session--buttons {
        height: 3;
        align: center middle;
        padding: 0 1;
    }
    SessionScreen Button {
        width: 10;
        margin: 0 1;
    }
    """

    def __init__(
        self, session_list: list[sessions.Session], starting: bool
    ) -> None:
        super().__init__()
        self._sessions = session_list
        self._starting = starting
        self._delete_armed = False

    def compose(self) -> ComposeResult:
        yield Label(
            "sessions — each session is a snapshot of the folder for diffs",
            id="session-head",
        )
        yield _SessionList(id="session-list")
        yield Input(
            id="session-label-input",
            placeholder=f"name for a new session (e.g. session {len(self._sessions) + 1})",
        )
        with Horizontal(classes="session--buttons"):
            yield Button("Open", variant="primary", id="sess-open")
            yield Button("New", id="sess-new")
            yield Button("Delete", id="sess-delete")
            yield Button("Cancel", id="sess-cancel")

    async def on_mount(self) -> None:
        await self._refresh_list()

    async def _refresh_list(self) -> None:
        lv = self.query_one("#session-list", ListView)
        if lv.children:
            await lv.clear()
        app = self.app
        for s in self._sessions:
            label = Text()
            active = app is not None and s.id == getattr(app, "session_id", None)
            label.append(f" {'● ' if active else '  '}", style="bold green")
            label.append(s.label, style="bold" if active else "")
            label.append(f"  ·  {s.created[:16].replace('T', ' ')}", style="dim")
            label.append(f"  ·  {s.file_count} files", style="dim")
            lv.append(ListItem(Label(label), id=f"sess-{s.id}"))
        if self._sessions:
            lv.index = 0

    def _selected_sid(self) -> Optional[str]:
        if not self._sessions:
            return None
        lv = self.query_one("#session-list", ListView)
        idx = (
            lv.index
            if lv.index is not None and 0 <= lv.index < len(self._sessions)
            else 0
        )
        return self._sessions[idx].id

    async def on__session_list_session_chosen(
        self, event: _SessionList.SessionChosen
    ) -> None:
        event.stop()
        self.action_open_selected()

    def action_open_selected(self) -> None:
        sid = self._selected_sid()
        if sid is None:
            self.app.notify("no sessions to open", title="Sessions")
            return
        self.dismiss(SessionChoice("open", sid=sid))

    def action_new(self) -> None:
        label = self.query_one("#session-label-input", Input).value.strip() or None
        self.dismiss(SessionChoice("new", label=label))

    async def action_delete(self) -> None:
        app = self.app
        sid = self._selected_sid()
        if sid is None:
            self.app.notify("no sessions to delete", title="Sessions")
            return
        if sid == getattr(app, "session_id", None):
            self.app.notify("that is the active session — switch first", title="Sessions")
            return
        button = self.query_one("#sess-delete", Button)
        if not self._delete_armed:
            self._delete_armed = True
            button.label = "Sure?"
            button.variant = "warning"
            return
        self._delete_armed = False
        button.label = "Delete"
        button.variant = "default"
        sessions.delete_session(app.root, sid)
        self._sessions = [s for s in self._sessions if s.id != sid]
        await self._refresh_list()
        self.app.notify(f"deleted session {sid}", title="Sessions")

    def action_cancel(self) -> None:
        self.dismiss(SessionChoice("cancel"))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sess-open":
            self.action_open_selected()
        elif event.button.id == "sess-new":
            self.action_new()
        elif event.button.id == "sess-delete":
            await self.action_delete()
        elif event.button.id == "sess-cancel":
            self.action_cancel()


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
    """External changes since session start — approve or reject them.

    a: approve · r: reject · A: approve all · R: reject all
    o: open · enter: review in editor · q/esc: back
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("a", "approve", "Approve", show=False),
        Binding("r", "reject", "Reject", show=False),
        Binding("shift+a", "approve_all", "Approve all", show=False),
        Binding("shift+r", "reject_all", "Reject all", show=False),
        Binding("o", "open", "Open", show=False),
        Binding("q,escape", "back", "Back", show=False),
    ]

    CSS = """
    ChangesScreen {
        layout: vertical;
    }
    ChangesScreen #changes-head {
        height: 3;
        align: left middle;
        padding: 0 2;
        text-style: bold;
    }
    ChangesScreen #changes-allbar {
        height: 3;
        align: left middle;
        padding: 0 2;
    }
    ChangesScreen #changes-allbar Button {
        width: 13;
        margin: 0 1 0 0;
    }
    ChangesScreen ListView {
        height: 1fr;
        padding: 0 1;
    }
    ChangesScreen #changes-itembar {
        height: 3;
        align: right middle;
        padding: 0 2;
    }
    ChangesScreen #changes-itembar Button {
        width: 9;
        margin: 0 0 0 1;
    }
    ChangesScreen #changes-foot {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(" external changes since session start ", id="changes-head")
        with Horizontal(id="changes-allbar"):
            yield Button("Approve all", variant="primary", id="btn-approve-all")
            yield Button("Reject all", variant="error", id="btn-reject-all")
        yield _ChangesList(id="changes-list")
        with Horizontal(id="changes-itembar"):
            yield Button("Approve", variant="primary", id="btn-approve")
            yield Button("Reject", variant="error", id="btn-reject")
        yield Label(
            " a approve · r reject · A approve all · R reject all · o open · enter diff · q back",
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-approve-all":
            self.app.approve_all()
        elif event.button.id == "btn-reject-all":
            self.app.reject_all()
        elif event.button.id == "btn-approve":
            path = self._selected()
            if path is not None:
                self.app.approve_path(path)
        elif event.button.id == "btn-reject":
            path = self._selected()
            if path is not None:
                self.app.reject_path(path)

    def action_approve(self) -> None:
        path = self._selected()
        if path is not None:
            self.app.approve_path(path)

    def action_reject(self) -> None:
        path = self._selected()
        if path is not None:
            self.app.reject_path(path)

    def action_approve_all(self) -> None:
        self.app.approve_all()

    def action_reject_all(self) -> None:
        self.app.reject_all()

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
        width: 24%;
        content-align: center middle;
        text-style: bold;
        color: $text-muted;
    }
    #topbar Button {
        margin: 0 0 0 1;
    }
    #btn-changes {
        transition: color 0.15s, background-color 0.15s;
    }
    #btn-changes.has-changes {
        color: #ffa62b;
        background: #33250a;
        text-style: bold;
    }
    #btn-changes.has-changes .button-label {
        color: #ffa62b;
        text-style: bold;
    }
    #middle {
        height: 1fr;
        layout: horizontal;
    }
    #sidebar {
        width: 24%;
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
        Binding("ctrl+shift+n", "new_folder", "New folder", show=False),
        Binding("ctrl+shift+x", "delete_folder", "Delete folder", show=False),
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
        #: The active session id under ``.alxedit/`` (the diff baseline).
        self.session_id: Optional[str] = None
        #: path -> (size, mtime_ns) as of the last watcher tick.
        self._snap: dict[Path, tuple[int, int]] = {}
        #: path -> tracked external change.
        self._changes: dict[Path, ChangeRecord] = {}
        self._tree_markers: dict[Path, tuple[int, int]] = {}
        #: path -> monotonic time of our own write (self-write guard).
        self._self_writes: dict[Path, float] = {}
        self._change_listeners: set = set()
        self._tree_reload_pending = False
        #: TextArea -> (text, language, show_line_numbers, theme) captured when
        #: the tab shows an inline diff.
        self._inline_diff: dict[TextArea, InlineDiffState] = {}
        #: Areas whose buffer state was just set deliberately (finish-review /
        #: approve). The one spurious TextArea.Changed that follows must not
        #: clobber ``modified`` — it is popped when that event is consumed.
        self._suppress_modified: set = set()

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
            yield Button("Session", compact=True, id="btn-session")
        with Horizontal(id="middle"):
            with Horizontal(id="sidebar"):
                yield Explorer(self.root)
            with Horizontal(id="tabs"):
                yield TabbedContent(id="tabbed")
            yield HunkBar(id="hunkbar")
        yield Statusbar()

    async def on_mount(self) -> None:
        self.set_interval(self.watch_interval, self._watch_tick)
        # Session startup (picker / mirror + progress) may push modals, so it
        # has to run as a worker.
        self._run_modal(self._startup())

    async def _startup(self) -> None:
        """Pick (or create) the active session, then open the initial files."""
        found = sessions.list_sessions(self.root)
        if found:
            choice = await self.push_screen_wait(
                SessionScreen(found, starting=True)
            )
            if choice.action == "new":
                sid = await self._create_session(choice.label)
            elif choice.action == "open" and choice.sid is not None:
                sid = choice.sid
            else:  # cancel: fall back to the most recent session
                sid = found[0].id
        else:
            sid = await self._create_session(None)
        if not sessions.session_dir(self.root, sid).is_dir():
            # e.g. it was deleted while the picker was open
            sid = await self._create_session(None)
        self._activate_session(sid)

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
        self.notify(
            f"session: {sessions.session_label(self.root, sid)}",
            title="Session",
        )

    async def _create_session(self, label: Optional[str]) -> str:
        """Create a session and mirror the tree into it, with a progress popup."""
        sid = sessions.create_session(self.root, label)
        screen = SyncScreen()
        await self.push_screen(screen)
        files = sessions.iter_tracked_files(self.root)
        total = len(files)
        for index, path in enumerate(files):
            try:
                sessions.copy_to_mirror(self.root, sid, path)
            except OSError:
                pass
            screen.update(index + 1, total)
            if index % 64 == 63:
                await asyncio.sleep(0)  # let the progress bar paint
        screen.update(total, total)
        await asyncio.sleep(0)
        screen.dismiss()
        return sid

    def _activate_session(self, sid: str) -> None:
        """Make *sid* the active session (diff baseline = its mirror)."""
        self.session_id = sid
        self._changes.clear()
        self._reconcile_with_mirror()
        self._init_snapshot()
        self._emit_changes()

    def _reconcile_with_mirror(self) -> None:
        """Flag files that already differ from the session copy.

        The watcher only notices changes made *after* it took its snapshot,
        so on activation we compare disk against the mirror to pick up
        edits made while alxedit2 was closed. Afterwards ``_changes`` —
        and thus the explorer markers, the F2 list and the status-bar
        counter — all track one invariant: *the file differs from the
        session copy*.
        """
        sid = self.session_id
        if sid is None:
            return
        now = time.monotonic()
        disk = self._iter_tracked_files()
        disk_set = set(disk)
        for path in disk:
            if path in self._changes:
                continue
            if not self._mirror_exists(path):
                # new file, added after the session snapshot
                self._changes[path] = ChangeRecord(path, "added", None, now)
                continue
            try:
                cur = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binary file: not text-diffable
            base = self._baseline_text(path)
            if base is not None and cur != base:
                self._changes[path] = ChangeRecord(
                    path, "modified", base, now
                )
        # files in the mirror but gone from disk
        files_dir = sessions.session_dir(self.root, sid) / "files"
        if files_dir.is_dir():
            for mfile in sorted(files_dir.rglob("*")):
                if not mfile.is_file():
                    continue
                path = self.root / mfile.relative_to(files_dir)
                if path in disk_set or path in self._changes:
                    continue
                self._changes[path] = ChangeRecord(
                    path,
                    "deleted",
                    sessions.read_mirror_text(self.root, sid, path),
                    now,
                )

    async def _do_session_pick(self) -> None:
        """Top-bar Session button: switch to / create / delete sessions."""
        found = sessions.list_sessions(self.root)
        choice = await self.push_screen_wait(
            SessionScreen(found, starting=False)
        )
        if choice.action == "cancel":
            return
        if choice.action == "new":
            sid = await self._create_session(choice.label)
        else:
            sid = choice.sid
        if sid is None or sid == self.session_id:
            return
        if any(buf.unsaved for buf in self.buffers.values()):
            answer = await self.push_screen_wait(
                ConfirmScreen(
                    "Switch sessions? Unsaved edits in open tabs will be lost."
                )
            )
            if not answer:
                return
        await self._reset_buffers()
        self._activate_session(sid)
        self._refresh_tree()
        self.notify(
            f"session: {sessions.session_label(self.root, sid)}",
            title="Session",
        )

    async def _reset_buffers(self) -> None:
        """Close all tabs but one; reset that one to a fresh untitled buffer."""
        areas = list(self._panes)
        for area in areas[:-1]:
            pane = self._panes.pop(area)
            self.buffers.pop(area, None)
            self._inline_diff.pop(area, None)
            await self._tabbed.remove_pane(pane.id)
        area = areas[-1]
        pane = self._panes[area]
        self._inline_diff.pop(area, None)
        self._tabbed.active = pane.id
        area.load_text("")
        area.language = None
        self.buffers[area] = Buffer(path=None, saved_text="")
        self._retab(area)
        self._statusbar_refresh()

    # ------------------------------------------------------------------ #
    # lookups
    # ------------------------------------------------------------------ #

    @property
    def _tabbed(self) -> TabbedContent:
        return self.query_one("#tabbed", TabbedContent)

    def _statusbar_refresh(self) -> None:
        """Refresh the status bar, if it is mounted yet.

        Startup runs as a worker alongside the screen's mount, so early
        events (e.g. the first ``TabActivated``) can land before the
        status bar exists.
        """
        try:
            self.query_one(Statusbar).refresh()
        except NoMatches:
            pass

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
        """Sync the tab label with the buffer (name + status marks).

        The file name is colored red/orange while **unsaved** (buffer
        differs from the disk); a bold ``●`` marks a buffer that is **not
        committed** to the session baseline (buffer differs from the
        mirror); ``†`` marks a file that changed on disk outside the
        editor; ``⇄`` marks the inline diff review in progress.
        """
        buf = self.buffers.get(area)
        pane = self._panes.get(area)
        if buf is None or pane is None or not pane.id:
            return
        theme = self.current_theme
        # Colored file name while unsaved (buffer != disk).
        name_style = Style(bold=True, color=theme.warning) if buf.unsaved else None
        label = Text(buf.title, style=name_style)
        # ● while not committed to the session baseline (buffer != mirror).
        # While the diff is on screen, show the pre-diff dirty state.
        dirty = buf.modified and (
            area not in self._inline_diff or not buf.clean_at_diff
        )
        if dirty:
            label.append(" ●", Style(bold=True, color=theme.warning))
        if buf.external:
            label.append("†", Style(bold=True, color=theme.warning))
        if area in self._inline_diff:
            label.append(" ⇄", Style(bold=True, color=theme.primary))
        try:
            tabs = self._tabbed.get_child_by_type(ContentTabs)
            tabs.get_content_tab(pane.id).label = label
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
        buf = self.buffers[area]
        rec = self._changes.get(target)
        if rec is not None:
            # Opening a file with a pending external change goes straight
            # into the diff review — no separate “changes” step to find it.
            if self.session_id is not None:
                buf.baseline = sessions.read_mirror_text(self.root, self.session_id, target)
            self._enter_inline_diff(area, rec)
        else:
            # No pending change: load the session baseline (mirror). The two
            # indicators fall out — color (buffer != disk) and dot (buffer !=
            # baseline).
            if self.session_id is not None:
                buf.baseline = sessions.read_mirror_text(self.root, self.session_id, target)
        self._recompute_flags(buf, area)
        self._retab(area)
        self._statusbar_refresh()
        return area

    async def action_new_buffer(self) -> None:
        area = TextArea(
            placeholder="new buffer — start typing, or pick a file from the explorer",
            show_line_numbers=True,
            soft_wrap=False,
        )
        self.buffers[area] = Buffer(path=None, saved_text="")
        await self._add_pane(area, "untitled")

    # explorer node actions (mouse menu + hotkeys)
    # ------------------------------------------------------------------ #

    def _current_node(self):
        """Explorer node the cursor is on, or the root as a fallback."""
        tree = self.query_one(Explorer)
        return tree.cursor_node if tree.cursor_node is not None else tree.root

    def _parent_dir(self, node) -> tuple[Path, object]:
        """Directory that *node* lives in (itself if a folder), plus the
        node whose listing must be reloaded to show new children."""
        if node.data is not None and node.data.path.is_dir():
            return node.data.path, node
        parent = node.parent if node.parent is not None else node
        return (
            parent.data.path if parent.data is not None else self.root,
            parent,
        )

    def action_node_menu(self, node=None) -> None:
        """Open the context menu for *node* (or the cursor's node)."""
        tree = self.query_one(Explorer)
        if node is None:
            node = self._current_node()
        self._run_modal(self._do_node_menu(node, tree.root is node))

    async def _do_node_menu(self, node, is_root: bool) -> None:
        choice = await self.push_screen_wait(NodeMenuScreen(node.data.path, is_root))
        if choice == "rename":
            await self._do_node_rename(node)
        elif choice == "delete":
            await self._do_node_delete(node)
        elif choice in ("new-file", "new-folder"):
            parent, list_node = self._parent_dir(node)
            if choice == "new-file":
                await self._do_new_file(parent, list_node)
            else:
                await self._do_new_folder(parent, list_node)

    async def _do_new_file(self, parent: Path, list_node) -> None:
        name = await self.push_screen_wait(
            NamePrompt(
                f"New file in {display_path(parent)}",
                placeholder="file name (e.g. notes.txt)",
            )
        )
        if not name:
            return
        target = parent / name
        try:
            target.touch()
        except OSError as exc:
            self.notify(str(exc), title="New file", severity="error")
            return
        self._watch_tick()  # session-tracks the new file
        self.query_one(Explorer).reload_node(list_node)
        self.notify(f"created {self._rel(target)}", title="New file")
        try:
            await self.open_path(target)
        except (OSError, ValueError):
            pass  # created on disk; opening is a bonus

    async def _do_new_folder(self, parent: Path, list_node) -> None:
        name = await self.push_screen_wait(
            NamePrompt(
                f"New folder in {display_path(parent)}",
                placeholder="folder name",
            )
        )
        if not name:
            return
        target = parent / name
        try:
            target.mkdir()
        except OSError as exc:
            self.notify(str(exc), title="New folder", severity="error")
            return
        # DirectoryTree does not watch the filesystem; reload this node's
        # listing so the new folder shows up (expanded state is kept).
        self.query_one(Explorer).reload_node(list_node)
        self.notify(f"created {self._rel(target)}/", title="New folder")

    async def _do_node_rename(self, node) -> None:
        src = node.data.path
        name = await self.push_screen_wait(
            NamePrompt(f"Rename {display_path(src)}", placeholder="new name")
        )
        if not name or name == src.name:
            return
        dst = src.parent / name
        if dst.exists():
            self.notify(
                f"{dst.name} already exists there", title="Rename", severity="error"
            )
            return
        area, buf = self._buffer_for(src)
        if buf is not None:
            self.notify(
                f"{src.name} is open in a tab — close it before renaming",
                title="Rename",
            )
            return
        try:
            src.rename(dst)
        except OSError as exc:
            self.notify(str(exc), title="Rename", severity="error")
            return
        # the old path is gone and the new path is untracked — re-baseline
        self._watch_tick()
        self._refresh_tree_markers()
        tree = self.query_one(Explorer)
        if node.parent is not None:
            tree.reload_node(node.parent)
        self.notify(f"renamed to {self._rel(dst)}", title="Rename")

    async def _do_node_delete(self, node) -> None:
        target = node.data.path
        if target == self.root:
            self.notify("the project root cannot be deleted", title="Delete")
            return
        area, buf = self._buffer_for(target)
        if buf is not None and buf.unsaved:
            self.notify(
                f"{target.name} is open with unsaved changes — close it first",
                title="Delete",
            )
            return
        message = (
            f"Delete {self._rel(target)}/ and everything inside it?"
            if target.is_dir()
            else f"Delete {self._rel(target)}?"
        )
        if target.is_dir():
            message += " (tracked files stay revertable)"
        ok = await self.push_screen_wait(ConfirmScreen(message))
        if not ok:
            return
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            self.notify(str(exc), title="Delete", severity="error")
            return
        # Poll once so tracked files are flagged as deleted (and stay
        # revertable from the session copy); refresh the parent's listing.
        self._watch_tick()
        tree = self.query_one(Explorer)
        if node.parent is not None:
            tree.reload_node(node.parent)
        self._refresh_tree_markers()
        self.notify(f"deleted {self._rel(target)}", title="Delete")

    # hotkey shims (mouse menu is the primary way; these keep working)

    def action_new_folder(self) -> None:
        node = self._current_node()
        parent, list_node = self._parent_dir(node)
        self._run_modal(self._do_new_folder(parent, list_node))

    def action_delete_folder(self) -> None:
        node = self._current_node()
        if node.data is None or not node.data.path.is_dir():
            self.notify(
                "put the cursor on a folder in the explorer first",
                title="Delete folder",
            )
            return
        self._run_modal(self._do_node_delete(node))

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
        buf.baseline = text  # mirror now equals the saved content
        buf.unsaved = False
        buf.modified = False
        buf.external = False
        self._note_self_write(buf.path, text)  # also updates the session mirror
        self._changes.pop(buf.path, None)
        if area in self._inline_diff:
            self._leave_inline_diff(area)
        self._retab(area)
        self._emit_changes()
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
        buf.baseline = text  # mirror now equals the saved content
        buf.unsaved = False
        buf.modified = False
        buf.external = False
        self._note_self_write(target, text)  # also updates the session mirror
        if old_path is not None:
            self._changes.pop(old_path, None)
        if area in self._inline_diff:
            self._leave_inline_diff(area)
        self._retab(area)
        self._emit_changes()
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
        if buf.unsaved:
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
            self._statusbar_refresh()
            return
        self._panes.pop(area, None)
        self.buffers.pop(area, None)
        await self._tabbed.remove_pane(pane.id)
        self._statusbar_refresh()

    # ------------------------------------------------------------------ #
    # misc actions
    # ------------------------------------------------------------------ #

    def action_quit(self) -> None:
        self._run_modal(self._do_quit())

    async def _do_quit(self) -> None:
        if any(buf.unsaved for buf in self.buffers.values()):
            answer = await self.push_screen_wait(
                ConfirmScreen("Unsaved changes — quit anyway?")
            )
            if not answer:
                return
        self.exit()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_changes(self) -> None:
        """Open the external-changes list (approve / reject / review)."""
        self.push_screen(ChangesScreen())

    def action_sessions(self) -> None:
        """Top-bar Session button: switch to / create / delete sessions."""
        self._run_modal(self._do_session_pick())

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
        clean_before = not buf.unsaved if buf is not None else True
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
        self._statusbar_refresh()

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
            was_tracked = state.path in self._changes
            self._finish_review(area, state)
            buf = self.buffers.get(area)
            if buf is not None and buf.modified:
                self.notify(
                    "all hunks resolved — ●: not in baseline yet; ctrl+s commits",
                    title="Diff",
                )
            elif was_tracked:
                self.notify("all hunks resolved — committed to baseline", title="Diff")
            else:
                self.notify("all hunks resolved — matches baseline", title="Diff")
        else:
            self._set_readonly(area, state)
            self._refresh_hunkbar()

    def _finish_review(self, area: TextArea, state: InlineDiffState) -> None:
        """Every hunk decided: settle the decision and drop the diff state."""
        self._inline_diff.pop(area, None)
        area.is_read_only = False
        area.language = state.language
        area.theme = state.theme
        self._show_hunkbar(False)
        buf = self.buffers.get(area)
        if buf is None:
            self._retab(area)
            self._statusbar_refresh()
            return

        if state.path in self._changes:
            # Approving is a commit: the resolved content becomes the session
            # baseline (mirror) immediately — disk + mirror + buffer all agree,
            # the unsaved dot clears, and the change leaves the pending list.
            text = area.text
            try:
                state.path.write_text(text, encoding="utf-8")
            except OSError as exc:
                self.notify(str(exc), title="Approve", severity="error")
                return
            buf.saved_text = text
            buf.baseline = text  # mirror now equals the resolved content
            buf.unsaved = False
            buf.modified = False
            buf.external = False
            self._note_self_write(state.path, text)  # mirror + snapshot + pop + emit
            self._retab(area)
            return

        # No tracked change — a plain review; both indicators reflect the
        # buffer against the disk and the session baseline.
        buf.baseline = self._baseline_text(state.path)
        buf.external = False
        self._suppress_modified.add(area)
        self._recompute_flags(buf, area)
        self._retab(area)
        self._statusbar_refresh()

    def _exit_inline_diff(self, area: TextArea) -> None:
        """esc / ctrl+d / tab close: abandon the review of the inline diff.

        Abandoning is not a decision: the change stays pending in the
        ledger and the session mirror is untouched — only an explicit
        resolution (all hunks decided), approve+save, or reject settles
        it. A clean buffer still adopts the disk content as working text;
        the dot then rides along until ctrl+s commits it (or a reject
        reverts it).
        """
        state = self._inline_diff.pop(area, None)
        if state is None:
            return
        buf = self.buffers.get(area)
        if state.clean_before:
            # Clean buffer: adopt the (externally changed) file on disk —
            # as working text only. NOT a commit: the mirror and the
            # pending ledger are left exactly as they were.
            disk_text = self._read_text(state.path)
            text = disk_text if disk_text is not None else state.backup_text
            area.load_text(text)
            if buf is not None:
                buf.saved_text = text
                buf.external = False
                self._recompute_flags(buf, area)
        else:
            area.load_text(state.backup_text)
            if buf is not None:
                self._recompute_flags(buf, area)
        area.is_read_only = False
        area.language = state.language
        area.theme = state.theme
        self._show_hunkbar(False)
        self._retab(area)
        self._emit_changes()

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

    def _baseline_text(self, path: Path) -> Optional[str]:
        """The session-approved content of *path* (its mirror), if any."""
        if self.session_id is None:
            return None
        return sessions.read_mirror_text(self.root, self.session_id, path)

    def _mirror_write(self, path: Path, text: str) -> None:
        """Update the session mirror after content the user approved."""
        if self.session_id is None:
            return
        try:
            sessions.write_mirror(self.root, self.session_id, path, text)
        except (OSError, ValueError):
            pass

    def _iter_tracked_files(self) -> list[Path]:
        return sessions.iter_tracked_files(self.root)

    def _init_snapshot(self) -> None:
        """Capture the starting state (stat sigs for the change watcher).

        The content baseline lives in the session mirror on disk.
        """
        for path in self._iter_tracked_files():
            try:
                st = path.stat()
            except OSError:
                continue
            self._snap[path] = (st.st_size, st.st_mtime_ns)

    def _watch_tick(self) -> None:
        if self.session_id is None:
            return  # no active session yet (startup still running)
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
            baseline = self._baseline_text(path)
        has_baseline = buf is not None or self._mirror_exists(path)
        status = "modified" if has_baseline else "added"
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
            baseline = self._baseline_text(path)
        rec = self._changes.get(path)
        if rec is None:
            if buf is None and baseline is None and not self._mirror_exists(path):
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

    def _mirror_exists(self, path: Path) -> bool:
        if self.session_id is None:
            return False
        return sessions.mirror_exists(self.root, self.session_id, path)

    def _refresh_tree_markers(self) -> None:
        """Recompute the explorer's ``+N/-M`` markers and repaint the tree.

        Markers show, per file, how many lines the on-disk content adds /
        removes relative to the session copy (the diff baseline). A file
        without a marker matches the session copy exactly.
        """
        markers: dict[Path, tuple[int, int]] = {}
        for path in self._changes:
            disk = self._read_text(path)
            base = self._baseline_text(path)
            added, removed = self._diff_counts(
                "" if disk is None else disk, base or ""
            )
            if added or removed:
                markers[path] = (added, removed)
        self._tree_markers = markers
        try:
            tree = self.query_one(Explorer)
            # Tree caches its rendered lines (label text included) at build
            # time; a plain refresh() repaints the stale cache. _invalidate()
            # rebuilds the lines, which re-reads our dynamic render_label().
            tree._invalidate()
        except Exception:
            pass  # tree not mounted yet

    @staticmethod
    def _diff_counts(new_text: str, old_text: str) -> tuple[int, int]:
        """Lines added / removed going from *old_text* to *new_text*."""
        added = removed = 0
        matcher = difflib.SequenceMatcher(
            None, old_text.splitlines(), new_text.splitlines(), autojunk=False
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("replace", "delete"):
                removed += i2 - i1
            if tag in ("replace", "insert"):
                added += j2 - j1
        return added, removed

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
        self._mirror_write(path, text)  # user-approved content -> baseline
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

    # --- approve / reject (F2) ------------------------------------------- #

    def revert_path(self, path: Path) -> None:
        """Revert *path* to its baseline (programmatic, no confirmation).

        Kept for back-compat / tests; the UI uses :meth:`reject_path`.
        """
        self._run_modal(self._do_revert(Path(path).expanduser().resolve()))

    async def _do_revert(self, path: Path) -> None:
        """Core reject: restore the baseline (mirror) state. No confirmation."""
        rec = self._changes.get(path)
        if rec is None:
            self.notify("no tracked change for this file", title="Revert")
            return
        if self.session_id is None:
            self.notify("no active session", title="Revert", severity="error")
            return
        area, buf = self._buffer_for(path)
        diff_state = None
        if area is not None and area in self._inline_diff:
            diff_state = self._inline_diff.pop(area)
        # The baseline is the session mirror: restore it (or delete the file
        # if the mirror has no copy — it did not exist when the session
        # started).
        try:
            if not self._mirror_exists(path):
                if path.exists():
                    path.unlink()
                if buf is not None and area is not None:
                    area.load_text("")
                    buf.saved_text = ""
                    buf.baseline = None
                    buf.unsaved = False
                    buf.modified = False
            else:
                data = sessions.read_mirror_bytes(self.root, self.session_id, path)
                if data is None:
                    self.notify(
                        "mirror copy is missing — cannot revert",
                        title="Revert",
                        severity="error",
                    )
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                if buf is not None and area is not None:
                    text = self._read_text(path)
                    if text is not None:
                        area.load_text(text)
                        buf.saved_text = text
                    buf.baseline = text if text is not None else ""
                    buf.unsaved = False
                    buf.modified = False
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
        self.notify(f"rejected {self._rel(path)}", title="Reject")
        self._emit_changes()

    def reject_path(self, path: Path) -> None:
        """Reject an external change (F2 → Reject): confirm, then revert."""
        self._run_modal(self._do_reject(Path(path).expanduser().resolve()))

    async def _do_reject(self, path: Path) -> None:
        rec = self._changes.get(path)
        if rec is None:
            self.notify("no tracked change for this file", title="Reject")
            return
        if self.session_id is None:
            self.notify("no active session", title="Reject", severity="error")
            return
        _area, buf = self._buffer_for(path)
        unsaved = (
            buf is not None
            and buf.unsaved
            and rec.status in ("modified", "added")
        )
        if unsaved:
            msg = (
                f"Reject {self._rel(path)}?\n"
                "Unsaved edits in this tab will be discarded."
            )
        else:
            msg = f"Reject {self._rel(path)}?\n(restore the approved baseline)"
        answer = await self.push_screen_wait(ConfirmScreen(msg, "Reject"))
        if not answer:
            return
        await self._do_revert(path)

    def approve_path(self, path: Path) -> None:
        """Approve an external change (F2 → Approve): confirm, then adopt."""
        self._run_modal(self._do_approve(Path(path).expanduser().resolve()))

    async def _do_approve(self, path: Path) -> None:
        rec = self._changes.get(path)
        if rec is None:
            self.notify("no tracked change for this file", title="Approve")
            return
        if self.session_id is None:
            self.notify("no active session", title="Approve", severity="error")
            return
        answer = await self.push_screen_wait(
            ConfirmScreen(
                f"Approve {self._rel(path)}?\n(accept the current file on disk)",
                "Approve",
            )
        )
        if not answer:
            return
        self._apply_approve(path, rec)

    def approve_all(self) -> None:
        """Approve every tracked change (F2 → Approve all)."""
        self._run_modal(self._do_approve_all())

    async def _do_approve_all(self) -> None:
        if not self._changes:
            self.notify("no tracked changes", title="Approve all")
            return
        if self.session_id is None:
            self.notify("no active session", title="Approve all", severity="error")
            return
        n = len(self._changes)
        plural = "s" if n != 1 else ""
        answer = await self.push_screen_wait(
            ConfirmScreen(
                f"Approve all {n} change{plural}?\n"
                "(accept every current file on disk)",
                "Approve all",
            )
        )
        if not answer:
            return
        for path, rec in list(self._changes.items()):
            self._apply_approve(path, rec)

    def reject_all(self) -> None:
        """Reject every tracked change (F2 → Reject all)."""
        self._run_modal(self._do_reject_all())

    async def _do_reject_all(self) -> None:
        if not self._changes:
            self.notify("no tracked changes", title="Reject all")
            return
        if self.session_id is None:
            self.notify("no active session", title="Reject all", severity="error")
            return
        n = len(self._changes)
        plural = "s" if n != 1 else ""
        msg = f"Reject all {n} change{plural}?\n(restore every approved baseline)"
        dirty: list[str] = []
        for path, rec in self._changes.items():
            if rec.status in ("modified", "added"):
                _area, buf = self._buffer_for(path)
                if buf is not None and buf.unsaved:
                    dirty.append(self._rel(path))
        if dirty:
            msg += "\nUnsaved edits in: " + ", ".join(dirty) + " will be discarded."
        answer = await self.push_screen_wait(ConfirmScreen(msg, "Reject all"))
        if not answer:
            return
        for path in list(self._changes):
            await self._do_revert(path)

    def _apply_approve(self, path: Path, rec: ChangeRecord) -> None:
        """Accept a change: adopt it, but only as *pending* work.

        Approving settles the change out of the pending list and adopts the
        on-disk (agent's) content into any open buffer, but it does NOT
        update the session baseline (mirror): the ``●`` dot stays on until
        the user saves (which commits the buffer to the mirror) or reverts.
        If the user has their own unsaved edits, those are kept instead.
        Adopting a *deletion* is itself a commit (there is no buffer content
        to save), so the mirror copy is dropped right away.
        """
        area, buf = self._buffer_for(path)
        diff_state = None
        if area is not None and area in self._inline_diff:
            diff_state = self._inline_diff.pop(area)
        try:
            if rec.status == "deleted":
                # Adopt the deletion: drop the baseline copy.
                sessions.delete_mirror(self.root, self.session_id, path)
                self._snap.pop(path, None)
            else:
                # added / modified: pending — the baseline (mirror) is only
                # updated on save. Keep the buffer's anchor on the mirror.
                if path.exists():
                    st = path.stat()
                    self._snap[path] = (st.st_size, st.st_mtime_ns)
                if buf is not None:
                    buf.baseline = self._baseline_text(path)
        except (OSError, ValueError) as exc:
            self.notify(str(exc), title="Approve", severity="error")
            return
        self._self_writes[path] = time.monotonic()
        if buf is not None and area is not None:
            if diff_state is not None:
                area.language = diff_state.language
                area.theme = diff_state.theme
            area.is_read_only = False
            if rec.status == "deleted":
                # Adopt the deletion: empty the buffer, clear both indicators.
                area.load_text("")
                buf.saved_text = ""
                buf.baseline = None
                buf.unsaved = False
                buf.modified = False
            else:
                # Clean or external buffer: adopt the disk content as working
                # text; the dot stays on (baseline = mirror) until save.
                if buf.external or not buf.unsaved:
                    disk = self._read_text(path)
                    if disk is not None:
                        area.load_text(disk)
                        buf.saved_text = disk
                buf.external = False
                self._recompute_flags(buf, area)
            self._retab(area)
            self._show_hunkbar(False)
            self._statusbar_refresh()
        self._changes.pop(path, None)
        self.notify(f"approved {self._rel(path)}", title="Approve")
        self._emit_changes()

    # --- change-list subscribers ----------------------------------------- #

    def _subscribe_changes(self, callback: Callable[[], None]) -> None:
        self._change_listeners.add(callback)

    def _unsubscribe_changes(self, callback: Callable[[], None]) -> None:
        self._change_listeners.discard(callback)

    def _refresh_changes_button(self) -> None:
        """Highlight the Changes button while the session has pending items."""
        try:
            btn = self.query_one("#btn-changes", Button)
        except NoMatches:
            return
        btn.set_class(bool(self._changes), "has-changes")

    def _emit_changes(self) -> None:
        # Open buffers whose content differs from the disk (colored file name
        # in the explorer).
        self._unsaved_paths = {
            buf.path for buf in self.buffers.values()
            if buf.path is not None and buf.unsaved
        }
        self._refresh_tree_markers()
        self._refresh_changes_button()
        for callback in list(self._change_listeners):
            try:
                callback()
            except Exception:
                pass
        try:
            self._statusbar_refresh()
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

    def _recompute_flags(self, buf: Buffer, area: TextArea) -> None:
        """Recompute the two status flags from the current buffer text.

        * ``unsaved`` (colored file name): buffer differs from the disk.
        * ``modified`` (``●`` dot): buffer differs from the session
          baseline (mirror); with no mirror copy yet the dot stays on for
          any path-backed buffer (nothing has been committed).
        """
        text = area.text
        buf.unsaved = (text != buf.saved_text)
        if buf.baseline is None:
            buf.modified = (buf.path is not None)
        else:
            buf.modified = (text != buf.baseline)

    @on(TextArea.Changed)
    def _on_text_changed(self, event: TextArea.Changed) -> None:
        area = event.text_area
        buf = self.buffers.get(area)
        if buf is None:
            return
        # A deliberate state change (finish-review / approve) just set the
        # flags; the TextArea.Changed that loading the resolved text
        # triggered must not clobber them with a stale anchor.
        if area in self._suppress_modified:
            self._suppress_modified.discard(area)
            self._statusbar_refresh()
            return
        before = (buf.unsaved, buf.modified)
        self._recompute_flags(buf, area)
        if (buf.unsaved, buf.modified) != before:
            self._retab(area)
        self._statusbar_refresh()

    @on(TextArea.SelectionChanged)
    def _on_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self._statusbar_refresh()

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._statusbar_refresh()

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
        self._statusbar_refresh()

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
        elif button_id == "btn-session":
            self.action_sessions()


def _same_file(a: Path, b: Path) -> bool:
    """True if both paths exist and point at the same file."""
    try:
        return a.samefile(b)
    except OSError:
        return False
