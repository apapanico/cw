from __future__ import annotations

import threading
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Checkbox, DataTable, Footer, Header, Input, Label, RichLog, Select, Static

from .models import BRANCH_PREFIX, WORKTREE_DIR, NewSessionOptions, PruneRow, ResumeOptions, TuiResult, WorktreeSession, CwError
from .operations import (
    branch_description,
    default_choice,
    dirty_count,
    display_name,
    doctor_rows,
    exec_from,
    git,
    happy_mode_value,
    kill_session,
    last_commit,
    list_sessions,
    machine_worktree_tmux_lines,
    prepare_new_session,
    prepare_resume_session,
    remove_session,
    repo_root,
    session_name,
    session_state,
    slugify,
    tmux_alive,
    unpushed_count,
    worktree_branch,
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
        width: 94;
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
        height: 2;
        margin-top: 0;
    }

    .form-cell {
        width: 1fr;
        height: 2;
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
        height: 1;
        margin-top: 0;
    }

    #new-summary {
        height: 2;
        margin-top: 1;
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
        margin-top: 0;
        color: #f87171;
    }

    #new-actions {
        height: 1;
        margin-top: 1;
        color: #cbd5e1;
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
                    yield Input(placeholder="blank => Claude default; Happy sonnet", compact=True, id="new-model")
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
                        yield Checkbox("classic", value=True, compact=True, id="new-classic")
                        yield Checkbox("happy", value=False, compact=True, id="new-happy")
                        yield Checkbox("remote", value=True, compact=True, id="new-rc")
                        yield Checkbox("prompt", value=True, compact=True, id="new-prompt")
            yield Static("", id="new-summary")
            yield Static("", id="new-error")
            yield Static(Text("[ctrl+enter] create  [esc] back"), id="new-actions")

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
        happy = self.query_one("#new-happy", Checkbox).value
        rc = self.query_one("#new-rc", Checkbox).value and not happy
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
            happy=happy,
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
        happy_checkbox = self.query_one("#new-happy", Checkbox)
        rc_checkbox = self.query_one("#new-rc", Checkbox)
        rc_checkbox.disabled = happy_checkbox.value
        if happy_checkbox.value:
            rc_checkbox.value = False
        options = self.values()
        slug = options.slug or slugify(options.title) or "<slug>"
        branch = options.branch or f"{BRANCH_PREFIX}{slug}"
        base = options.base or "origin/main"
        tmux = "native tmux" if options.native else "classic tmux"
        extras = []
        if options.mode and not options.happy:
            extras.append(f"mode {options.mode}")
        if options.model:
            extras.append(f"model {options.model}")
        if options.effort:
            extras.append(f"effort {options.effort}")
        if options.happy:
            extras.append("happy mobile")
            extras.append(f"mode {happy_mode_value(options.mode)}")
        elif not options.rc:
            extras.append("no remote control")
        if not options.prompt:
            extras.append("no prompt")
        flags = ", ".join([tmux, *extras]) or tmux
        wt = f"{WORKTREE_DIR}/{slug}"
        base_note = "fetch latest" if options.base is None else "custom ref"
        preview = [
            f"[bold]Plan[/bold]  {escape(str(wt))}  ->  {escape(branch)}",
            f"      base {escape(base)} ({base_note}); {escape(flags)}",
        ]
        self.query_one("#new-branch-preview", Static).update(branch)
        self.query_one("#new-summary", Static).update("\n".join(preview))


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

    #resume-actions {
        height: 1;
        margin-top: 1;
        color: #cbd5e1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+enter", "launch", "launch", priority=True),
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
                    yield Input(placeholder="blank => Claude default; Happy sonnet", compact=True, id="resume-model")
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
                yield Checkbox("Happy", value=False, compact=True, id="resume-happy")
                yield Checkbox("Remote Control", value=True, compact=True, id="resume-rc")
                yield Checkbox("session prompt", value=True, compact=True, id="resume-prompt")
            yield Static(Text("[ctrl+enter] launch  [esc] back"), id="resume-actions")

    def on_mount(self) -> None:
        self.query_one("#resume-conversation", Select).focus()
        self.update_remote_state()

    def on_checkbox_changed(self, _: Checkbox.Changed) -> None:
        self.update_remote_state()

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
        happy = self.query_one("#resume-happy", Checkbox).value
        return ResumeOptions(
            fresh=conversation == "fresh",
            pick=conversation == "pick",
            mode=default_choice(mode),
            model=model,
            effort=default_choice(effort),
            rc=self.query_one("#resume-rc", Checkbox).value and not happy,
            happy=happy,
            prompt=self.query_one("#resume-prompt", Checkbox).value,
        )

    def launch(self) -> None:
        self.dismiss(self.values())

    def update_remote_state(self) -> None:
        happy_checkbox = self.query_one("#resume-happy", Checkbox)
        rc_checkbox = self.query_one("#resume-rc", Checkbox)
        rc_checkbox.disabled = happy_checkbox.value
        if happy_checkbox.value:
            rc_checkbox.value = False
        note = "Relaunch Happy on this worktree; Remote Control is disabled."
        if not happy_checkbox.value:
            note = "No live tmux session exists. Relaunch Claude on this worktree."
        self.query_one("#resume-note", Static).update(note)


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
        height: 3;
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

    #doctor-actions {
        height: 1;
        margin-top: 1;
        color: #cbd5e1;
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
            yield Static(Text("[r] refresh  [esc] back"), id="doctor-actions")

    def on_mount(self) -> None:
        self.refresh_doctor()

    def body_text(self) -> str:
        lines = []
        for name, result in doctor_rows():
            style = "green" if result and not result.startswith(("missing", "unknown")) else "yellow"
            lines.append(f"[bold]{name:<14}[/bold] [{style}]{escape(result)}[/{style}]")
        return "\n".join(lines)

    def refresh_doctor(self) -> None:
        self.query_one("#doctor-body", Static).update(self.body_text())

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

    #status-actions {
        height: 1;
        margin-top: 1;
        color: #cbd5e1;
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
            yield Static(Text("[enter] resume  [esc] back"), id="status-actions")

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
            ]
        )

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
                happy=self.options.happy,
                log=lambda line: self.app.call_from_thread(self.append_log, line),
            )
        except Exception as exc:
            self.failed = True
            self.app.call_from_thread(self.append_log, f"cw: {exc}")
            self.app.call_from_thread(self.set_status, "Launch failed. Press Esc to return.")
            return
        self.finished = True
        launcher = "Happy" if self.options.happy else "Claude"
        self.app.call_from_thread(self.append_log, f"cw: handing off to {launcher}/tmux")
        self.app.call_from_thread(self.set_status, f"Opening {launcher}...")
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
                    "[dim]CLI and TUI use the same shared operations, "
                    "so safety checks stay consistent.[/dim]",
                ]
            )
        )


def run_tui() -> None:
    result = CwTui(repo_root()).run()
    if result is None:
        return
    if result.action == "exec" and result.exec_root and result.exec_args:
        exec_from(result.exec_root, result.exec_args)
    elif result.action == "resume" and result.slug:
        opts = result.resume_options
        plan = prepare_resume_session(
            result.slug,
            fresh=opts.fresh if opts else False,
            pick=opts.pick if opts else False,
            mode=opts.mode if opts else None,
            model=opts.model if opts else None,
            effort=opts.effort if opts else None,
            prompt_file=None,
            no_prompt=not opts.prompt if opts else False,
            no_rc=not opts.rc if opts else False,
            happy=opts.happy if opts else False,
        )
        exec_from(plan.root, plan.args)
