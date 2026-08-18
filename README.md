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
| `f1`            | Help                            |

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
