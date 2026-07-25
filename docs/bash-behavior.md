# Bash Behavior Checklist

Source of truth read from `/Users/alexp/bin/cw` on 2026-07-24.

## Commands

- `cw new <title>`
- `cw resume <name>`
- `cw list`, `cw ls`
- `cw rm <name>`, `cw remove <name>`
- `cw kill <name>`
- `cw prune`

## Invariants

- Worktrees live under `.claude/worktrees/<slug>` relative to the main worktree.
- Default branch is `claude/<slug>`, unless `--branch` is provided.
- Default base is freshly fetched `origin/main`; `--base <ref>` skips fetch.
- tmux session name is `<repo-basename>_worktree-<slug>`.
- Commands key off slug, not branch.
- Branch is discovered for resume/delete; do not assume `claude/<slug>`.
- Human title is stored in `branch.<branch>.description`.
- `--tmux=classic` is the default.
- `--native` passes Claude's bare `--tmux`.
- `--remote-control` is passed bare by default.
- Multiline prompt is passed as one `--append-system-prompt` argument.
- Prompt template placeholders: `{{slug}}`, `{{branch}}`, `{{base}}`,
  `{{title}}`, `{{repo}}`, `{{wt}}`.

## Safety

- `rm` refuses dirty worktrees unless `--force`.
- `rm` refuses branches with commits not on any remote unless `--force`.
- `prune` is dry-run by default.
- `prune` only removes dead-session, clean, fully-pushed worktrees.
- `prune` never kills tmux sessions.
- `prune` reports machine-wide tmux sessions whose names contain `_worktree-`.
