# cw

`cw` is a Claude Code worktree session launcher.

This repository is the Python/Typer port of the existing Bash tool at
`/Users/alexp/bin/cw`. The Bash script remains the source of truth until this
port reaches parity and is installed with `uv tool install`.

## Development

```sh
uv run cw --help
uv run pytest
```

Run `uv run cw dash` to open the Textual session browser. It
shows the full repo path, a filterable worktree list, selected-session details,
and bracketed keyboard shortcuts for common actions. Press `[n]` to open an
in-TUI new-session form with defaults visible up front; `[ctrl+enter]` creates
from that form. Press `[/]` to filter and `[enter]` or `[esc]` to return to the
session table.

Longer-term product ideas live in `docs/ideas.md`, including attached repo
management and optional GitHub/Linear/Jira-style integrations.

TUI layout and flow sketches live in `docs/tui-mocks.md`.

Install the development version as a uv tool:

```sh
uv tool install --editable /Users/alexp/Projects/cw
```

The current Bash `/Users/alexp/bin/cw` should stay in place until the uv tool
install is verified and PATH ordering is intentional.

## Shared State

`cw` can symlink selected repo-local state into each worktree. Configure
allowlisted paths in the project-local `.cw/config.toml`. Run `cw init` to
create a starter config:

```toml
[shared_state]

[[shared_state.paths]]
path = ".staffreview"
type = "dir"

[[shared_state.paths]]
path = ".env.local"
type = "file"
```

Paths must be relative and stay inside the repo. `cw new` creates the root
target if needed, then creates a relative symlink in the new worktree. `cw
resume` repairs/checks links before relaunching a dead session. Existing real
files, directories, or wrong symlinks in the worktree are never overwritten.
Only add paths that are intentionally project-local runtime state.
