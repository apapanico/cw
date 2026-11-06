import os
from pathlib import Path

import pytest

from cw.models import ModeError, WorktreeSession
from cw.operations import (
    HAPPY_DEFAULT_MODEL,
    PROJECT_CONFIG,
    apply_shared_state,
    claude_base_args,
    default_choice,
    display_name,
    exec_from,
    happy_mode_value,
    inferred_repo_root,
    load_shared_state_entries,
    map_mode,
    parse_worktrees_porcelain,
    render_prompt_text,
    repo_root,
    session_state,
    session_name,
    slugify,
    tmux_command_args,
    tmux_flag,
    validate_shared_state_path,
    validate_happy_mode,
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


def test_inferred_repo_root_recovers_from_worktree_path():
    assert inferred_repo_root(Path("/repo/.claude/worktrees/demo")) == Path("/repo")
    assert inferred_repo_root(Path("/repo/.claude/worktrees/demo/subdir")) == Path("/repo")
    assert inferred_repo_root(Path("/repo/src")) is None


def test_repo_root_falls_back_to_inferred_root_when_cwd_is_deleted(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    deleted_worktree = root / ".claude/worktrees/demo"
    calls = []

    monkeypatch.setattr("cw.operations.current_path", lambda: deleted_worktree)

    def fake_run(args, *, check=True, capture=False, cwd=None):
        calls.append(args)
        if args[:2] == ["git", "worktree"]:
            return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "getcwd failed"})()
        if args[:3] == ["git", "-C", str(root)]:
            return type("CP", (), {"returncode": 0, "stdout": f"worktree {root}\n", "stderr": ""})()
        raise AssertionError(args)

    monkeypatch.setattr("cw.operations.run", fake_run)

    assert repo_root() == root.resolve()
    assert calls == [
        ["git", "worktree", "list", "--porcelain"],
        ["git", "-C", str(root.resolve()), "worktree", "list", "--porcelain"],
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


def test_claude_base_args_happy_swaps_launcher_and_suppresses_remote_control():
    assert claude_base_args(
        slug="slug",
        title="My Title",
        native=False,
        rc=True,
        mode="accept",
        model="sonnet",
        effort="high",
        happy=True,
    ) == [
        "happy",
        "--name",
        "My Title",
        "--permission-mode",
        "acceptEdits",
        "--permission-mode=acceptEdits",
        "--model=sonnet",
        "--effort=high",
    ]


def test_claude_base_args_happy_defaults_to_yolo_and_sonnet():
    assert claude_base_args(
        slug="slug",
        title="My Title",
        native=False,
        rc=True,
        mode=None,
        model=None,
        effort=None,
        happy=True,
    ) == ["happy", "--name", "My Title", "--permission-mode", "yolo", "--yolo", f"--model={HAPPY_DEFAULT_MODEL}"]


def test_happy_mode_value_maps_cw_defaults_to_happy_yolo():
    assert happy_mode_value(None) == "yolo"
    assert happy_mode_value("default") == "yolo"
    assert happy_mode_value("bypass") == "yolo"
    assert happy_mode_value("auto") == "auto"
    assert happy_mode_value("accept") == "acceptEdits"
    assert happy_mode_value("plan") == "plan"


def test_validate_happy_mode_rejects_claude_only_modes():
    validate_happy_mode(None)
    validate_happy_mode("accept")
    validate_happy_mode("auto")
    with pytest.raises(Exception, match="does not support"):
        validate_happy_mode("manual")
    with pytest.raises(Exception, match="does not support"):
        validate_happy_mode("dontask")


def test_load_shared_state_entries_accepts_array_config(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[[shared_state]]
path = ".staffreview"
type = "dir"

[[shared_state]]
path = ".tool/cache.db"
type = "file"
"""
    )

    entries = load_shared_state_entries(config)

    assert [(entry.path, entry.type) for entry in entries] == [
        (Path(".staffreview"), "dir"),
        (Path(".tool/cache.db"), "file"),
    ]


def test_load_shared_state_entries_accepts_named_config(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[shared_state.staffreview]
path = ".staffreview"
type = "dir"
"""
    )

    entries = load_shared_state_entries(config)

    assert [(entry.path, entry.type) for entry in entries] == [(Path(".staffreview"), "dir")]


def test_load_shared_state_entries_accepts_project_config_paths(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[shared_state]

[[shared_state.paths]]
path = ".staffreview"
type = "dir"

[[shared_state.paths]]
path = ".env.local"
type = "file"
"""
    )

    entries = load_shared_state_entries(config)

    assert [(entry.path, entry.type) for entry in entries] == [
        (Path(".staffreview"), "dir"),
        (Path(".env.local"), "file"),
    ]


@pytest.mark.parametrize("path", ["/tmp/state", "../state", "a/../state", ""])
def test_validate_shared_state_path_rejects_unsafe_paths(path):
    with pytest.raises(Exception, match="invalid shared_state path"):
        validate_shared_state_path(path)


def test_apply_shared_state_creates_root_target_and_relative_worktree_link(tmp_path):
    root = tmp_path / "repo"
    worktree = root / ".claude/worktrees/demo"
    worktree.mkdir(parents=True)
    config = tmp_path / "config.toml"
    config.write_text(
        """
[[shared_state]]
path = ".staffreview"
type = "dir"

[[shared_state]]
path = ".tool/cache.db"
type = "file"
"""
    )

    apply_shared_state(root, worktree, config_path=config)
    apply_shared_state(root, worktree, config_path=config)

    assert (root / ".staffreview").is_dir()
    assert (root / ".tool/cache.db").is_file()
    staffreview_link = worktree / ".staffreview"
    cache_link = worktree / ".tool/cache.db"
    assert staffreview_link.is_symlink()
    assert cache_link.is_symlink()
    assert staffreview_link.resolve() == (root / ".staffreview").resolve()
    assert cache_link.resolve() == (root / ".tool/cache.db").resolve()
    assert not os.readlink(staffreview_link).startswith("/")
    assert not os.readlink(cache_link).startswith("/")


def test_apply_shared_state_reads_project_config_by_default(tmp_path):
    root = tmp_path / "repo"
    worktree = root / ".claude/worktrees/demo"
    worktree.mkdir(parents=True)
    config = root / PROJECT_CONFIG
    config.parent.mkdir(parents=True)
    config.write_text(
        """
[shared_state]

[[shared_state.paths]]
path = ".staffreview"
type = "dir"
"""
    )

    apply_shared_state(root, worktree)

    assert (worktree / ".staffreview").is_symlink()
    assert (worktree / ".staffreview").resolve() == (root / ".staffreview").resolve()


def test_apply_shared_state_refuses_existing_worktree_path(tmp_path):
    root = tmp_path / "repo"
    worktree = root / ".claude/worktrees/demo"
    (root / ".staffreview").mkdir(parents=True)
    (worktree / ".staffreview").mkdir(parents=True)
    config = tmp_path / "config.toml"
    config.write_text(
        """
[[shared_state]]
path = ".staffreview"
type = "dir"
"""
    )

    with pytest.raises(Exception, match="already exists"):
        apply_shared_state(root, worktree, config_path=config)


def test_apply_shared_state_refuses_wrong_symlink(tmp_path):
    root = tmp_path / "repo"
    worktree = root / ".claude/worktrees/demo"
    other = tmp_path / "other"
    (root / ".staffreview").mkdir(parents=True)
    other.mkdir()
    worktree.mkdir(parents=True)
    (worktree / ".staffreview").symlink_to(other, target_is_directory=True)
    config = tmp_path / "config.toml"
    config.write_text(
        """
[[shared_state]]
path = ".staffreview"
type = "dir"
"""
    )

    with pytest.raises(Exception, match="links elsewhere"):
        apply_shared_state(root, worktree, config_path=config)


def test_tmux_command_args_runs_command_from_worktree():
    import os

    old_env = os.environ.copy()
    os.environ.clear()
    os.environ.update({"PATH": "/bin", "HAPPY_SERVER_URL": "https://happy.example", "OTHER": "ignored"})
    try:
        args = tmux_command_args(
            "repo_worktree-demo",
            Path("/repo/.claude/worktrees/demo"),
            ["happy", "--name", "Demo Session", "--permission-mode", "yolo", "--yolo"],
        )
    finally:
        os.environ.clear()
        os.environ.update(old_env)

    assert args == [
        "tmux",
        "new-session",
        "-A",
        "-s",
        "repo_worktree-demo",
        "-c",
        "/repo/.claude/worktrees/demo",
        "env PATH=/bin HAPPY_SERVER_URL=https://happy.example happy --name 'Demo Session' --permission-mode yolo --yolo",
    ]


def test_tmux_command_args_quotes_command_env_and_worktree():
    assert tmux_command_args(
        "repo_worktree-demo",
        Path("/repo/.claude/worktrees/demo"),
        ["happy", "--name", "Demo Session", "--permission-mode", "yolo", "--yolo"],
    )[:7] == ["tmux", "new-session", "-A", "-s", "repo_worktree-demo", "-c", "/repo/.claude/worktrees/demo"]


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


def test_tui_new_form_happy_disables_remote():
    import asyncio

    from cw.tui import CwTui
    from textual.widgets import Checkbox

    async def run_form_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            await pilot.press("n")
            await pilot.pause()
            happy = app.screen.query_one("#new-happy", Checkbox)
            remote = app.screen.query_one("#new-rc", Checkbox)
            happy.value = True
            await pilot.pause()
            assert remote.disabled
            assert not remote.value
            assert app.screen.values().happy
            assert not app.screen.values().rc

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


def test_tui_resume_modal_uses_ctrl_enter_to_launch():
    import asyncio

    from cw.models import WorktreeSession
    from cw.tui import CwTui

    async def run_resume_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            app.sessions = [
                WorktreeSession(
                    slug="dead",
                    branch="claude/dead",
                    title="Dead",
                    tmux="dead",
                    path=Path.cwd() / ".claude/worktrees/dead",
                )
            ]
            app.apply_filter("")
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "ResumeSessionScreen"
            await pilot.press("enter")
            await pilot.pause()
            assert app._return_value is None
            assert type(app.screen).__name__ == "ResumeSessionScreen"
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert app._return_value.action == "resume"
            assert app._return_value.slug == "dead"

    asyncio.run(run_resume_test())


def test_tui_resume_form_happy_disables_remote():
    import asyncio

    from cw.models import WorktreeSession
    from cw.tui import CwTui
    from textual.widgets import Checkbox

    async def run_resume_test():
        app = CwTui(Path.cwd())
        async with app.run_test() as pilot:
            app.sessions = [
                WorktreeSession(
                    slug="dead",
                    branch="claude/dead",
                    title="Dead",
                    tmux="dead",
                    path=Path.cwd() / ".claude/worktrees/dead",
                )
            ]
            app.apply_filter("")
            await pilot.press("enter")
            await pilot.pause()
            happy = app.screen.query_one("#resume-happy", Checkbox)
            remote = app.screen.query_one("#resume-rc", Checkbox)
            happy.value = True
            await pilot.pause()
            assert remote.disabled
            assert not remote.value
            assert app.screen.values().happy
            assert not app.screen.values().rc

    asyncio.run(run_resume_test())


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
