# alxedit2

A personal IDE-style TUI file editor, built with [Textual](https://textual.textualize.io).

- **File explorer** for the working directory on the left (24% of the screen), with a
  mouse context menu: **ctrl+click** any file or folder to rename it, delete
  it, or create a new file/folder there
- **Tabbed editor** on the rest of the screen
- **Syntax highlighting** via tree-sitter grammars (see below)
- Mouse-friendly: click files to open them, click folders to expand them,
  click tabs to switch, wheel to scroll

## Installation

**Development (in this repo):**

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/alxedit2
```

**Global install** (so `alxedit2` is on your `PATH` in any directory):

```sh
pipx install /home/alex/devel/alxedit2     # isolated env, recommended
# — or —
pip install /home/alex/devel/alxedit2      # into the active environment
```

After `pipx`, just run `alxedit2` from anywhere.

## Usage

```sh
alxedit2 [PATHS…] [--root DIR]
```

- `PATHS` — files to open, and/or a directory to use as the working directory
  (the first directory given becomes the working directory).
- `--root DIR` — set the working directory explicitly (wins over any
  directory passed in `PATHS`).
- If no directory is given, the working directory is the current one (`pwd`).

The working directory is what the left-hand file explorer is rooted at, and it
is shown at the bottom-left of the status bar.

### Examples

```sh
alxedit2                        # open the current directory
alxedit2 ~/src/myproject        # open a project directory
alxedit2 ~/src/myproject/main.py   # open a file (working dir = pwd)
alxedit2 --root ~/src/myproject main.py   # explicit working dir + a file
```

## Keys

| Key             | Action                          |
| --------------- | ------------------------------- |
| `ctrl+n`        | New buffer                      |
| `ctrl+s`        | Save (save-as if untitled)      |
| `ctrl+shift+s`  | Save as…                        |
| `ctrl+click` (explorer) | File/folder menu — **Rename**, **Delete**, **Track**/**Untrack** (edit `.alxeditrc`), **New file here**, **New folder here**. Files: "here" = its folder. Folders: "here" = inside them |
| `ctrl+shift+n`  | New folder where the cursor is (hotkey alternative) |
| `ctrl+shift+x`  | Delete highlighted folder (hotkey alternative; tracked files inside stay revertable) |
| `f4` / `ctrl+w` | Close tab                       |
| `ctrl+q`        | Quit                            |
| `f2`            | External changes — **approve or reject** them (see below)|
| `s`             | Sessions (open / new / delete)  |
| `ctrl+.`        | Settings — what the mirror tracks (see below) |
| `f1`            | Help                            |

## Sessions

Sessions are optional. When alxedit2 starts, if the working directory
already has a `.alxedit/` folder it offers to open (or create) a **session**
— a sync of the working tree under `.alxedit/<id>/` (a progress bar
shows while files are copied; what counts as “tracked” is configurable,
see [Settings](#settings-alexeditrc)):

```
.alxedit/
└── 20260819-100335-4c1a/
    ├── session.json      # id, label, timestamps
    └── files/            # exact mirror of the tree at that moment
```

The mirror is the **baseline** for everything else: diffs against external
edits, reverts, and the “approved” state all compare against *this session’s*
copy — not memory, not git.

- **Startup** — if the working directory has a `.alxedit/` folder with
  sessions you get a picker: open an existing one (it re-baselines the diff
  on that snapshot), create a new one (fresh mirror, with a progress bar),
  or delete ones you don’t want.
- **No `.alxedit/` folder? Basic editor mode.** alxedit2 does *not* copy
  the tree or track changes — it just runs as a plain editor (explorer,
  tabs, syntax highlighting, save). Use the `s` key / **Session** button to
  start a session later if you want tracked changes.
- **`s` key / “Session” button** — the same picker any time, so you can
  start a new baseline mid-run (e.g. “everything the agent did so far is
  fine, start a fresh session for the next round”) or jump back to an
  older one.
- **Saving** — every save through alxedit2 also updates the mirror, so
  “approved” always means *matches the session snapshot*.

Sessions are plain files: inspect them, `cp -r` one to preserve it,
`rm -rf .alxedit/<id>` to remove one manually. (`.alxedit/` itself is
excluded from the mirror.) They appear in the explorer, which refreshes
on its own when a session is created or deleted, and open **read-only**
in a tab — they *are* the baseline, so saving over one in
place would silently move the baseline; use Save As to copy one out if
you want to keep it. The ctrl+click menu offers no file operations for
entries inside `.alxedit/` (no rename, delete, new-file, or
track/untrack) — only a read-only notice and close.

## Settings (`.alxeditrc`)

The **Settings** button (top bar, or `ctrl+.`) edits the project's
`.alxeditrc` file — plain text, also hand-editable:

```
ignore <path>   never mirror/track this file or folder
track  <path>   mirror/track this dot file/dot folder despite the default
```

The explorer always shows **every** file in the project; these settings
control what the session mirror (the diff/revert baseline) actually
covers:

- regular files and folders are tracked by default;
- dot files and dot folders (`.env`, `.github/`, …) are **not** tracked
  unless listed with `track`;
- `ignore` opts anything out — e.g. a massive image you don't want
  copied into every session.

Paths are relative to the project root, case-insensitive; a folder entry
covers everything below it; `ignore` wins over `track`. Changes apply
immediately (new sessions use them right away; the active session's
change list is re-reconciled).

## External change tracking (AI-agent aware)

> Requires an active session — in basic editor mode (no `.alxedit/` folder)
> there is no baseline, so nothing is tracked.

alxedit2 watches the project tree for changes made **outside** the editor —
for example an AI agent writing files while you watch. Every 0.8 s the tree
is scanned; dot-dirs, `__pycache__`/`node_modules`, and files over 1 MB are
ignored, and saves made by alxedit2 itself are never flagged.

Open tabs carry status marks after the file name: `●` (orange) while the
buffer differs from the session baseline (mirror) — i.e. there is a pending
change not yet committed by a save — `†` (orange) when the file on disk
diverged from it, and `⇄` (blue) while the inline diff review is on screen.

When something changes you get a toast, a `⚑n` counter in the status bar,
and a `+N/-M`
marker next to the file in the explorer — the lines added/removed
relative to the session copy (green `+`, red `-`). A file without a
marker matches the session snapshot exactly; the marker disappears the
moment the file is saved, approved, or rejected.

Every explorer entry also carries a **tracking glyph**: `T` (accent
blue) means the change tracker covers it — it is in the session mirror
— while `○` (dim) means it is untracked (dot files by default, or an
`ignore` rule in `.alxeditrc`). Folders reflect their contents: `T`
when anything inside is tracked, `○` when nothing is. Flip a rule in
Settings — or right from the tree: **ctrl+click an entry and pick
Track / Untrack** — and the glyphs follow. Untracking adds an `ignore`
rule; tracking a dot file/folder adds a `track` rule. Both land in
`.alxeditrc`.

If the file is already open, the tab **immediately switches to a diff
review** — there is no separate diff window. Both sides are painted into
the tab:

- **green line** — the side a plain save would write (the agent's new
  content for a clean buffer, your own lines for a buffer with unsaved
  edits);
- **`⌫` red struck-through line** — a *ghost* line: content that exists
  only on the other side;
- unmarked lines — unchanged context.

A **hunk bar** appears on the right with one row per contiguous change
block:

- **theirs** — adopt the external (agent) side of that block;
- **mine** — keep your side of that block.

The view updates as you decide; once every block is decided, the tab
becomes a normal editable editor holding the merged content. The `●`
dot shows whenever the buffer differs from the session baseline (mirror):
taking the agent's side lights it (buffer now differs from the baseline);
keeping your own side leaves it off (buffer matches the baseline). In
either case `ctrl+s` commits the resolution to disk and the baseline.
Alternatives:

- **`ctrl+s` save** — pending blocks resolve the way a plain save would
  (the green + context lines are written, `⌫` ghosts dropped), so saving
  never loses your unsaved edits;
- **`esc` / `ctrl+d`** — abandon the review: a clean buffer adopts the new
  disk content (approved), a dirty buffer restores your unsaved edits —
  never lost.

If the agent keeps writing, the review **updates live**.

Files that are **not open** yet: just **open them** — clicking them in the
explorer (or giving them on the command line) drops you straight into the
diff review, no hunting needed. The **`f2`** / **Changes** screen still
lists everything the agent touched, and lets you **approve** or **reject**
each one (or all of them at once) — most useful for files it *deleted*
(they can't be opened) or *added* (reject = delete):

| Key     | Action                                                        |
| ------- | ------------------------------------------------------------- |
| `a`     | **Approve** — accept the change (pending until save)           |
| `r`     | **Reject** — restore the baseline (or delete an addition)      |
| `A`     | **Approve all** — accept every tracked change at once          |
| `R`     | **Reject all** — restore every baseline at once                |
| `enter` | Open the file in the diff review                              |
| `o`     | Open the file in the editor (plain view)                      |
| `q`     | Back to the editor                                            |

In a file under review, `ctrl+d`/`esc` exits the diff (see above).

**Approve** accepts the change into the buffer (the tab shows `●` —
pending). The session baseline (mirror) is **not** updated yet; that
happens on **save** (`ctrl+s`), which writes the buffer to disk and
commits it as the new baseline. **Reject** does the opposite: it restores
the file to its content in the active **session** snapshot (the mirror).
Files the agent *added* are deleted on reject; files it *deleted* are
restored; **approving a deletion** drops the baseline copy so the file is
gone for good. Every action asks first, and if a tab has unsaved edits
you are told they will be discarded.

## Syntax highlighting

Highlighting uses Textual's built-in tree-sitter grammars
(installed by `textual[syntax]`). Supported extensions:

- **Python** — `.py`, `.pyi`
- **Rust** — `.rs`
- **Bash** — `.sh`, `.bash`, `.zsh`
- **JavaScript / TypeScript** — `.js`, `.cjs`, `.mjs`, `.jsx`, `.ts`, `.tsx`
  (TypeScript is highlighted with the JavaScript grammar — a close
  approximation, since no TS grammar ships with Textual)
- **JSON** — `.json`
- **TOML** — `.toml`
- **YAML** — `.yaml`, `.yml`
- **Markdown** — `.md`, `.markdown`
- **Go** — `.go`
- **Java** — `.java`
- **SQL** — `.sql`
- **HTML** — `.html`, `.htm`
- **CSS** — `.css`
- **XML** — `.xml`

Anything else opens as plain text.

## Development

```sh
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```
