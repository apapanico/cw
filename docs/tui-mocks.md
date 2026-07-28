# TUI Mocks

Design sketches for the `cw` Textual interface. These are mocks, not an
implementation contract yet.

## Command Shape

Proposal:

```text
cw              show CLI command list / help
cw dash         open dashboard TUI
cwd             optional shell alias for `cw dash`
```

Rationale:

- `cw` stays predictable as a CLI entrypoint.
- `cw dash` is explicit and memorable.
- `cwd` can be a fast local alias without claiming another package command.

## Dashboard

Target width: roughly 110-120 columns.

```text
cw dash
────────────────────────────────────────────────────────────────────────────────────────────────────────────
Repo  /Users/alexp/Projects/work/otter/ottersim                                      Filter  _
Worktrees  .claude/worktrees
──────────────────────────────────────────────────────────────────────┬─────────────────────────────────────
NAME                    TMUX       STATE           BRANCH              │ Details
demo-cw                 detached   clean           claude/demo-cw      │ Name       demo-cw
pseudospectral-opt-perf dead       clean           claude/pseudo...    │ Title      Demo cw
rmader-follow-up        detached   clean           feat/swarm-torus    │ Branch     claude/demo-cw
scheduling              dead       clean           feat/offline...     │ Upstream   origin/main
test-suite-perf         dead       clean           claude/test-suite   │ Tmux       detached
                                                                      │ Session    ottersim_worktree-demo-cw
                                                                      │ Path       .claude/worktrees/demo-cw
                                                                      │ Dirty      0 files
                                                                      │ Unpushed   0 commits
                                                                      │ Recent     3 days ago Merge PR #74
──────────────────────────────────────────────────────────────────────┴─────────────────────────────────────
[n] new  [enter] resume  [s] status  [/] filter  [x] remove  [k] kill
[p] prune  [d] doctor  [?] help  [q] quit
```

Notes:

- `enter` should resume/attach the highlighted session.
- Table should handle `DataTable.RowSelected`; app-level Enter may be swallowed.
- Explicit words beat unclear glyphs. Use color for tmux/state, not icons.
- Details should use repo-relative paths when possible.

## New Session Form

Goal: all important fields visible at once, no scroll. Defaults should be
visible. This should not drop into sequential CLI prompts.

```text
┌─ New session ────────────────────────────────────────────────────────────────────────────────────────────┐
│ Title              Demo cw                         Slug              demo-cw                            │
│ Branch             claude/demo-cw                  Branch override   _                                  │
│ Base ref           origin/main                     Fetch base        yes                                │
│ Mode               default                         Model             _                                  │
│ Effort             default                         Tmux              classic                            │
│ Remote Control     yes                             Prompt            yes                                │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Preview                                                                                                  │
│ branch  claude/demo-cw                                                                                   │
│ base    origin/main (fetch first)                                                                         │
│ launch  claude --worktree demo-cw --tmux=classic --name "Demo cw" --remote-control                       │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [enter] create  [tab] next field  [shift-tab] prev field  [esc] cancel                                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```



```text
┌─ New session ────────────────────────────────────────────────────────────────────────────────────────────┐
│ Title  Demo cw                                    Slug  demo-cw                                          │
│ Branch claude/demo-cw                             Base  origin/main                                      │
│ Mode   default       Model _       Effort default       Tmux classic       RC yes       Prompt yes       │
│                                                                                                          │
│ Create command                                                                                            │
│ cw new "Demo cw"                                                                                          │
│                                                                                                          │
│ Claude launch                                                                                             │
│ claude --worktree demo-cw --tmux=classic --name "Demo cw" --remote-control                               │
│                                                                                                          │
│ [enter] create   [esc] cancel                                                                             │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Open questions:

- Decision: `enter` creates from anywhere in the form.
- Decision: `base` visibly defaults to `origin/main`, and `cw` still fetches it
  before creating the worktree.
- Decision: show the derived default branch as read-only preview, with a
  separate branch override field.

## Resume Flow

Live session:

```text
enter on selected row
→ attach tmux session immediately
```

Dead session:

```text
┌─ Resume demo-cw ─────────────────────────────────────────────┐
│ No live tmux session exists. Relaunch Claude on this worktree.│
│                                                              │
│ Conversation                                                 │
│   continue previous conversation                             │
│   fresh conversation                                         │
│   open Claude picker                                         │
│                                                              │
│ Mode     default                                             │
│ Model    _                                                   │
│ Effort   default                                             │
│ RC       yes                                                 │
│ Prompt   yes                                                 │
│                                                              │
│ [enter] launch  [esc] cancel                                 │
└──────────────────────────────────────────────────────────────┘
```

Question:

- Decision: `enter` on a dead session shows this modal.

## Remove Flow

```text
┌─ Remove demo-cw ─────────────────────────────────────────────┐
│ Worktree   .claude/worktrees/demo-cw                         │
│ Branch     claude/demo-cw                                    │
│ Tmux       detached                                          │
│ Dirty      no                                                │
│ Unpushed   0 commits                                         │
│                                                             │
│ This will kill tmux, remove the worktree, and delete branch. │
│                                                             │
│ [r] remove  [f] force remove  [esc] cancel                   │
└─────────────────────────────────────────────────────────────┘
```

Dirty/unpushed state:

```text
┌─ Remove demo-cw ─────────────────────────────────────────────┐
│ Dirty      3 files                                           │
│ Unpushed   2 commits                                         │
│                                                             │
│ Normal remove is blocked. Use force only if you accept loss. │
│                                                             │
│ [f] force remove  [esc] cancel                              │
└─────────────────────────────────────────────────────────────┘
```

## Kill Flow

```text
┌─ Kill tmux session ──────────────────────────────────────────┐
│ Session  ottersim_worktree-demo-cw                           │
│ State    detached                                            │
│                                                             │
│ Worktree and branch are left intact.                         │
│                                                             │
│ [k] kill tmux  [esc] cancel                                  │
└─────────────────────────────────────────────────────────────┘
```

## Prune Flow

```text
┌─ Prune Preview ─────────────────────────────────────────────────────────────┐
│ Eligible                                                                    │
│   demo-cw              dead  clean  pushed                                  │
│   test-suite-perf      dead  clean  pushed                                  │
│                                                                             │
│ Kept                                                                        │
│   rmader-follow-up     detached session alive                               │
│   local-spike          dead  2 unpushed commits                             │
│   dirty-parser         dead  4 dirty files                                  │
│                                                                             │
│ [p] apply prune  [r] refresh  [esc] cancel                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

Rules:

- Never kill tmux from prune.
- Dry-run preview first.
- Apply only eligible dead + clean + fully pushed worktrees.

## Status Flow

Status can either stay as the details pane or open a larger modal:

```text
┌─ Status demo-cw ─────────────────────────────────────────────┐
│ Repo      /Users/alexp/Projects/work/otter/ottersim          │
│ Worktree  .claude/worktrees/demo-cw                          │
│ Branch    claude/demo-cw                                     │
│ Upstream  origin/main                                        │
│ Tmux      detached                                           │
│ Dirty     0 files                                            │
│ Unpushed  0 commits                                          │
│                                                             │
│ Recent commits                                               │
│   3 days ago  Merge pull request #74                         │
│                                                             │
│ Linked work                                                  │
│   PR      -                                                  │
│   Linear  -                                                  │
│   Jira    -                                                  │
│                                                             │
│ [r] refresh  [enter] resume  [esc] back                      │
└─────────────────────────────────────────────────────────────┘
```

## Doctor Flow

```text
┌─ Doctor ─────────────────────────────────────────────────────┐
│ git       /opt/homebrew/bin/git                              │
│ tmux      /opt/homebrew/bin/tmux                             │
│ claude    /opt/homebrew/bin/claude                           │
│ uv        /opt/homebrew/bin/uv                               │
│ prompt    /Users/alexp/.config/cw/session-prompt.md          │
│ ignore    .claude/worktrees/ present                         │
│ cw PATH   /Users/alexp/bin/cw                                │
│                                                             │
│ [r] refresh  [esc] back                                      │
└─────────────────────────────────────────────────────────────┘
```
