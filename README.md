# alxedit2

A personal IDE-style TUI file editor, built with [Textual](https://textual.textualize.io).

- **File explorer** for the working directory on the left (20% of the screen)
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
| `f4` / `ctrl+w` | Close tab                       |
| `ctrl+q`        | Quit                            |
| `f2`            | External changes (review & revert)|
| `f1`            | Help                            |

## External change tracking (AI-agent aware)

alxedit2 watches the project tree for changes made **outside** the editor —
for example an AI agent writing files while you watch. Every 0.8 s the tree
is scanned; dot-dirs, `__pycache__`/`node_modules`, and files over 1 MB are
ignored, and saves made by alxedit2 itself are never flagged.

When something changes you get a toast, a `⚑n` counter in the status bar,
and a `†` on any open tab whose file diverged from disk.

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
becomes a normal editable editor holding the merged content. Alternatives:

- **`ctrl+s` save** — pending blocks resolve the way a plain save would
  (the green + context lines are written, `⌫` ghosts dropped), so saving
  never loses your unsaved edits;
- **`esc` / `ctrl+d`** — abandon the review: a clean buffer adopts the new
  disk content (approved), a dirty buffer restores your unsaved edits —
  never lost.

If the agent keeps writing, the review **updates live**.

Files that are **not open** yet only appear in the changes list — press
**`f2`** (or the **Changes** button):

| Key     | Action                                                        |
| ------- | ------------------------------------------------------------- |
| `enter` | Open the file and start the diff review in its editor tab   |
| `o`     | Open the file in the editor (plain view)                      |
| `r`     | Revert — restore the original, or delete an agent-added file  |
| `q`     | Back to the editor                                            |

You can also press `ctrl+d`/`esc` in a file directly to toggle its diff
when one is tracked.

"Revert" restores the file to the last content you approved in alxedit2
(session start or your most recent save). Files the agent *added* are
deleted; files it *deleted* are restored. If a tab has unsaved edits,
you are asked before they are discarded.

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
