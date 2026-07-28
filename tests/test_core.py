from pathlib import Path

import pytest

from cw.models import ModeError, WorktreeSession
from cw.operations import (
    claude_base_args,
    default_choice,
    display_name,
    exec_from,
    map_mode,
    parse_worktrees_porcelain,
    render_prompt_text,
    session_state,
    session_name,
    slugify,
    tmux_flag,
)


def test_slugify_matches_bash_behavior():
    assert slugify("SCRUM-241 perf") == "scrum-241-perf"
    assert slugify("  Hello, WORLD!! ") == "hello-world"
    assert slugify("!!!") == ""


@pytest.mark.parametrize(
    ("alias", "mapped"),
    [
        ("manual", "manual"),
        ("accept", "acceptEdits"),
        ("accept-edits", "acceptEdits"),
        ("plan", "plan"),
        ("auto", "auto"),
        ("bypass", "bypassPermissions"),
        ("yolo", "bypassPermissions"),
        ("dontask", "dontAsk"),
    ],
)
def test_map_mode(alias, mapped):
    assert map_mode(alias) == mapped


def test_map_mode_rejects_unknown():
    with pytest.raises(ModeError):
        map_mode("whatever")


def test_session_and_display_names():
    root = Path("/tmp/myrepo")
    assert session_name(root, "abc") == "myrepo_worktree-abc"
    assert display_name(root, "abc") == "myrepo/abc"


def test_render_prompt_text():
    text = "{{slug}} {{branch}} {{base}} {{title}} {{repo}} {{wt}}"
    assert (
        render_prompt_text(
            text,
            slug="s",
            branch="b",
            base="origin/main",
            title="Title",
            repo="repo",
            wt=".claude/worktrees/s",
        )
        == "s b origin/main Title repo .claude/worktrees/s"
    )


def test_parse_worktrees_porcelain_filters_cw_worktrees():
    text = """worktree /repo
HEAD abc
branch refs/heads/main

worktree /repo/.claude/worktrees/one
HEAD def
branch refs/heads/claude/one

worktree /repo/.claude/worktrees/two
HEAD ghi
detached

"""
    assert parse_worktrees_porcelain(text) == [
        (Path("/repo/.claude/worktrees/one"), "claude/one"),
        (Path("/repo/.claude/worktrees/two"), "(detached)"),
    ]


def test_tmux_flag_defaults_to_classic():
    assert tmux_flag(False) == "--tmux=classic"
    assert tmux_flag(True) == "--tmux"


def test_claude_base_args_preserves_remote_control_name_and_prompt_shape():
    assert claude_base_args(
        slug="slug",
        title="My Title",
        native=False,
        rc=True,
        mode="accept",
        model="sonnet",
        effort="high",
    ) == [
        "claude",
        "--worktree",
        "slug",
        "--tmux=classic",
        "--name",
        "My Title",
        "--remote-control",
        "--permission-mode",
        "acceptEdits",
        "--model",
        "sonnet",
        "--effort",
        "high",
    ]


def test_session_state_labels():
    base = {
        "slug": "s",
        "branch": "claude/s",
        "title": "S",
        "tmux": "dead",
        "path": Path("/repo/.claude/worktrees/s"),
    }
    assert session_state(WorktreeSession(**base)) == "clean"
    assert session_state(WorktreeSession(**base, dirty_count=2)) == "dirty"
    assert session_state(WorktreeSession(**base, unpushed_count=1)) == "unpushed"
    assert session_state(WorktreeSession(**base, dirty_count=2, unpushed_count=1)) == "dirty+unpushed"


def test_default_choice_normalizes_prompt_defaults():
    assert default_choice(None) is None
    assert default_choice("") is None
    assert default_choice("default") is None
    assert default_choice("accept") == "accept"


def test_tui_uses_keyboard_command_footers_not_buttons():
    source = (Path.cwd() / "src/cw/tui.py").read_text()
    assert "Button" not in source
    assert "on_button_pressed" not in source


def test_exec_from_changes_to_repo_root_before_exec(monkeypatch):
    calls = {}

    def fake_chdir(path):
        calls["cwd"] = path

    def fake_execvp(args):
        calls["args"] = args

    monkeypatch.setattr("cw.operations.os.chdir", fake_chdir)
    monkeypatch.setattr("cw.operations.execvp", fake_execvp)

    exec_from(Path("/repo"), ["claude", "--worktree", "demo"])

    assert calls == {
        "cwd": Path("/repo"),
        "args": ["claude", "--worktree", "demo"],
    }


def test_tui_new_form_autofills_slug():
    import asyncio

    from cw.tui import CwTui
    from textual.widgets import Button, Input, Static

    async def run_form_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            await pilot.press("n")
            await pilot.pause()
            assert not list(app.screen.query(Button))
            assert app.screen.query_one("#new-actions", Static)
            assert app.screen.query_one("#new-summary", Static)
            app.screen.query_one("#new-session-title", Input).value = "Demo Form"
            await pilot.pause()
            assert "Plan" in str(app.screen.query_one("#new-summary", Static).render())
            assert app.screen.query_one("#new-slug", Input).value == "demo-form"
            assert app.screen.values().slug == "demo-form"
            assert app.screen.values().branch is None
            assert app.screen.values().base is None

    asyncio.run(run_form_test())


def test_tui_new_form_ctrl_enter_creates():
    import asyncio

    from cw.models import LaunchPlan
    from cw.tui import CwTui
    from textual.widgets import Input

    async def run_form_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            await pilot.press("n")
            await pilot.pause()
            app.screen.query_one("#new-session-title", Input).value = "Enter Create"
            await pilot.pause()
            await pilot.press("ctrl+enter")
            await pilot.pause()
            await pilot.pause()
            assert app._return_value.action == "exec"
            assert app._return_value.exec_root == Path.cwd()
            assert app._return_value.exec_args == ["claude", "--worktree", "enter-create"]

    def fake_prepare_new_session(**kwargs):
        assert kwargs["title_parts"] == ["Enter Create"]
        kwargs["log"]("cw: fake launch")
        return LaunchPlan(Path.cwd(), ["claude", "--worktree", "enter-create"])

    import cw.tui

    original = cw.tui.prepare_new_session
    cw.tui.prepare_new_session = fake_prepare_new_session
    try:
        asyncio.run(run_form_test())
    finally:
        cw.tui.prepare_new_session = original


def test_tui_remove_modal_returns_force_choice():
    import asyncio

    from cw.models import RemoveResult, WorktreeSession
    from cw.tui import CwTui
    from textual.widgets import Button, Static

    async def run_remove_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            app.sessions = [
                WorktreeSession(
                    slug="dirty",
                    branch="claude/dirty",
                    title="Dirty",
                    tmux="dead",
                    path=Path.cwd() / ".claude/worktrees/dirty",
                    dirty_count=1,
                )
            ]
            app.apply_filter("")
            await pilot.press("x")
            await pilot.pause()
            assert not list(app.screen.query(Button))
            assert app.screen.query_one("#remove-actions", Static)
            await pilot.press("f")
            await pilot.pause()
            assert app._return_value is None
            assert type(app.screen).__name__ == "Screen"

    calls = {}

    def fake_remove_session(root, name, *, force=False, log=None):
        calls["args"] = (root, name, force, log)
        return RemoveResult(
            slug=name,
            branch="claude/dirty",
            worktree=Path(".claude/worktrees/dirty"),
            killed_tmux=False,
            branch_deleted=True,
        )

    import cw.tui

    original = cw.tui.remove_session
    cw.tui.remove_session = fake_remove_session
    try:
        asyncio.run(run_remove_test())
    finally:
        cw.tui.remove_session = original

    assert calls["args"] == (Path.cwd(), "dirty", True, None)


def test_tui_kill_modal_stays_in_dashboard():
    import asyncio

    from cw.models import KillResult, WorktreeSession
    from cw.tui import CwTui
    from textual.widgets import Static

    async def run_kill_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            app.sessions = [
                WorktreeSession(
                    slug="live",
                    branch="claude/live",
                    title="Live",
                    tmux="detached",
                    path=Path.cwd() / ".claude/worktrees/live",
                )
            ]
            app.apply_filter("")
            await pilot.press("k")
            await pilot.pause()
            assert type(app.screen).__name__ == "KillSessionScreen"
            assert "Session" in str(app.screen.query_one("#kill-details", Static).render())
            await pilot.press("k")
            await pilot.pause()
            assert app._return_value is None
            assert type(app.screen).__name__ == "Screen"

    calls = {}

    def fake_kill_session(root, name, *, log=None):
        calls["args"] = (root, name, log)
        return KillResult(slug=name, session="cw_worktree-live")

    import cw.tui

    original = cw.tui.kill_session
    cw.tui.kill_session = fake_kill_session
    try:
        asyncio.run(run_kill_test())
    finally:
        cw.tui.kill_session = original

    assert calls["args"] == (Path.cwd(), "live", None)


def test_tui_filter_focus_and_exit():
    import asyncio

    from cw.tui import CwTui
    from textual.widgets import DataTable, Input

    async def run_filter_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            await pilot.press("/")
            await pilot.pause()
            assert app.query_one("#filter", Input).has_focus
            app.query_one("#filter", Input).value = "demo"
            await pilot.press("enter")
            await pilot.pause()
            assert app.query_one("#sessions", DataTable).has_focus
            await pilot.press("/")
            await pilot.pause()
            assert app.query_one("#filter", Input).has_focus
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#sessions", DataTable).has_focus

    asyncio.run(run_filter_test())


def test_tui_input_word_delete_shortcut():
    import asyncio

    from cw.tui import CwTui
    from textual.widgets import Input

    async def run_word_delete_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            await pilot.press("n")
            await pilot.pause()
            title = app.screen.query_one("#new-session-title", Input)
            title.value = "Demo Word"
            title.cursor_position = len(title.value)
            await pilot.press("ctrl+w")
            await pilot.pause()
            assert title.value == "Demo "

    asyncio.run(run_word_delete_test())


def test_tui_filter_word_delete_shortcut():
    import asyncio

    from cw.tui import CwTui
    from textual.widgets import Input

    async def run_word_delete_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            await pilot.press("/")
            await pilot.pause()
            filter_input = app.query_one("#filter", Input)
            filter_input.value = "demo word"
            filter_input.cursor_position = len(filter_input.value)
            await pilot.press("ctrl+w")
            await pilot.pause()
            assert filter_input.value == "demo "

    asyncio.run(run_word_delete_test())


def test_tui_doctor_modal_stays_in_dashboard():
    import asyncio

    from cw.tui import CwTui
    from textual.widgets import Button, Static

    async def run_doctor_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            await pilot.press("d")
            await pilot.pause()
            assert type(app.screen).__name__ == "DoctorScreen"
            assert not list(app.screen.query(Button))
            assert app.screen.query_one("#doctor-actions", Static)
            await pilot.press("escape")
            await pilot.pause()
            assert type(app.screen).__name__ == "Screen"
            assert app._return_value is None

    asyncio.run(run_doctor_test())


def test_tui_status_modal_stays_in_dashboard():
    import asyncio

    from cw.models import WorktreeSession
    from cw.tui import CwTui
    from textual.widgets import Button, Static

    async def run_status_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            app.sessions = [
                WorktreeSession(
                    slug="demo",
                    branch="claude/demo",
                    title="Demo",
                    tmux="detached",
                    path=Path.cwd() / ".claude/worktrees/demo",
                )
            ]
            app.apply_filter("")
            await pilot.press("s")
            await pilot.pause()
            assert type(app.screen).__name__ == "StatusScreen"
            assert not list(app.screen.query(Button))
            assert app.screen.query_one("#status-actions", Static)
            await pilot.press("escape")
            await pilot.pause()
            assert type(app.screen).__name__ == "Screen"
            assert app._return_value is None

    asyncio.run(run_status_test())


def test_tui_prune_modal_stays_in_dashboard():
    import asyncio

    from cw.tui import CwTui
    from textual.widgets import Button, DataTable, RichLog, Static

    async def run_prune_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            await pilot.press("p")
            await pilot.pause()
            assert type(app.screen).__name__ == "PruneScreen"
            assert not list(app.screen.query(Button))
            assert not list(app.screen.query(RichLog))
            assert not list(app.screen.query(DataTable))
            assert app.screen.query_one("#prune-remove-list", Static)
            assert app.screen.query_one("#prune-keep-list", Static)
            assert app.screen.query_one("#prune-actions", Static)
            await pilot.press("escape")
            await pilot.pause()
            assert type(app.screen).__name__ == "Screen"
            assert app._return_value is None

    asyncio.run(run_prune_test())
