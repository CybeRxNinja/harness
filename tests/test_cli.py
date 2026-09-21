"""Every CLI subcommand, driven through main() with a throwaway project.

The CLI is the guaranteed fallback UI (no TUI deps), so a broken subcommand is
the difference between "usable" and "nothing works".
"""
import json

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_MOCK", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HARNESS_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)  # `--root` defaults to the project root
    return tmp_path


def cli(env, *argv):
    from harness.cli import main
    return main(list(argv))


def test_config_get_set_show(env, capsys):
    assert cli(env, "config", "get", "budgets.max_turns") == 0
    assert json.loads(capsys.readouterr().out)  # a default is always present

    assert cli(env, "config", "set", "budgets.max_turns", "3", "--scope", "project") == 0
    capsys.readouterr()
    cli(env, "config", "get", "budgets.max_turns")
    assert json.loads(capsys.readouterr().out) == 3

    assert cli(env, "config", "show") == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["budgets"]["max_turns"] == 3


def test_skills_list_view_and_approval_flow(env, capsys):
    from harness.skills import ensure_seed_skills
    assert "using-agent-skills" in ensure_seed_skills()

    assert cli(env, "skills", "list") == 0
    listing = capsys.readouterr().out
    assert "using-agent-skills" in listing

    assert cli(env, "skills", "view", "using-agent-skills") == 0
    assert "## Procedure" in capsys.readouterr().out

    # a staged lesson shows up as pending and can be approved or rejected
    from harness import memory as M
    from harness.store import connect
    con = connect(env)
    try:
        pid = M.stage_lesson(con, "lesson-one", "evidence " * 20, "gist")
    finally:
        con.close()

    assert cli(env, "skills", "pending") == 0
    assert "lesson-one" in capsys.readouterr().out
    assert cli(env, "skills", "approve", "--ids", str(pid)) == 0
    assert "approved" in capsys.readouterr().out
    assert cli(env, "skills", "pending") == 0
    assert "lesson-one" not in capsys.readouterr().out


def test_skills_approve_writes_a_staged_skill_file(env, capsys):
    from harness.store import connect
    con = connect(env)
    try:
        con.execute("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,0)",
                    ("skill-create", "my-skill", "---\nname: my-skill\n---\nbody", "gist"))
        con.commit()
        pid = con.execute("SELECT id FROM pending").fetchone()[0]
    finally:
        con.close()

    assert cli(env, "skills", "approve", "--ids", str(pid)) == 0
    assert "approved" in capsys.readouterr().out
    written = env / "home" / "skills" / "general" / "my-skill" / "SKILL.md"
    assert written.read_text().endswith("body")

    # a second staged change, this time rejected in bulk
    con = connect(env)
    try:
        con.execute("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,0)",
                    ("skill-create", "other-skill", "body", "gist"))
        con.commit()
    finally:
        con.close()
    assert cli(env, "skills", "reject", "--ids", "all") == 0
    assert "rejected" in capsys.readouterr().out
    assert not (env / "home" / "skills" / "general" / "other-skill").exists()


def test_memory_save_search_and_refine(env, capsys):
    assert cli(env, "memory", "save", "--text", "the auth module lives in auth.py") == 0
    assert "saved" in capsys.readouterr().out

    assert cli(env, "memory", "search", "auth") == 0
    assert "auth.py" in capsys.readouterr().out

    # refine needs evidence: an empty trajectory is refused, a real one stages
    assert cli(env, "memory", "refine", "--session", "s1", "--name", "l1") == 0
    assert "no substantial trajectory" in capsys.readouterr().out

    from harness.store import connect, ensure_session, add_message
    con = connect(env)
    try:
        ensure_session(con, "s1")
        add_message(con, "s1", "assistant",
                    "I fixed the bug by guarding the empty case and re-ran the suite.")
    finally:
        con.close()
    assert cli(env, "memory", "refine", "--session", "s1", "--name", "l1") == 0
    assert "staged lesson" in capsys.readouterr().out


def _git(env, *args):
    import subprocess
    return subprocess.run(["git", *args], cwd=env, capture_output=True, text=True, timeout=30)


def test_checkpoint_round_trips_through_a_git_diff(env, capsys):
    _git(env, "init", "-q")
    _git(env, "config", "user.email", "t@example.com")
    _git(env, "config", "user.name", "t")
    (env / "f.txt").write_text("v1")
    _git(env, "add", "f.txt")
    _git(env, "commit", "-qm", "init")

    (env / "f.txt").write_text("v2")
    assert cli(env, "checkpoint", "save") == 0
    snap = capsys.readouterr().out.strip()
    assert (env / ".opencode" / "harness" / "shadow").exists()

    # work continues past the checkpoint, then the checkpoint is restored
    (env / "f.txt").write_text("v3")
    assert cli(env, "checkpoint", "restore", snap) == 0
    assert "restored" in capsys.readouterr().out
    assert (env / "f.txt").read_text() == "v2", "back to the checkpointed state"


def test_restore_reports_a_missing_or_empty_snapshot(env, capsys):
    assert cli(env, "checkpoint", "restore", "20200101-000000") == 1
    assert "no snapshot" in capsys.readouterr().err


def test_checkpoint_warns_when_it_captured_nothing(env, capsys):
    """No git repo and no touched files: say so instead of implying safety."""
    (env / "f.txt").write_text("v1")
    assert cli(env, "checkpoint", "save") == 0
    out = capsys.readouterr()
    assert "nothing captured" in out.err
    from harness.checkpoints import has_content
    assert not has_content(out.out.strip())


def test_plan_start_next_check(env, capsys):
    assert cli(env, "plan", "start", "--title", "work", "--items", "one;two") == 0
    capsys.readouterr()

    assert cli(env, "plan", "next") == 0
    assert "one" in capsys.readouterr().out

    assert cli(env, "plan", "check", "--item", "one") == 0
    assert "checked" in capsys.readouterr().out


def test_plan_next_without_a_plan_exits_nonzero(env, capsys):
    assert cli(env, "plan", "next") == 1
    assert "no active work" in capsys.readouterr().out


def test_plugin_path_lists_both_entrypoints(env, capsys):
    assert cli(env, "plugin", "path") == 0
    out = capsys.readouterr().out
    assert "harness.ts" in out and "tui.tsx" in out


def test_setup_and_tui_setup_only(env, capsys):
    assert cli(env, "setup") == 0
    assert "Seed skills installed" in capsys.readouterr().out

    assert cli(env, "tui", "--setup-only") == 0
    out = capsys.readouterr().out
    assert "plugins" in out or "opencode" in out.lower()
    # the plugin really landed in the throwaway config dir
    assert (env / "cfg" / "opencode" / "plugins" / "harness" / "server.ts").exists()


def test_retired_relay_subcommands_exit_2(env, capsys):
    for retired in ("serve", "router"):
        assert cli(env, retired) == 2
        assert "retired" in capsys.readouterr().err


def test_bare_prompt_shorthand_routes_to_chat(env, capsys):
    """`harness "a prompt"` is documented shorthand for `harness chat "..."`."""
    assert cli(env, "hello there", "--mode", "ask", "--model", "acme/foo") == 0
    assert "mock" in capsys.readouterr().out

    from harness.store import connect, history
    con = connect(env)
    try:
        sessions = [r[0] for r in con.execute("SELECT id FROM sessions").fetchall()]
    finally:
        con.close()
    assert sessions, "the shorthand must actually run a turn"
