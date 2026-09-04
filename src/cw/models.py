from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

WORKTREE_DIR = Path(".claude/worktrees")
BRANCH_PREFIX = "claude/"
EFFORTS = {"", "low", "medium", "high", "xhigh", "max"}


class CwError(RuntimeError):
    pass


class ModeError(ValueError):
    pass


@dataclass(frozen=True)
class WorktreeSession:
    slug: str
    branch: str
    title: str
    tmux: str
    path: Path
    dirty_count: int = 0
    unpushed_count: int = 0
    upstream: str = ""
    last_commit: str = ""


@dataclass(frozen=True)
class SharedStateEntry:
    path: Path
    type: str


@dataclass(frozen=True)
class NewSessionOptions:
    title: str
    slug: str | None = None
    branch: str | None = None
    base: str | None = None
    mode: str | None = None
    model: str | None = None
    effort: str | None = None
    native: bool = False
    rc: bool = True
    happy: bool = False
    prompt: bool = True


@dataclass(frozen=True)
class ResumeOptions:
    fresh: bool = False
    pick: bool = False
    mode: str | None = None
    model: str | None = None
    effort: str | None = None
    rc: bool = True
    happy: bool = False
    prompt: bool = True


@dataclass(frozen=True)
class TuiResult:
    action: str
    slug: str | None = None
    new_options: NewSessionOptions | None = None
    resume_options: ResumeOptions | None = None
    force: bool = False
    exec_root: Path | None = None
    exec_args: list[str] | None = None


@dataclass(frozen=True)
class LaunchPlan:
    root: Path
    args: list[str]


@dataclass(frozen=True)
class PruneRow:
    session: WorktreeSession
    action: str
    reason: str


@dataclass(frozen=True)
class RemoveResult:
    slug: str
    branch: str
    worktree: Path
    killed_tmux: bool
    branch_deleted: bool


@dataclass(frozen=True)
class KillResult:
    slug: str
    session: str
