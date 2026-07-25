# Ideas and TODOs

These are product directions to consider after the Python port reaches Bash
parity and the Textual TUI is stable.

## General-Purpose Repo Manager

Turn `cw` into a local manager for multiple repositories, not just a launcher
inside the current repo.

Possible shape:

- `cw repo attach <path>` registers a repo with cw.
- `cw repo detach <path-or-name>` stops monitoring it.
- `cw repo list` shows attached repos, their default branch, worktree count,
  dirty worktrees, active tmux sessions, and last-seen status.
- Naked `cw` can open a global dashboard when run outside a git repo.
- Inside a git repo, naked `cw` opens that repo by default but can switch to the
  global view.

State storage options:

- `~/.config/cw/config.toml` for user preferences and attached repos.
- `~/.local/state/cw/state.json` or SQLite for cached repo/session status.
- Keep repo-local git state as the source of truth whenever possible.

Open questions:

- Should attached repos be opt-in only, or should `cw` offer to attach the
  current repo on first use?
- Should status be computed live every time, cached, or refreshed in the
  background?
- How should renamed/moved repos be detected?

## External Work Trackers

Attach external systems to repos and/or sessions so `cw` can show issue and PR
context.

Candidates:

- GitHub: branch PR URL, PR status, review status, CI status.
- Linear: issue title, status, assignee, project, labels.
- Jira: issue title, status, assignee, sprint, epic.
- Other future sources: Slack links, docs, design links, release tickets.

Possible commands:

- `cw connect github`
- `cw connect linear`
- `cw connect jira`
- `cw link <session> <external-id-or-url>`
- `cw unlink <session> <external-id-or-url>`
- `cw sync`

TUI ideas:

- Repo dashboard with attached trackers.
- Session detail pane shows PR, issue, CI, and review state.
- Filter sessions by tracker state, for example `review`, `ci-failing`,
  `linear:in-progress`, or `jira:blocked`.
- Recent activity window can include local git events plus tracker updates.

Implementation notes:

- Keep integrations optional; the core CLI should work with only git, tmux, and
  Claude.
- Prefer existing CLIs/APIs when available, such as `gh` for GitHub.
- Do not make the TUI slow or network-dependent by default. Cache external
  status and refresh explicitly or in the background.

## TUI Follow-Ups

- Replace the `questionary` new-session flow with a Textual modal form.
- Add a global repo dashboard.
- Add session action confirmation modals inside Textual.
- Add richer selected-session activity:
  - recent commits
  - dirty file preview
  - unpushed commits
  - PR link/status when available
  - linked Linear/Jira issue state when configured
- Add configurable color theme and compact/detailed density modes.
