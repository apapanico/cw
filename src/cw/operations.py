from __future__ import annotations

import os
import shutil
import subprocess
import re
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

from .models import (
    BRANCH_PREFIX,
    EFFORTS,
    WORKTREE_DIR,
    CwError,
    KillResult,
    LaunchPlan,
    ModeError,
    RemoveResult,
    WorktreeSession,
)

console = Console(stderr=True)


def info(message: str) -> None:
    console.print(f"cw: {message}")


def info_with(log: Callable[[str], None] | None, message: str) -> None:
    if log:
        log(f"cw: {message}")
    else:
        info(message)


def require_cmd(name: str) -> None:
    if shutil.which(name) is None:
        raise CwError(f"required command not found on PATH: {name}")


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def run_logged(args: list[str], *, cwd: Path | None, log: Callable[[str], None]) -> subprocess.CompletedProcess[str]:
    cp = run(args, cwd=cwd, check=False, capture=True)
    for line in (cp.stdout or "").splitlines():
        log(line)
    for line in (cp.stderr or "").splitlines():
        log(line)
    if cp.returncode != 0:
        raise subprocess.CalledProcessError(cp.returncode, args, output=cp.stdout, stderr=cp.stderr)
    return cp


def git(root: Path, *args: str, check: bool = True, capture: bool = True) -> str:
    cp = run(["git", "-C", str(root), *args], check=check, capture=capture)
    return cp.stdout or ""


def repo_root() -> Path:
    cp = run(["git", "worktree", "list", "--porcelain"], check=False, capture=True)
    if cp.returncode != 0:
        raise CwError("not inside a git repository")
    for line in (cp.stdout or "").splitlines():
        if line.startswith("worktree "):
            return Path(line.removeprefix("worktree ")).resolve()
    raise CwError("not inside a git repository")


def session_name(root: Path, slug: str) -> str:
    return f"{root.name}_worktree-{slug}"


def display_name(root: Path, slug: str) -> str:
    return f"{root.name}/{slug}"


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def map_mode(mode: str) -> str:
    match mode:
        case "manual":
            return "manual"
        case "accept" | "accept-edits" | "acceptedits" | "acceptEdits":
            return "acceptEdits"
        case "plan":
            return "plan"
        case "auto":
            return "auto"
        case "bypass" | "bypassPermissions" | "yolo":
            return "bypassPermissions"
        case "dontask" | "dont-ask" | "dontAsk":
            return "dontAsk"
        case _:
            raise ModeError(mode)


def validate_effort(effort: str | None) -> str:
    value = effort or ""
    if value not in EFFORTS:
        raise CwError(f"unknown --effort '{value}' (low|medium|high|xhigh|max)")
    return value


def default_choice(value: str | None) -> str | None:
    return None if value in {None, "", "default"} else value


def tmux_alive(session: str) -> bool:
    if shutil.which("tmux") is None:
        return False
    cp = run(["tmux", "has-session", "-t", f"={session}"], check=False, capture=True)
    return cp.returncode == 0


def tmux_status(session: str) -> str:
    if not tmux_alive(session):
        return "dead"
    cp = run(
        ["tmux", "display-message", "-p", "-t", f"={session}", "#{session_attached}"],
        check=False,
        capture=True,
    )
    attached = (cp.stdout or "0").strip()
    return "attached" if attached.isdigit() and int(attached) > 0 else "detached"


def parse_worktrees_porcelain(text: str) -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    path: Path | None = None
    branch = ""
    for line in [*text.splitlines(), ""]:
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
            branch = ""
        elif line.startswith("branch "):
            branch = line.removeprefix("branch refs/heads/")
        elif line == "detached":
            branch = "(detached)"
        elif line == "":
            if path and ".claude/worktrees" in path.as_posix():
                rows.append((path, branch))
            path = None
            branch = ""
    return rows


def worktree_branch(root: Path, slug: str) -> str:
    for path, branch in parse_worktrees_porcelain(git(root, "worktree", "list", "--porcelain")):
        if path.name == slug:
            return branch
    return ""


def branch_description(root: Path, branch: str) -> str:
    if not branch or branch == "(detached)":
        return ""
    cp = run(
        ["git", "-C", str(root), "config", f"branch.{branch}.description"],
        check=False,
        capture=True,
    )
    return (cp.stdout or "").rstrip("\n")


def dirty_count(worktree: Path) -> int:
    return len([line for line in git(worktree, "status", "--porcelain", check=False).splitlines() if line])


def unpushed_count(root: Path, branch: str) -> int:
    if not branch or branch == "(detached)":
        return 0
    value = git(root, "rev-list", "--count", branch, "--not", "--remotes", check=False).strip()
    return int(value or "0")


def upstream_ref(root: Path, branch: str) -> str:
    if not branch or branch == "(detached)":
        return ""
    return git(root, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", check=False).strip()


def last_commit(root: Path, branch: str) -> str:
    ref = branch if branch and branch != "(detached)" else "HEAD"
    return git(root, "log", "-1", "--format=%cr  %s", ref, check=False).strip()


def session_state(session: WorktreeSession) -> str:
    parts = []
    if session.dirty_count:
        parts.append("dirty")
    if session.unpushed_count:
        parts.append("unpushed")
    return "+".join(parts) if parts else "clean"


def render_prompt_text(
    text: str,
    *,
    slug: str,
    branch: str,
    base: str,
    title: str,
    repo: str,
    wt: str,
) -> str:
    values = {
        "{{slug}}": slug,
        "{{branch}}": branch,
        "{{base}}": base,
        "{{title}}": title,
        "{{repo}}": repo,
        "{{wt}}": wt,
    }
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def render_prompt_file(
    prompt_path: Path,
    *,
    slug: str,
    branch: str,
    base: str,
    title: str,
    repo: str,
    wt: str,
) -> str:
    return render_prompt_text(
        prompt_path.read_text(),
        slug=slug,
        branch=branch,
        base=base,
        title=title,
        repo=repo,
        wt=wt,
    )


def default_prompt_path(prompt_file: Optional[Path]) -> Path:
    if prompt_file is not None:
        return prompt_file.expanduser()
    env = os.environ.get("CW_PROMPT_FILE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config/cw/session-prompt.md"


def doctor_rows() -> list[tuple[str, str]]:
    rows = []
    for cmd in ["git", "tmux", "claude", "uv"]:
        rows.append((cmd, shutil.which(cmd) or "missing"))
    prompt = default_prompt_path(None)
    rows.append(("prompt", str(prompt) if prompt.is_file() else f"missing: {prompt}"))
    excludes = run(["git", "config", "--global", "--get", "core.excludesFile"], check=False, capture=True)
    excludes_path = (excludes.stdout or "").strip()
    ignore_ok = "unknown"
    if excludes_path:
        ignore_file = Path(excludes_path.replace("~", str(Path.home()), 1))
        if ignore_file.is_file():
            ignore_ok = "yes" if ".claude/worktrees/" in ignore_file.read_text() else "missing .claude/worktrees/"
    rows.append(("global ignore", ignore_ok))
    rows.append(("cw on PATH", shutil.which("cw") or "missing"))
    return rows


def tmux_flag(native: bool) -> str:
    return "--tmux" if native else "--tmux=classic"


def mode_value(mode: str | None) -> str:
    if not mode:
        return ""
    try:
        return map_mode(mode)
    except ModeError as exc:
        raise CwError(f"unknown --mode '{exc.args[0]}' (manual|accept|plan|auto|bypass)") from exc


def claude_base_args(
    *,
    slug: str,
    title: str,
    native: bool,
    rc: bool,
    mode: str | None,
    model: str | None,
    effort: str | None,
) -> list[str]:
    pm = mode_value(mode)
    validate_effort(effort)
    args = ["claude", "--worktree", slug, tmux_flag(native), "--name", title]
    if rc:
        args.append("--remote-control")
    if pm:
        args.extend(["--permission-mode", pm])
    if model:
        args.extend(["--model", model])
    if effort:
        args.extend(["--effort", effort])
    return args


def append_prompt_arg(
    args: list[str],
    *,
    prompt_file: Optional[Path],
    no_prompt: bool,
    slug: str,
    branch: str,
    base: str,
    title: str,
    repo: str,
    wt: str,
    log: Callable[[str], None] | None = None,
) -> None:
    prompt_path = default_prompt_path(prompt_file)
    if no_prompt or not prompt_path.is_file():
        return
    rendered = render_prompt_file(
        prompt_path,
        slug=slug,
        branch=branch,
        base=base,
        title=title,
        repo=repo,
        wt=wt,
    )
    if rendered:
        args.extend(["--append-system-prompt", rendered])
        info_with(log, f"  + session prompt from {prompt_path}")


def execvp(args: list[str]) -> None:
    os.execvp(args[0], args)


def exec_from(root: Path, args: list[str]) -> None:
    os.chdir(root)
    execvp(args)


def list_sessions(root: Path) -> list[WorktreeSession]:
    sessions = []
    for path, branch in parse_worktrees_porcelain(git(root, "worktree", "list", "--porcelain")):
        slug = path.name
        title = branch_description(root, branch)
        abs_path = path if path.is_absolute() else root / path
        sessions.append(
            WorktreeSession(
                slug=slug,
                branch=branch,
                title=title,
                tmux=tmux_status(session_name(root, slug)),
                path=path,
                dirty_count=dirty_count(abs_path),
                unpushed_count=unpushed_count(root, branch),
                upstream=upstream_ref(root, branch),
                last_commit=last_commit(abs_path, branch),
            )
        )
    return sessions


def machine_worktree_tmux_lines(repo: str) -> list[str]:
    sigs = run(["tmux", "ls", "-F", "#{session_name}\t#{session_attached}"], check=False, capture=True)
    rows = []
    for line in (sigs.stdout or "").splitlines():
        if "_worktree-" not in line:
            continue
        session, attached = line.split("\t", 1)
        scope = "this-repo" if session.startswith(f"{repo}_worktree-") else "other-repo"
        state = "attached" if attached.isdigit() and int(attached) > 0 else "detached"
        rows.append(f"{session:<42} {state:<9} {scope}")
    return rows


def prepare_new_session(
    *,
    title_parts: list[str],
    slug: str | None,
    branch: str | None,
    base: str | None,
    mode: str | None,
    model: str | None,
    effort: str | None,
    prompt_file: Optional[Path],
    no_prompt: bool,
    native: bool,
    no_rc: bool,
    log: Callable[[str], None] | None = None,
) -> LaunchPlan:
    require_cmd("claude")
    require_cmd("tmux")
    session_title = " ".join(title_parts)
    if not session_title:
        raise CwError("usage: cw new <title> [--slug <name>] [--base <branch>] [--no-rc]")
    slug = slug or slugify(session_title)
    if not slug:
        raise CwError(f"could not derive a name from title '{session_title}'; pass --slug <name>")
    mode_value(mode)
    validate_effort(effort)
    branch_name = branch or f"{BRANCH_PREFIX}{slug}"
    cp = run(["git", "check-ref-format", f"refs/heads/{branch_name}"], check=False, capture=True)
    if cp.returncode != 0:
        raise CwError(f"invalid branch name: {branch_name}")

    root = repo_root()
    repo = root.name
    wt = WORKTREE_DIR / slug
    sess = session_name(root, slug)
    if tmux_alive(sess):
        raise CwError(f"tmux session '{sess}' already exists; use 'cw resume {slug}'")
    if (root / wt).exists():
        raise CwError(f"worktree path already exists: {wt} (use 'cw resume {slug}')")
    if base is None:
        base = "origin/main"
        info_with(log, "fetching latest origin/main")
        if log:
            run_logged(["git", "fetch", "origin", "main"], cwd=root, log=log)
        else:
            run(["git", "fetch", "origin", "main"], cwd=root)
    info_with(log, f"creating worktree {wt} on branch {branch_name} (base: {base})")
    info_with(log, f'  title: "{session_title}"  (slug: {slug})')
    if log:
        run_logged(["git", "worktree", "add", str(wt), "-b", branch_name, base], cwd=root, log=log)
    else:
        run(["git", "worktree", "add", str(wt), "-b", branch_name, base], cwd=root)
    run(
        ["git", "config", f"branch.{branch_name}.description", session_title],
        cwd=root,
        check=False,
    )
    args = claude_base_args(
        slug=slug,
        title=session_title,
        native=native,
        rc=not no_rc,
        mode=mode,
        model=model,
        effort=effort,
    )
    info_with(log, f"launching: {' '.join(args)}")
    append_prompt_arg(
        args,
        prompt_file=prompt_file,
        no_prompt=no_prompt,
        slug=slug,
        branch=branch_name,
        base=base,
        title=session_title,
        repo=repo,
        wt=str(wt),
        log=log,
    )
    return LaunchPlan(root=root, args=args)


def prepare_resume_session(
    name: str,
    *,
    fresh: bool = False,
    pick: bool = False,
    mode: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    prompt_file: Optional[Path] = None,
    no_prompt: bool = False,
    native: bool = False,
    no_rc: bool = False,
    log: Callable[[str], None] | None = None,
) -> LaunchPlan:
    if fresh and pick:
        raise CwError("--fresh and --pick are mutually exclusive")
    require_cmd("tmux")
    mode_value(mode)
    validate_effort(effort)
    root = repo_root()
    sess = session_name(root, name)
    if tmux_alive(sess):
        if fresh or pick or mode:
            info_with(log, "session is live; attaching (--fresh/--pick/--mode ignored)")
        return LaunchPlan(root=root, args=["tmux", "attach-session", "-t", f"={sess}"])

    wt = WORKTREE_DIR / name
    if not (root / wt).is_dir():
        raise CwError(f"no tmux session and no worktree named '{name}' (looked in {wt})")
    require_cmd("claude")
    branch = worktree_branch(root, name)
    title = branch_description(root, branch) or display_name(root, name)
    info_with(log, f"no live tmux session; relaunching claude on existing worktree {wt}")
    args = claude_base_args(
        slug=name,
        title=title,
        native=native,
        rc=not no_rc,
        mode=mode,
        model=model,
        effort=effort,
    )
    if pick:
        args.append("--resume")
    elif not fresh:
        args.append("--continue")
    info_with(log, f"launching: {' '.join(args)}")
    upstream = git(root, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", check=False).strip()
    base = upstream or "origin/main"
    append_prompt_arg(
        args,
        prompt_file=prompt_file,
        no_prompt=no_prompt,
        slug=name,
        branch=branch,
        base=base,
        title=title,
        repo=root.name,
        wt=str(wt),
        log=log,
    )
    return LaunchPlan(root=root, args=args)


def remove_session(
    root: Path,
    name: str,
    *,
    force: bool = False,
    log: Callable[[str], None] | None = None,
) -> RemoveResult:
    wt = WORKTREE_DIR / name
    abs_wt = root / wt
    if not abs_wt.is_dir():
        raise CwError(f"no worktree named '{name}' (looked in {wt})")
    branch = worktree_branch(root, name)
    if not force:
        if git(abs_wt, "status", "--porcelain", check=False).strip():
            raise CwError(f"worktree '{name}' has uncommitted changes; commit them or use 'cw rm {name} --force'")
        if branch:
            unpushed = git(root, "rev-list", "--count", branch, "--not", "--remotes", check=False).strip()
            if int(unpushed or "0") > 0:
                raise CwError(
                    f"branch '{branch}' has {unpushed} commit(s) not on any remote; "
                    f"push them or use 'cw rm {name} --force'"
                )
    sess = session_name(root, name)
    killed_tmux = False
    if tmux_alive(sess):
        if log:
            log(f"killing tmux session '{sess}'")
        run(["tmux", "kill-session", "-t", f"={sess}"])
        killed_tmux = True
    if log:
        log(f"removing worktree {wt}")
    args = ["git", "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(wt))
    run(args, cwd=root)
    branch_deleted = False
    branch_exists = branch and run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=root,
        check=False,
    ).returncode == 0
    if branch_exists:
        if log:
            log(f"deleting branch {branch}")
        run(["git", "branch", "-D", branch], cwd=root)
        branch_deleted = True
    elif log:
        suffix = f" ({branch} not found)" if branch else ""
        log(f"no backing branch to delete{suffix}")
    return RemoveResult(
        slug=name,
        branch=branch,
        worktree=wt,
        killed_tmux=killed_tmux,
        branch_deleted=branch_deleted,
    )


def kill_session(root: Path, name: str, *, log: Callable[[str], None] | None = None) -> KillResult:
    sess = session_name(root, name)
    if not tmux_alive(sess):
        raise CwError(f"no tmux session named '{sess}'")
    run(["tmux", "kill-session", "-t", f"={sess}"])
    if log:
        log(f"killed tmux session '{sess}' (worktree and branch left intact)")
    return KillResult(slug=name, session=sess)
