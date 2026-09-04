from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated, Iterable, Optional

import questionary
import typer
from rich.console import Console
from rich.table import Table

from .models import (
    BRANCH_PREFIX,
    WORKTREE_DIR,
    CwError,
    KillResult,
    LaunchPlan,
    ModeError,
    NewSessionOptions,
    RemoveResult,
    ResumeOptions,
    TuiResult,
    WorktreeSession,
)
from .operations import (
    append_prompt_arg,
    branch_description,
    claude_base_args,
    default_choice,
    default_prompt_path,
    display_name,
    doctor_rows,
    exec_from,
    execvp,
    git,
    info,
    kill_session,
    list_sessions,
    machine_worktree_tmux_lines,
    map_mode,
    mode_value,
    parse_worktrees_porcelain,
    prepare_new_session,
    prepare_resume_session,
    remove_session,
    render_prompt_text,
    repo_root,
    run,
    session_name,
    session_state,
    slugify,
    tmux_alive,
    tmux_flag,
    tmux_status,
    validate_effort,
    worktree_branch,
)
from .tui import CwTui, run_tui

console = Console(stderr=True)
out = Console()

app = typer.Typer(
    add_completion=True,
    invoke_without_command=True,
    no_args_is_help=False,
    help="Claude Code worktree session launcher.",
)


def print_sessions(sessions: Iterable[WorktreeSession]) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("NAME", no_wrap=True)
    table.add_column("BRANCH", no_wrap=True)
    table.add_column("TMUX", no_wrap=True)
    table.add_column("STATE", no_wrap=True)
    table.add_column("TITLE")
    count = 0
    for session in sessions:
        count += 1
        table.add_row(session.slug, session.branch, session.tmux, session_state(session), session.title)
    if count:
        out.print(table)
    else:
        out.print(f"(no worktree sessions under {WORKTREE_DIR})")


def pick_session(root: Path, *, message: str = "Select a session") -> WorktreeSession | None:
    sessions = list_sessions(root)
    if not sessions:
        out.print(f"(no worktree sessions under {WORKTREE_DIR})")
        return None
    choices = [
        questionary.Choice(
            f"{s.slug}  [{s.tmux}]  {s.branch}  {s.title}".rstrip(),
            value=s,
        )
        for s in sessions
    ]
    return questionary.select(message, choices=choices).ask()


def interactive_new() -> None:
    title = questionary.text("Title").ask()
    if not title:
        raise typer.Exit(0)
    derived = slugify(title)
    slug = questionary.text("Slug", default=derived).ask() or derived
    branch = questionary.text("Branch override (blank for claude/<slug>)").ask() or None
    base = questionary.text("Base ref (blank for latest origin/main)").ask() or None
    mode = questionary.select(
        "Permission mode",
        choices=[
            questionary.Choice("default", value=None),
            "manual",
            "accept",
            "plan",
            "auto",
            "bypass",
        ],
    ).ask()
    model = questionary.text("Model (blank for Claude default)").ask() or None
    effort = questionary.select(
        "Effort",
        choices=[questionary.Choice("default", value=None), "low", "medium", "high", "xhigh", "max"],
    ).ask()
    native = questionary.confirm("Use native iTerm2 tmux integration?", default=False).ask()
    happy = questionary.confirm("Launch with Happy?", default=False).ask()
    rc = False if happy else questionary.confirm("Enable Remote Control?", default=True).ask()
    use_prompt = questionary.confirm("Inject session prompt if present?", default=True).ask()
    new(
        title=[title],
        slug=slug,
        branch=branch,
        base=base,
        mode=default_choice(mode),
        model=model,
        effort=default_choice(effort),
        prompt_file=None,
        no_prompt=not bool(use_prompt),
        native=bool(native),
        no_rc=not bool(rc),
        happy=bool(happy),
    )


@app.callback()
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


@app.command()
def dash() -> None:
    """Open dashboard TUI."""
    run_tui()


@app.command()
def tui() -> None:
    """Open dashboard TUI."""
    run_tui()


@app.command()
def new(
    title: Annotated[list[str], typer.Argument(help="Session display title.")],
    slug: Annotated[Optional[str], typer.Option("--slug", help="Explicit worktree name.")] = None,
    branch: Annotated[Optional[str], typer.Option("--branch", help="Branch name override.")] = None,
    base: Annotated[Optional[str], typer.Option("--base", help="Base ref.")] = None,
    mode: Annotated[Optional[str], typer.Option("--mode", help="Permission mode.")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="Claude model.")] = None,
    effort: Annotated[Optional[str], typer.Option("--effort", help="Reasoning effort.")] = None,
    prompt_file: Annotated[
        Optional[Path], typer.Option("--prompt-file", help="Session prompt template.")
    ] = None,
    no_prompt: Annotated[bool, typer.Option("--no-prompt", help="Skip session prompt.")] = False,
    native: Annotated[bool, typer.Option("--native", help="Use native tmux integration.")] = False,
    classic: Annotated[bool, typer.Option("--classic", help="Use classic tmux.")] = False,
    no_rc: Annotated[bool, typer.Option("--no-rc", help="Launch without Remote Control.")] = False,
    happy: Annotated[bool, typer.Option("--happy", help="Launch with Happy instead of Claude.")] = False,
) -> None:
    del classic
    plan = prepare_new_session(
        title_parts=title,
        slug=slug,
        branch=branch,
        base=base,
        mode=mode,
        model=model,
        effort=effort,
        prompt_file=prompt_file,
        no_prompt=no_prompt,
        native=native,
        no_rc=no_rc,
        happy=happy,
    )
    exec_from(plan.root, plan.args)


@app.command()
def resume(
    name: Annotated[str, typer.Argument(help="Worktree session slug.")],
    fresh: Annotated[bool, typer.Option("--fresh", help="Start a new conversation.")] = False,
    pick: Annotated[bool, typer.Option("--pick", "-r", help="Open Claude's session picker.")] = False,
    mode: Annotated[Optional[str], typer.Option("--mode", help="Permission mode.")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="Claude model.")] = None,
    effort: Annotated[Optional[str], typer.Option("--effort", help="Reasoning effort.")] = None,
    prompt_file: Annotated[
        Optional[Path], typer.Option("--prompt-file", help="Session prompt template.")
    ] = None,
    no_prompt: Annotated[bool, typer.Option("--no-prompt", help="Skip session prompt.")] = False,
    native: Annotated[bool, typer.Option("--native", help="Use native tmux integration.")] = False,
    classic: Annotated[bool, typer.Option("--classic", help="Use classic tmux.")] = False,
    no_rc: Annotated[bool, typer.Option("--no-rc", help="Relaunch without Remote Control.")] = False,
    happy: Annotated[bool, typer.Option("--happy", help="Relaunch with Happy instead of Claude.")] = False,
) -> None:
    del classic
    plan = prepare_resume_session(
        name,
        fresh=fresh,
        pick=pick,
        mode=mode,
        model=model,
        effort=effort,
        prompt_file=prompt_file,
        no_prompt=no_prompt,
        native=native,
        no_rc=no_rc,
        happy=happy,
        log=info,
    )
    exec_from(plan.root, plan.args)


@app.command("list")
def list_cmd() -> None:
    print_sessions(list_sessions(repo_root()))


@app.command("ls")
def ls_cmd() -> None:
    list_cmd()


@app.command()
def status(name: Annotated[str, typer.Argument(help="Worktree session slug.")]) -> None:
    root = repo_root()
    wt = root / WORKTREE_DIR / name
    if not wt.is_dir():
        raise CwError(f"no worktree named '{name}' (looked in {WORKTREE_DIR / name})")
    branch = worktree_branch(root, name)
    dirty = git(wt, "status", "--porcelain", check=False).strip()
    unpushed = "0"
    if branch and branch != "(detached)":
        unpushed = git(root, "rev-list", "--count", branch, "--not", "--remotes", check=False).strip()
    table = Table(show_header=False)
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("Name", name)
    table.add_row("Path", str(WORKTREE_DIR / name))
    table.add_row("Branch", branch or "")
    table.add_row("Title", branch_description(root, branch))
    table.add_row("Tmux", tmux_status(session_name(root, name)))
    table.add_row("Dirty", "yes" if dirty else "no")
    table.add_row("Unpushed", unpushed or "0")
    out.print(table)


@app.command()
def rm(
    name: Annotated[str, typer.Argument(help="Worktree session slug.")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Remove despite dirty state.")] = False,
) -> None:
    remove_session(repo_root(), name, force=force, log=info)


@app.command("remove")
def remove_cmd(
    name: Annotated[str, typer.Argument(help="Worktree session slug.")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Remove despite dirty state.")] = False,
) -> None:
    rm(name, force)


@app.command()
def kill(name: Annotated[str, typer.Argument(help="Worktree session slug.")]) -> None:
    kill_session(repo_root(), name, log=info)


@app.command()
def prune(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Apply removals.")] = False,
) -> None:
    root = repo_root()
    repo = root.name
    sessions = list_sessions(root)
    if yes:
        info("prune: applying - removing eligible worktrees + branches")
    else:
        info("prune: dry run - nothing removed (pass --yes to apply)")
    removable = 0
    kept = 0
    for s in sessions:
        wt = root / WORKTREE_DIR / s.slug
        if tmux_alive(session_name(root, s.slug)):
            out.print(f"  keep    {s.slug:<22} session alive ({s.tmux})")
            kept += 1
            continue
        if git(wt, "status", "--porcelain", check=False).strip():
            out.print(f"  keep    {s.slug:<22} uncommitted changes")
            kept += 1
            continue
        unpushed = 0
        if s.branch and s.branch != "(detached)":
            unpushed = int(git(root, "rev-list", "--count", s.branch, "--not", "--remotes", check=False).strip() or "0")
        if unpushed > 0:
            out.print(f"  keep    {s.slug:<22} {unpushed} unpushed commit(s) on {s.branch}")
            kept += 1
            continue
        removable += 1
        if yes:
            out.print(f"  remove  {s.slug:<22} ({s.branch})")
            cp = run(["git", "worktree", "remove", str(WORKTREE_DIR / s.slug)], cwd=root, check=False)
            if cp.returncode == 0 and s.branch and s.branch != "(detached)":
                if run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{s.branch}"], cwd=root, check=False).returncode == 0:
                    run(["git", "branch", "-D", s.branch], cwd=root, check=False, capture=True)
            elif cp.returncode != 0:
                out.print(f"  (failed to remove {s.slug} - skipped)")
                removable -= 1
                kept += 1
        else:
            out.print(f"  would remove {s.slug:<18} ({s.branch})")
    run(["git", "worktree", "prune"], cwd=root, check=False)
    lines = machine_worktree_tmux_lines(repo)
    if lines:
        out.print("\nClaude worktree tmux sessions on this machine (kill by hand if stale):")
        for line in lines:
            out.print(f"  {line}")
    if yes:
        info(f"prune: removed {removable}, kept {kept}")
    else:
        info(f"prune: {removable} removable, {kept} kept - pass --yes to apply")


@app.command()
def doctor() -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Result")
    for name, result in doctor_rows():
        table.add_row(name, result)
    out.print(table)


def entrypoint() -> int | None:
    try:
        app()
    except CwError as exc:
        console.print(f"cw: {exc}")
        return 1
    except subprocess.CalledProcessError:
        return 1
    return None


if __name__ == "__main__":
    entrypoint()
