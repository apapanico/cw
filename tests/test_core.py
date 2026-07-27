from pathlib import Path

import pytest

from cw.cli import (
    ModeError,
    WorktreeSession,
    claude_base_args,
    default_choice,
    display_name,
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
