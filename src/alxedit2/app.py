"""alxedit2 — IDE-style TUI: file explorer sidebar + tabbed, syntax-highlighted editor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Iterable, Optional

from rich.text import Text

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DirectoryTree,
    Input,
    Label,
    Static,
    TabPane,
    TabbedContent,
    TextArea,
)
from textual.widgets._tabbed_content import ContentTabs

from .languages import language_for_path


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

    @property
    def title(self) -> str:
        return "untitled" if self.path is None else self.path.name


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
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+n", "new_buffer", "New buffer", show=False),
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("ctrl+shift+s", "save_as", "Save as", show=False),
        Binding("f4,ctrl+w", "close_buffer", "Close tab", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, root: Path, paths: Optional[list[Path]] = None) -> None:
        super().__init__()
        self.root = root
        self.initial_paths: list[Path] = list(paths or [])
        #: Open buffers, keyed by their TextArea.
        self.buffers: dict[TextArea, Buffer] = {}
        #: TextArea -> the TabPane that hosts it.
        self._panes: dict[TextArea, TabPane] = {}
        self._pane_counter = 0

    # ------------------------------------------------------------------ #
    # layout
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Label("files", classes="sidebar-label")
            yield Button("New", compact=True, id="btn-new")
            yield Button("Save", compact=True, id="btn-save")
            yield Button("Close", compact=True, id="btn-close")
        with Horizontal(id="middle"):
            with Horizontal(id="sidebar"):
                yield Explorer(self.root)
            with Horizontal(id="tabs"):
                yield TabbedContent(id="tabbed")
        yield Statusbar()

    async def on_mount(self) -> None:
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
        title = f"{buf.title}●" if buf.modified else buf.title
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

    async def action_save(self) -> None:
        area = self.active_area
        if area is None:
            return
        buf = self.buffers[area]
        if buf.path is None:
            await self.action_save_as()
            return
        try:
            buf.path.write_text(area.text, encoding="utf-8")
        except OSError as exc:
            self.notify(str(exc), title="Save", severity="error")
            return
        buf.saved_text = area.text
        buf.modified = False
        self._retab(area)
        self._statusbar.refresh()
        self.notify(f"saved {buf.path}", title="Save")

    async def action_save_as(self) -> None:
        area = self.active_area
        if area is None:
            return
        buf = self.buffers[area]
        initial = buf.path.name if buf.path is not None else "untitled"
        target = await self.push_screen_wait(SaveAsScreen(initial))
        if target is None:
            return
        target = Path(target).expanduser().resolve()
        try:
            target.write_text(area.text, encoding="utf-8")
        except OSError as exc:
            self.notify(str(exc), title="Save", severity="error")
            return
        buf.path = target
        buf.saved_text = area.text
        buf.modified = False
        self._retab(area)
        self._statusbar.refresh()
        self.notify(f"saved {target}", title="Save")

    async def action_close_buffer(self) -> None:
        area = self.active_area
        if area is None:
            return
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

    async def action_quit(self) -> None:
        if any(buf.modified for buf in self.buffers.values()):
            answer = await self.push_screen_wait(
                ConfirmScreen("Unsaved changes — quit anyway?")
            )
            if not answer:
                return
        self.exit()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

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
        """Keep the status bar's ln/col fresh as the cursor moves."""
        self._statusbar.refresh()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-new":
            await self.action_new_buffer()
        elif event.button.id == "btn-save":
            await self.action_save()
        elif event.button.id == "btn-close":
            await self.action_close_buffer()


def _same_file(a: Path, b: Path) -> bool:
    """True if both paths exist and point at the same file."""
    try:
        return a.samefile(b)
    except OSError:
        return False
