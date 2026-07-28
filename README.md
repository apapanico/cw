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
in-TUI new-session form with defaults visible up front; `[f5]` creates
from that form.

Longer-term product ideas live in `docs/ideas.md`, including attached repo
management and optional GitHub/Linear/Jira-style integrations.

TUI layout and flow sketches live in `docs/tui-mocks.md`.

Install the development version as a uv tool:

```sh
uv tool install --editable /Users/alexp/Projects/cw
```

The current Bash `/Users/alexp/bin/cw` should stay in place until the uv tool
install is verified and PATH ordering is intentional.
