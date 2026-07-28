from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Callable, Iterable, Optional

import questionary
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, RichLog, Select, Static

WORKTREE_DIR = Path(".claude/worktrees")
BRANCH_PREFIX = "claude/"
EFFORTS = {"", "low", "medium", "high", "xhigh", "max"}

console = Console(stderr=True)
out = Console()

app = typer.Typer(
    add_completion=True,
    invoke_without_command=True,
    no_args_is_help=False,
    help="Claude Code worktree session launcher.",
)

WORD_DELETE_BINDINGS = [
    Binding("alt+backspace", "word_delete", "delete word", show=False),
    Binding("option+backspace", "word_delete", "delete word", show=False),
    Binding("meta+backspace", "word_delete", "delete word", show=False),
    Binding("ctrl+w", "word_delete", "delete word", show=False),
]


def delete_focused_input_word(focused: object) -> bool:
    if isinstance(focused, Input):
        focused.action_delete_left_word()
        return True
    return False


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
    prompt: bool = True


@dataclass(frozen=True)
class ResumeOptions:
    fresh: bool = False
    pick: bool = False
    mode: str | None = None
    model: str | None = None
    effort: str | None = None
    rc: bool = True
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


def info(message: str) -> None:
    console.print(f"cw: {message}")


def info_with(log: Callable[[str], None] | None, message: str) -> None:
    if log:
        log(f"cw: {message}")
    else:
        info(message)


def die(message: str) -> None:
    raise typer.BadParameter(message)


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


def state_text(value: str) -> Text:
    if value == "attached":
        return Text(value, style="bold green")
    if value == "detached":
        return Text(value, style="yellow")
    if value == "dead":
        return Text(value, style="dim")
    if value == "clean":
        return Text(value, style="green")
    if "dirty" in value:
        return Text(value, style="bold red")
    return Text(value, style="yellow")


class NewSessionScreen(ModalScreen[NewSessionOptions | None]):
    CSS = """
    NewSessionScreen {
        align: center middle;
    }

    #new-dialog {
        width: 96;
        max-width: 96%;
        height: auto;
        padding: 1 2;
        border: solid #38bdf8;
        background: #111827;
    }

    #new-title {
        height: 1;
        margin-bottom: 0;
        color: #e2e8f0;
        text-style: bold;
    }

    .form-row {
        height: 3;
        margin-top: 0;
    }

    .form-cell {
        width: 1fr;
        height: 3;
        margin-right: 1;
    }

    .field-label {
        height: 1;
        margin-top: 0;
        color: #94a3b8;
    }

    Input, Select {
        height: 1;
    }

    .toggle-row {
        height: auto;
        margin-top: 0;
    }

    #new-preview {
        min-height: 3;
        margin-top: 1;
        padding: 1;
        border: solid #334155;
        color: #cbd5e1;
    }

    #new-branch-preview {
        height: 1;
        padding: 0 1;
        background: #1f2937;
        color: #e5e7eb;
    }

    #new-error {
        height: 1;
        margin-top: 1;
        color: #f87171;
    }

    #new-buttons {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }

    Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+enter", "create", "create", priority=True),
        Binding("f5", "create", "create", priority=True),
        Binding("ctrl+s", "create", "create"),
        *WORD_DELETE_BINDINGS,
    ]

    def __init__(self) -> None:
        super().__init__()
        self.auto_slug = ""

    def compose(self) -> ComposeResult:
        with Container(id="new-dialog"):
            yield Static("New session", id="new-title")
            with Horizontal(classes="form-row"):
                with Vertical(classes="form-cell"):
                    yield Label("Title", classes="field-label")
                    yield Input(placeholder="Demo cw", compact=True, id="new-session-title")
                with Vertical(classes="form-cell"):
                    yield Label("Slug", classes="field-label")
                    yield Input(placeholder="auto from title", compact=True, id="new-slug")
            with Horizontal(classes="form-row"):
                with Vertical(classes="form-cell"):
                    yield Label("Branch override", classes="field-label")
                    yield Input(placeholder="optional branch name", compact=True, id="new-branch")
                with Vertical(classes="form-cell"):
                    yield Label("Branch", classes="field-label")
                    yield Static("claude/<slug>", id="new-branch-preview")
            with Horizontal(classes="form-row"):
                with Vertical(classes="form-cell"):
                    yield Label("Base ref", classes="field-label")
                    yield Input(value="origin/main", compact=True, id="new-base")
                with Vertical(classes="form-cell"):
                    yield Label("Fetch base", classes="field-label")
                    yield Static("yes for origin/main; custom refs skip fetch", id="new-fetch-preview")
            with Horizontal(classes="form-row"):
                with Vertical(classes="form-cell"):
                    yield Label("Permission mode", classes="field-label")
                    yield Select(
                        [
                            ("default", "default"),
                            ("manual", "manual"),
                            ("accept", "accept"),
                            ("plan", "plan"),
                            ("auto", "auto"),
                            ("bypass", "bypass"),
                        ],
                        value="default",
                        allow_blank=False,
                        compact=True,
                        id="new-mode",
                    )
                with Vertical(classes="form-cell"):
                    yield Label("Model", classes="field-label")
                    yield Input(placeholder="blank => Claude default", compact=True, id="new-model")
            with Horizontal(classes="form-row"):
                with Vertical(classes="form-cell"):
                    yield Label("Effort", classes="field-label")
                    yield Select(
                        [
                            ("default", "default"),
                            ("low", "low"),
                            ("medium", "medium"),
                            ("high", "high"),
                            ("xhigh", "xhigh"),
                            ("max", "max"),
                        ],
                        value="default",
                        allow_blank=False,
                        compact=True,
                        id="new-effort",
                    )
                with Vertical(classes="form-cell"):
                    yield Label("Launch", classes="field-label")
                    with Horizontal(classes="toggle-row"):
                        yield Checkbox("classic tmux", value=True, compact=True, id="new-classic")
                        yield Checkbox("Remote Control", value=True, compact=True, id="new-rc")
                        yield Checkbox("prompt", value=True, compact=True, id="new-prompt")
            yield Static("", id="new-preview")
            yield Static("", id="new-error")
            with Horizontal(id="new-buttons"):
                yield Button("Cancel", id="new-cancel")
                yield Button("Create", variant="primary", id="new-create")

    def on_mount(self) -> None:
        self.query_one("#new-session-title", Input).focus()
        self.update_preview()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "new-session-title":
            slug_input = self.query_one("#new-slug", Input)
            derived = slugify(event.value)
            if not slug_input.value or slug_input.value == self.auto_slug:
                slug_input.value = derived
            self.auto_slug = derived
        self.update_preview()

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.focus_next()

    def on_select_changed(self, _: Select.Changed) -> None:
        self.update_preview()
        self.focus_next()

    def on_checkbox_changed(self, _: Checkbox.Changed) -> None:
        self.update_preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-cancel":
            self.dismiss(None)
        elif event.button.id == "new-create":
            self.create()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_create(self) -> None:
        self.create()

    def action_word_delete(self) -> None:
        delete_focused_input_word(self.app.focused)

    def values(self) -> NewSessionOptions:
        title = self.query_one("#new-session-title", Input).value.strip()
        slug = self.query_one("#new-slug", Input).value.strip() or None
        branch = self.query_one("#new-branch", Input).value.strip() or None
        base_value = self.query_one("#new-base", Input).value.strip()
        base = None if base_value in {"", "origin/main"} else base_value
        mode = str(self.query_one("#new-mode", Select).value)
        model = self.query_one("#new-model", Input).value.strip() or None
        effort = str(self.query_one("#new-effort", Select).value)
        classic = self.query_one("#new-classic", Checkbox).value
        rc = self.query_one("#new-rc", Checkbox).value
        prompt = self.query_one("#new-prompt", Checkbox).value
        return NewSessionOptions(
            title=title,
            slug=slug,
            branch=branch,
            base=base,
            mode=default_choice(mode),
            model=model,
            effort=default_choice(effort),
            native=not classic,
            rc=rc,
            prompt=prompt,
        )

    def create(self) -> None:
        options = self.values()
        error = self.query_one("#new-error", Static)
        if not options.title:
            error.update("Title is required.")
            return
        slug = options.slug or slugify(options.title)
        if not slug:
            error.update("Could not derive a slug; enter one manually.")
            return
        error.update("")
        self.dismiss(options)

    def update_preview(self) -> None:
        options = self.values()
        slug = options.slug or slugify(options.title) or "<slug>"
        branch = options.branch or f"{BRANCH_PREFIX}{slug}"
        base = options.base or "origin/main (fetch first)"
        tmux = "--tmux" if options.native else "--tmux=classic"
        extras = []
        if options.mode:
            extras.append(f"--mode {options.mode}")
        if options.model:
            extras.append(f"--model {options.model}")
        if options.effort:
            extras.append(f"--effort {options.effort}")
        if not options.rc:
            extras.append("--no-rc")
        if not options.prompt:
            extras.append("--no-prompt")
        preview = [
            f"[bold]slug[/bold]    {slug}",
            f"[bold]branch[/bold]  {branch}",
            f"[bold]base[/bold]    {base}",
            f"[bold]launch[/bold]  claude --worktree {slug} {tmux} --name {options.title or '<title>'} "
            f"{' '.join(extras)}".rstrip(),
        ]
        self.query_one("#new-branch-preview", Static).update(branch)
        self.query_one("#new-preview", Static).update("\n".join(preview))


class ResumeSessionScreen(ModalScreen[ResumeOptions | None]):
    CSS = """
    ResumeSessionScreen {
        align: center middle;
    }

    #resume-dialog {
        width: 76;
        max-width: 94%;
        height: auto;
        padding: 1 2;
        border: solid #f59e0b;
        background: #111827;
    }

    #resume-title {
        height: 1;
        margin-bottom: 1;
        color: #e2e8f0;
        text-style: bold;
    }

    .resume-row {
        height: 3;
    }

    .resume-cell {
        width: 1fr;
        height: 3;
        margin-right: 1;
    }

    .resume-label {
        height: 1;
        color: #94a3b8;
    }

    #resume-note {
        min-height: 2;
        color: #cbd5e1;
    }

    #resume-buttons {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }

    Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("enter", "launch", "launch", priority=True),
        *WORD_DELETE_BINDINGS,
    ]

    def __init__(self, session: WorktreeSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with Container(id="resume-dialog"):
            yield Static(f"Resume {self.session.slug}", id="resume-title")
            yield Static("No live tmux session exists. Relaunch Claude on this worktree.", id="resume-note")
            with Horizontal(classes="resume-row"):
                with Vertical(classes="resume-cell"):
                    yield Label("Conversation", classes="resume-label")
                    yield Select(
                        [
                            ("continue previous conversation", "continue"),
                            ("fresh conversation", "fresh"),
                            ("open Claude picker", "pick"),
                        ],
                        value="continue",
                        allow_blank=False,
                        compact=True,
                        id="resume-conversation",
                    )
                with Vertical(classes="resume-cell"):
                    yield Label("Permission mode", classes="resume-label")
                    yield Select(
                        [
                            ("default", "default"),
                            ("manual", "manual"),
                            ("accept", "accept"),
                            ("plan", "plan"),
                            ("auto", "auto"),
                            ("bypass", "bypass"),
                        ],
                        value="default",
                        allow_blank=False,
                        compact=True,
                        id="resume-mode",
                    )
            with Horizontal(classes="resume-row"):
                with Vertical(classes="resume-cell"):
                    yield Label("Model", classes="resume-label")
                    yield Input(placeholder="blank => Claude default", compact=True, id="resume-model")
                with Vertical(classes="resume-cell"):
                    yield Label("Effort", classes="resume-label")
                    yield Select(
                        [
                            ("default", "default"),
                            ("low", "low"),
                            ("medium", "medium"),
                            ("high", "high"),
                            ("xhigh", "xhigh"),
                            ("max", "max"),
                        ],
                        value="default",
                        allow_blank=False,
                        compact=True,
                        id="resume-effort",
                    )
            with Horizontal(classes="resume-row"):
                yield Checkbox("Remote Control", value=True, compact=True, id="resume-rc")
                yield Checkbox("session prompt", value=True, compact=True, id="resume-prompt")
            with Horizontal(id="resume-buttons"):
                yield Button("Cancel", id="resume-cancel")
                yield Button("Launch", variant="primary", id="resume-launch")

    def on_mount(self) -> None:
        self.query_one("#resume-conversation", Select).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "resume-cancel":
            self.dismiss(None)
        elif event.button.id == "resume-launch":
            self.launch()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_launch(self) -> None:
        self.launch()

    def action_word_delete(self) -> None:
        delete_focused_input_word(self.app.focused)

    def values(self) -> ResumeOptions:
        conversation = str(self.query_one("#resume-conversation", Select).value)
        mode = str(self.query_one("#resume-mode", Select).value)
        model = self.query_one("#resume-model", Input).value.strip() or None
        effort = str(self.query_one("#resume-effort", Select).value)
        return ResumeOptions(
            fresh=conversation == "fresh",
            pick=conversation == "pick",
            mode=default_choice(mode),
            model=model,
            effort=default_choice(effort),
            rc=self.query_one("#resume-rc", Checkbox).value,
            prompt=self.query_one("#resume-prompt", Checkbox).value,
        )

    def launch(self) -> None:
        self.dismiss(self.values())


class RemoveSessionScreen(ModalScreen[str | None]):
    CSS = """
    RemoveSessionScreen {
        align: center middle;
    }

    #remove-dialog {
        width: 84;
        max-width: 94%;
        height: 18;
        padding: 1 2;
        border: solid #ef4444;
        background: #111827;
    }

    #remove-title {
        height: 1;
        margin-bottom: 1;
        color: #fecaca;
        text-style: bold;
    }

    #remove-details {
        height: 7;
        padding: 1;
        border: solid #334155;
        background: #0b1220;
        color: #cbd5e1;
    }

    #remove-warning {
        height: 2;
        margin-top: 1;
        color: #fca5a5;
    }

    #remove-actions {
        height: 1;
        margin-top: 1;
        color: #cbd5e1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("r", "remove", "remove"),
        Binding("f", "force", "force"),
    ]

    def __init__(self, session: WorktreeSession, root: Path) -> None:
        super().__init__()
        self.session = session
        self.root = root

    def compose(self) -> ComposeResult:
        with Container(id="remove-dialog"):
            yield Static(f"Remove {self.session.slug}", id="remove-title")
            yield Static(self.details_text(), id="remove-details")
            yield Static(self.warning_text(), id="remove-warning")
            yield Static(self.actions_text(), id="remove-actions")

    @property
    def can_remove_normally(self) -> bool:
        return self.session.dirty_count == 0 and self.session.unpushed_count == 0

    def display_path(self) -> Path:
        if self.session.path.is_absolute():
            try:
                return self.session.path.relative_to(self.root)
            except ValueError:
                return self.session.path
        return self.session.path

    def details_text(self) -> str:
        dirty = f"{self.session.dirty_count} file(s)"
        unpushed = f"{self.session.unpushed_count} commit(s)"
        return "\n".join(
            [
                f"[bold]Worktree[/bold]  {escape(str(self.display_path()))}",
                f"[bold]Branch[/bold]    {escape(self.session.branch)}",
                f"[bold]Tmux[/bold]      {escape(self.session.tmux)}",
                f"[bold]Dirty[/bold]     {dirty:<14} [bold]Unpushed[/bold]  {unpushed}",
                "",
                "Removes the worktree and deletes the discovered branch.",
            ]
        )

    def warning_text(self) -> str:
        if self.can_remove_normally:
            return "[dim]Normal remove is allowed: clean tree, no unpushed commits.[/dim]"
        return "[bold]Normal remove blocked.[/bold] Force can discard local work."

    def actions_text(self) -> Text:
        remove = "[r] remove" if self.can_remove_normally else "[r] remove blocked"
        return Text(f"{remove}  [f] force remove  [esc] back")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_remove(self) -> None:
        if self.can_remove_normally:
            self.dismiss("remove")
        else:
            self.bell()

    def action_force(self) -> None:
        self.dismiss("force")


class KillSessionScreen(ModalScreen[bool]):
    CSS = """
    KillSessionScreen {
        align: center middle;
    }

    #kill-dialog {
        width: 72;
        max-width: 94%;
        height: 12;
        padding: 1 2;
        border: solid #f59e0b;
        background: #111827;
    }

    #kill-title {
        height: 1;
        margin-bottom: 1;
        color: #fed7aa;
        text-style: bold;
    }

    #kill-details {
        height: 4;
        padding: 1;
        border: solid #334155;
        background: #0b1220;
        color: #cbd5e1;
    }

    #kill-actions {
        height: 1;
        margin-top: 1;
        color: #cbd5e1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("k", "kill", "kill"),
    ]

    def __init__(self, session: WorktreeSession, root: Path) -> None:
        super().__init__()
        self.session = session
        self.root = root

    def compose(self) -> ComposeResult:
        with Container(id="kill-dialog"):
            yield Static(f"Kill {self.session.slug}", id="kill-title")
            yield Static(self.details_text(), id="kill-details")
            yield Static(Text("[k] kill tmux  [esc] back"), id="kill-actions")

    def details_text(self) -> str:
        return "\n".join(
            [
                f"[bold]Tmux[/bold]     {escape(self.session.tmux)}",
                f"[bold]Session[/bold]  {escape(session_name(self.root, self.session.slug))}",
                "Leaves the worktree and branch intact.",
            ]
        )

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_kill(self) -> None:
        self.dismiss(True)


class DoctorScreen(ModalScreen[None]):
    CSS = """
    DoctorScreen {
        align: center middle;
    }

    #doctor-dialog {
        width: 78;
        max-width: 94%;
        height: auto;
        padding: 1 2;
        border: solid #38bdf8;
        background: #111827;
    }

    #doctor-title {
        height: 1;
        margin-bottom: 1;
        color: #e2e8f0;
        text-style: bold;
    }

    #doctor-body {
        min-height: 9;
        color: #cbd5e1;
    }

    #doctor-buttons {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }

    Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("r", "refresh", "refresh"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="doctor-dialog"):
            yield Static("Doctor", id="doctor-title")
            yield Static("", id="doctor-body")
            with Horizontal(id="doctor-buttons"):
                yield Button("Refresh", id="doctor-refresh")
                yield Button("Back", variant="primary", id="doctor-back")

    def on_mount(self) -> None:
        self.refresh_doctor()

    def body_text(self) -> str:
        lines = []
        for name, result in doctor_rows():
            style = "green" if result and not result.startswith(("missing", "unknown")) else "yellow"
            lines.append(f"[bold]{name:<14}[/bold] [{style}]{escape(result)}[/{style}]")
        lines.append("")
        lines.append("[dim][r] refresh  [esc] back[/dim]")
        return "\n".join(lines)

    def refresh_doctor(self) -> None:
        self.query_one("#doctor-body", Static).update(self.body_text())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "doctor-refresh":
            self.refresh_doctor()
        elif event.button.id == "doctor-back":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self.refresh_doctor()


class StatusScreen(ModalScreen[str | None]):
    CSS = """
    StatusScreen {
        align: center middle;
    }

    #status-dialog {
        width: 82;
        max-width: 94%;
        height: auto;
        padding: 1 2;
        border: solid #22c55e;
        background: #111827;
    }

    #status-title {
        height: 1;
        margin-bottom: 1;
        color: #dcfce7;
        text-style: bold;
    }

    #status-body {
        min-height: 14;
        color: #cbd5e1;
    }

    #status-buttons {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }

    Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("enter", "resume", "resume"),
    ]

    def __init__(self, session: WorktreeSession, root: Path) -> None:
        super().__init__()
        self.session = session
        self.root = root

    def compose(self) -> ComposeResult:
        with Container(id="status-dialog"):
            yield Static(f"Status {self.session.slug}", id="status-title")
            yield Static(self.body_text(), id="status-body")
            with Horizontal(id="status-buttons"):
                yield Button("Back", id="status-back")
                yield Button("Resume", variant="primary", id="status-resume")

    def display_path(self) -> Path:
        if self.session.path.is_absolute():
            try:
                return self.session.path.relative_to(self.root)
            except ValueError:
                return self.session.path
        return self.session.path

    def body_text(self) -> str:
        return "\n".join(
            [
                f"[bold]Repo[/bold]      {self.root}",
                f"[bold]Worktree[/bold]  {self.display_path()}",
                f"[bold]Branch[/bold]    {self.session.branch}",
                f"[bold]Upstream[/bold]  {self.session.upstream or '-'}",
                f"[bold]Tmux[/bold]      {self.session.tmux}",
                f"[bold]Session[/bold]   {session_name(self.root, self.session.slug)}",
                f"[bold]Dirty[/bold]     {self.session.dirty_count} file(s)",
                f"[bold]Unpushed[/bold]  {self.session.unpushed_count} commit(s)",
                "",
                "[bold]Recent[/bold]",
                f"  {self.session.last_commit or '-'}",
                "",
                "[bold]Linked work[/bold]",
                "  PR      -",
                "  Linear  -",
                "  Jira    -",
                "",
                "[dim][enter] resume  [esc] back[/dim]",
            ]
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "status-back":
            self.dismiss(None)
        elif event.button.id == "status-resume":
            self.dismiss("resume")

    def action_close(self) -> None:
        self.dismiss(None)

    def action_resume(self) -> None:
        self.dismiss("resume")


class PruneScreen(ModalScreen[bool]):
    CSS = """
    PruneScreen {
        align: center middle;
    }

    #prune-dialog {
        width: 104;
        max-width: 96%;
        height: 22;
        padding: 1 2;
        border: solid #a78bfa;
        background: #111827;
    }

    #prune-title {
        height: 1;
        margin-bottom: 1;
        color: #ede9fe;
        text-style: bold;
    }

    #prune-status {
        height: 1;
        margin-top: 1;
        color: #94a3b8;
    }

    #prune-body {
        height: 1fr;
    }

    #prune-remove-pane {
        width: 52%;
        height: 100%;
        margin-right: 1;
    }

    #prune-keep-pane {
        width: 48%;
        height: 100%;
    }

    .prune-pane-title {
        height: 1;
        color: #cbd5e1;
        text-style: bold;
    }

    #prune-remove-list,
    #prune-keep-list {
        height: 1fr;
        padding: 1;
        border: solid #334155;
        background: #0b1220;
    }

    #prune-tmux {
        height: 4;
        margin-top: 1;
        color: #94a3b8;
    }

    #prune-actions {
        height: 1;
        margin-top: 1;
        color: #cbd5e1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "back"),
        Binding("a", "apply", "apply"),
        Binding("r", "refresh", "refresh"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.applied = False
        self.rows: list[PruneRow] = []

    def compose(self) -> ComposeResult:
        with Container(id="prune-dialog"):
            yield Static("Prune", id="prune-title")
            yield Static("", id="prune-status")
            with Horizontal(id="prune-body"):
                with Vertical(id="prune-remove-pane"):
                    yield Static("Will Remove", classes="prune-pane-title")
                    yield Static("", id="prune-remove-list")
                with Vertical(id="prune-keep-pane"):
                    yield Static("Kept", classes="prune-pane-title")
                    yield Static("", id="prune-keep-list")
            yield Static("", id="prune-tmux")
            yield Static(Text("[a] apply  [r] refresh  [esc] back"), id="prune-actions")

    def on_mount(self) -> None:
        self.preview()

    def status(self, message: str) -> None:
        self.query_one("#prune-status", Static).update(message)

    def preview(self) -> None:
        self.rows = self.build_rows()
        self.populate_tables()
        removable = len([row for row in self.rows if row.action == "remove"])
        kept = len(self.rows) - removable
        self.status(f"{removable} removable, {kept} kept.")

    def build_rows(self) -> list[PruneRow]:
        rows = []
        for s in list_sessions(self.root):
            wt = self.root / WORKTREE_DIR / s.slug
            if tmux_alive(session_name(self.root, s.slug)):
                rows.append(PruneRow(s, "keep", f"session alive ({s.tmux})"))
                continue
            if git(wt, "status", "--porcelain", check=False).strip():
                rows.append(PruneRow(s, "keep", "uncommitted changes"))
                continue
            unpushed = 0
            if s.branch and s.branch != "(detached)":
                unpushed = int(
                    git(self.root, "rev-list", "--count", s.branch, "--not", "--remotes", check=False).strip()
                    or "0"
                )
            if unpushed > 0:
                rows.append(PruneRow(s, "keep", f"{unpushed} unpushed commit(s)"))
                continue
            rows.append(PruneRow(s, "remove", "dead, clean, pushed"))
        return rows

    def populate_tables(self) -> None:
        remove_rows = [row for row in self.rows if row.action == "remove"]
        keep_rows = [row for row in self.rows if row.action == "keep"]
        self.query_one("#prune-remove-list", Static).update(self.rows_text(remove_rows, remove=True))
        self.query_one("#prune-keep-list", Static).update(self.rows_text(keep_rows, remove=False))
        self.query_one("#prune-tmux", Static).update(self.tmux_text())

    def rows_text(self, rows: list[PruneRow], *, remove: bool) -> str:
        if not rows:
            return "[dim]Nothing eligible.[/dim]" if remove else "[dim]Nothing kept.[/dim]"
        lines = []
        for row in rows[:6]:
            s = row.session
            if remove:
                lines.append(f"[bold]{escape(s.slug)}[/bold]  [dim]{escape(s.branch)}[/dim]")
                lines.append(f"  {escape(row.reason)}")
            else:
                lines.append(f"[bold]{escape(s.slug)}[/bold]  {escape(row.reason)}")
        extra = len(rows) - 6
        if extra > 0:
            lines.append(f"[dim]+ {extra} more[/dim]")
        return "\n".join(lines)

    def tmux_text(self) -> str:
        lines = machine_worktree_tmux_lines(self.root.name)
        if not lines:
            return "[bold]Machine tmux[/bold]\n  no cw worktree sessions found"
        return "\n".join(["[bold]Machine tmux[/bold]", *[f"  {escape(line)}" for line in lines[:3]]])

    def apply_prune_row(self, row: PruneRow) -> bool:
        s = row.session
        cp = run(["git", "worktree", "remove", str(WORKTREE_DIR / s.slug)], cwd=self.root, check=False)
        if cp.returncode != 0:
            return False
        if s.branch and s.branch != "(detached)":
            exists = run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{s.branch}"],
                cwd=self.root,
                check=False,
            )
            if exists.returncode == 0:
                run(["git", "branch", "-D", s.branch], cwd=self.root, check=False, capture=True)
        return True

    def apply_prune(self) -> None:
        removable = [row for row in self.rows if row.action == "remove"]
        removed = 0
        failed = 0
        for row in removable:
            if self.apply_prune_row(row):
                removed += 1
            else:
                failed += 1
        run(["git", "worktree", "prune"], cwd=self.root, check=False)
        self.applied = True
        self.rows = self.build_rows()
        self.populate_tables()
        kept = len([row for row in self.rows if row.action == "keep"])
        suffix = f", {failed} failed" if failed else ""
        self.status(f"Removed {removed}, {kept} kept{suffix}.")

    def action_close(self) -> None:
        self.dismiss(self.applied)

    def action_apply(self) -> None:
        self.apply_prune()

    def action_refresh(self) -> None:
        self.preview()


class LaunchProgressScreen(ModalScreen[TuiResult | None]):
    CSS = """
    LaunchProgressScreen {
        align: center middle;
    }

    #launch-dialog {
        width: 104;
        max-width: 96%;
        height: 24;
        padding: 1 2;
        border: solid #38bdf8;
        background: #111827;
    }

    #launch-title {
        height: 1;
        margin-bottom: 1;
        color: #e2e8f0;
        text-style: bold;
    }

    #launch-log {
        height: 1fr;
        border: solid #334155;
        background: #0b1220;
    }

    #launch-status {
        height: 1;
        margin-top: 1;
        color: #94a3b8;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "back"),
    ]

    def __init__(self, options: NewSessionOptions) -> None:
        super().__init__()
        self.options = options
        self.finished = False
        self.failed = False

    def compose(self) -> ComposeResult:
        with Container(id="launch-dialog"):
            yield Static(f"Launching {self.options.title}", id="launch-title")
            yield RichLog(highlight=True, markup=True, wrap=True, id="launch-log")
            yield Static("Preparing worktree...", id="launch-status")

    def on_mount(self) -> None:
        self.query_one("#launch-log", RichLog).write("[bold]cw new[/bold]")
        thread = threading.Thread(target=self.prepare, name="cw-launch-progress", daemon=True)
        thread.start()

    def append_log(self, line: str) -> None:
        self.query_one("#launch-log", RichLog).write(escape(line))

    def set_status(self, message: str) -> None:
        self.query_one("#launch-status", Static).update(message)

    def prepare(self) -> None:
        try:
            plan = prepare_new_session(
                title_parts=[self.options.title],
                slug=self.options.slug,
                branch=self.options.branch,
                base=self.options.base,
                mode=self.options.mode,
                model=self.options.model,
                effort=self.options.effort,
                prompt_file=None,
                no_prompt=not self.options.prompt,
                native=self.options.native,
                no_rc=not self.options.rc,
                log=lambda line: self.app.call_from_thread(self.append_log, line),
            )
        except Exception as exc:
            self.failed = True
            self.app.call_from_thread(self.append_log, f"cw: {exc}")
            self.app.call_from_thread(self.set_status, "Launch failed. Press Esc to return.")
            return
        self.finished = True
        self.app.call_from_thread(self.append_log, "cw: handing off to Claude/tmux")
        self.app.call_from_thread(self.set_status, "Opening Claude...")
        self.app.call_from_thread(self.dismiss, TuiResult("exec", exec_root=plan.root, exec_args=plan.args))

    def action_cancel(self) -> None:
        if self.failed:
            self.dismiss(None)
        else:
            self.bell()


class CwTui(App[TuiResult | None]):
    TITLE = "cw"
    SUB_TITLE = "Claude worktrees"

    CSS = """
    Screen {
        background: #101418;
        color: #d7dee8;
    }

    #top {
        height: 4;
        padding: 0 1;
        border-bottom: solid #334155;
        background: #151b22;
    }

    #repo {
        height: 1;
        color: #e2e8f0;
    }

    #worktrees {
        height: 1;
        color: #94a3b8;
    }

    #filter-row {
        height: 1;
    }

    #filter-label {
        width: 9;
        color: #94a3b8;
    }

    #filter {
        width: 1fr;
        height: 1;
        border: none;
        background: #1f2937;
        color: #e5e7eb;
    }

    #filter:focus {
        background: #0f172a;
        color: #ffffff;
        text-style: bold;
    }

    #body {
        height: 1fr;
    }

    #sessions {
        width: 65%;
        height: 100%;
        border-right: solid #334155;
    }

    #details {
        width: 35%;
        height: 100%;
        padding: 1 2;
        overflow-y: auto;
        background: #0f1720;
    }

    #actions {
        height: 3;
        padding: 0 1;
        border-top: solid #334155;
        background: #151b22;
        color: #cbd5e1;
    }
    """

    BINDINGS = [
        Binding("n", "new", "[n] new", show=False),
        Binding("enter", "resume", "[enter] resume", show=False),
        Binding("s", "status", "[s] status", show=False),
        Binding("/", "focus_filter", "[/] filter", show=False),
        Binding("escape", "exit_filter", "[esc] table", show=False),
        Binding("x", "remove", "[x] remove", show=False),
        Binding("k", "kill", "[k] kill", show=False),
        Binding("p", "prune", "[p] prune", show=False),
        Binding("d", "doctor", "[d] doctor", show=False),
        Binding("?", "help", "[?] help", show=False),
        Binding("q", "quit", "[q] quit", show=False),
        *WORD_DELETE_BINDINGS,
    ]

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.sessions: list[WorktreeSession] = []
        self.visible_sessions: list[WorktreeSession] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, icon="")
        with Vertical(id="top"):
            yield Static(f"Repo  {self.root}", id="repo")
            yield Static(f"Worktrees  {WORKTREE_DIR}", id="worktrees")
            with Horizontal(id="filter-row"):
                yield Static("Filter", id="filter-label")
                yield Input(placeholder="type to filter; enter/esc returns to table", compact=True, id="filter")
        with Horizontal(id="body"):
            yield DataTable(id="sessions")
            yield Static("", id="details")
        yield Static(
            Text(
                "[n] new  [enter] resume  [s] status  [/] filter  [x] remove  [k] kill\n"
                "[p] prune  [d] doctor  [?] help  [q] quit"
            ),
            id="actions",
        )
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        table = self.query_one("#sessions", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("NAME", "TMUX", "BRANCH", "STATE", "TITLE")
        self.refresh_sessions()
        table.focus()

    def refresh_sessions(self) -> None:
        self.sessions = list_sessions(self.root)
        self.apply_filter(self.query_one("#filter", Input).value)

    def apply_filter(self, needle: str) -> None:
        needle = needle.strip().lower()
        if needle:
            self.visible_sessions = [
                s
                for s in self.sessions
                if needle
                in " ".join([s.slug, s.branch, s.title, s.tmux, session_state(s)]).lower()
            ]
        else:
            self.visible_sessions = list(self.sessions)
        table = self.query_one("#sessions", DataTable)
        table.clear()
        for s in self.visible_sessions:
            table.add_row(
                s.slug,
                state_text(s.tmux),
                s.branch,
                state_text(session_state(s)),
                s.title,
                key=s.slug,
            )
        if self.visible_sessions:
            table.cursor_coordinate = (0, 0)
        self.update_details()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            self.focus_sessions()

    def on_data_table_row_highlighted(self, _: DataTable.RowHighlighted) -> None:
        self.update_details()

    def on_data_table_row_selected(self, _: DataTable.RowSelected) -> None:
        self.action_resume()

    def current_session(self) -> WorktreeSession | None:
        table = self.query_one("#sessions", DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self.visible_sessions):
            return None
        return self.visible_sessions[row]

    def update_details(self) -> None:
        details = self.query_one("#details", Static)
        s = self.current_session()
        if s is None:
            details.update(
                "[bold]Details[/bold]\n\n"
                "No matching sessions.\n\n"
                "Use [n] to create a session or [/] to change the filter."
            )
            return
        display_path = s.path
        if s.path.is_absolute():
            try:
                display_path = s.path.relative_to(self.root)
            except ValueError:
                display_path = s.path
        details.update(
            "\n".join(
                [
                    "[bold]Details[/bold]",
                    "",
                    f"[bold]Name[/bold]       {s.slug}",
                    f"[bold]Title[/bold]      {s.title or ''}",
                    f"[bold]Branch[/bold]     {s.branch}",
                    f"[bold]Upstream[/bold]   {s.upstream or '-'}",
                    f"[bold]Tmux[/bold]       {s.tmux}",
                    f"[bold]Session[/bold]    {session_name(self.root, s.slug)}",
                    f"[bold]Path[/bold]       {display_path}",
                    f"[bold]Dirty[/bold]      {s.dirty_count} file(s)",
                    f"[bold]Unpushed[/bold]   {s.unpushed_count} commit(s)",
                    "",
                    "[bold]Recent[/bold]",
                    f"  {s.last_commit or '-'}",
                    "",
                    "[dim]GitHub PR detection can land later via gh when available.[/dim]",
                ]
            )
        )

    def selected_result(self, action: str) -> TuiResult | None:
        s = self.current_session()
        if s is None:
            self.bell()
            return None
        return TuiResult(action, s.slug)

    def action_new(self) -> None:
        self.push_screen(NewSessionScreen(), self.new_session_done)

    def new_session_done(self, options: NewSessionOptions | None) -> None:
        if options is not None:
            self.push_screen(LaunchProgressScreen(options), self.launch_progress_done)

    def launch_progress_done(self, result: TuiResult | None) -> None:
        if result is not None:
            self.exit(result)

    def action_resume(self) -> None:
        session = self.current_session()
        if session is None:
            self.bell()
            return
        if session.tmux == "dead":
            self.push_screen(ResumeSessionScreen(session), lambda opts: self.resume_session_done(session, opts))
            return
        self.exit(TuiResult("resume", session.slug))

    def resume_session_done(self, session: WorktreeSession, options: ResumeOptions | None) -> None:
        if options is not None:
            self.exit(TuiResult("resume", session.slug, resume_options=options))

    def action_status(self) -> None:
        session = self.current_session()
        if session is None:
            self.bell()
            return
        self.push_screen(StatusScreen(session, self.root), lambda choice: self.status_done(session, choice))

    def status_done(self, session: WorktreeSession, choice: str | None) -> None:
        if choice == "resume":
            if session.tmux == "dead":
                self.push_screen(ResumeSessionScreen(session), lambda opts: self.resume_session_done(session, opts))
            else:
                self.exit(TuiResult("resume", session.slug))

    def action_remove(self) -> None:
        session = self.current_session()
        if session is None:
            self.bell()
            return
        self.push_screen(RemoveSessionScreen(session, self.root), lambda choice: self.remove_session_done(session, choice))

    def remove_session_done(self, session: WorktreeSession, choice: str | None) -> None:
        if choice not in {"remove", "force"}:
            return
        try:
            result = remove_session(self.root, session.slug, force=choice == "force")
        except CwError as exc:
            self.refresh_sessions()
            self.query_one("#details", Static).update(
                "\n".join(
                    [
                        "[bold red]Remove Failed[/bold red]",
                        "",
                        escape(str(exc)),
                        "",
                        "[dim]Use [x] to reopen remove, or [/] to filter sessions.[/dim]",
                    ]
                )
            )
            return
        self.refresh_sessions()
        details = [
            "[bold green]Removed[/bold green]",
            "",
            f"[bold]Name[/bold]      {escape(result.slug)}",
            f"[bold]Worktree[/bold]  {escape(str(result.worktree))}",
            f"[bold]Branch[/bold]    {escape(result.branch or '-')}",
            f"[bold]Tmux[/bold]      {'killed' if result.killed_tmux else 'not running'}",
            f"[bold]Cleanup[/bold]   {'branch deleted' if result.branch_deleted else 'branch not found'}",
            "",
            "[dim]Dashboard refreshed.[/dim]",
        ]
        self.query_one("#details", Static).update("\n".join(details))

    def action_kill(self) -> None:
        session = self.current_session()
        if session is None:
            self.bell()
            return
        self.push_screen(KillSessionScreen(session, self.root), lambda confirmed: self.kill_session_done(session, confirmed))

    def kill_session_done(self, session: WorktreeSession, confirmed: bool | None) -> None:
        if not confirmed:
            return
        try:
            result = kill_session(self.root, session.slug)
        except CwError as exc:
            self.refresh_sessions()
            self.query_one("#details", Static).update(
                "\n".join(["[bold red]Kill Failed[/bold red]", "", escape(str(exc))])
            )
            return
        self.refresh_sessions()
        self.query_one("#details", Static).update(
            "\n".join(
                [
                    "[bold green]Killed Tmux[/bold green]",
                    "",
                    f"[bold]Name[/bold]     {escape(result.slug)}",
                    f"[bold]Session[/bold]  {escape(result.session)}",
                    "",
                    "[dim]Worktree and branch left intact.[/dim]",
                ]
            )
        )

    def action_prune(self) -> None:
        self.push_screen(PruneScreen(self.root), self.prune_done)

    def prune_done(self, applied: bool | None) -> None:
        if applied:
            self.refresh_sessions()

    def action_doctor(self) -> None:
        self.push_screen(DoctorScreen())

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_word_delete(self) -> None:
        delete_focused_input_word(self.focused)

    def action_exit_filter(self) -> None:
        if self.query_one("#filter", Input).has_focus:
            self.focus_sessions()
        else:
            self.exit(None)

    def focus_sessions(self) -> None:
        self.query_one("#sessions", DataTable).focus()

    def action_help(self) -> None:
        def key(label: str) -> str:
            return f"[bold]{escape(label)}[/bold]"

        self.query_one("#details", Static).update(
            "\n".join(
                [
                    "[bold]Keyboard[/bold]",
                    "",
                    f"{key('[n]')}      create a new session",
                    f"{key('[ctrl+enter]')} create from the new-session form",
                    f"{key('[enter]')}  resume selected session",
                    f"{key('[s]')}      print selected session status",
                    f"{key('[/]')}      focus the filter",
                    f"{key('[esc]')}    leave filter or quit",
                    f"{key('[x]')}      remove selected session, with confirmation",
                    f"{key('[k]')}      kill selected tmux session, with confirmation",
                    f"{key('[p]')}      preview prune, then optionally apply",
                    f"{key('[d]')}      run doctor",
                    f"{key('[q]')}      quit",
                    f"{key('[ctrl+w]')} delete the previous word in text fields",
                    "",
                    "[dim]The TUI exits into the same command implementations used by the CLI, "
                    "so safety checks stay shared.[/dim]",
                ]
            )
        )


def run_tui() -> None:
    result = CwTui(repo_root()).run()
    if result is None:
        raise typer.Exit(0)
    if result.action == "exec" and result.exec_root and result.exec_args:
        exec_from(result.exec_root, result.exec_args)
    elif result.action == "new" and result.new_options:
        opts = result.new_options
        new(
            title=[opts.title],
            slug=opts.slug,
            branch=opts.branch,
            base=opts.base,
            mode=opts.mode,
            model=opts.model,
            effort=opts.effort,
            prompt_file=None,
            no_prompt=not opts.prompt,
            native=opts.native,
            no_rc=not opts.rc,
        )
    elif result.action == "resume" and result.slug:
        opts = result.resume_options
        resume(
            result.slug,
            fresh=opts.fresh if opts else False,
            pick=opts.pick if opts else False,
            mode=opts.mode if opts else None,
            model=opts.model if opts else None,
            effort=opts.effort if opts else None,
            prompt_file=None,
            no_prompt=not opts.prompt if opts else False,
            no_rc=not opts.rc if opts else False,
        )


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
    rc = questionary.confirm("Enable Remote Control?", default=True).ask()
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
    )


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
) -> None:
    del classic
    if fresh and pick:
        raise CwError("--fresh and --pick are mutually exclusive")
    require_cmd("tmux")
    mode_value(mode)
    validate_effort(effort)
    root = repo_root()
    sess = session_name(root, name)
    if tmux_alive(sess):
        if fresh or pick or mode:
            info("session is live; attaching (--fresh/--pick/--mode ignored)")
        exec_from(root, ["tmux", "attach-session", "-t", f"={sess}"])
    wt = WORKTREE_DIR / name
    if not (root / wt).is_dir():
        raise CwError(f"no tmux session and no worktree named '{name}' (looked in {wt})")
    require_cmd("claude")
    branch = worktree_branch(root, name)
    title = branch_description(root, branch) or display_name(root, name)
    info(f"no live tmux session; relaunching claude on existing worktree {wt}")
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
    info(f"launching: {' '.join(args)}")
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
    )
    exec_from(root, args)


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
