"""Kept CLI subcommands, driven through main() with a throwaway project.

Live sessions run inside opencode; the CLI keeps the installer and the
inspect/repair operators (doctor/config/skills/memory/plugin/setup/tui).
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

    from harness.store import connect
    import time as _t
    con = connect(env)
    try:
        con.execute("INSERT INTO messages(session,role,content,ts) VALUES(?,?,?,?)",
                    ("s1", "assistant",
                     "I fixed the bug by guarding the empty case and re-ran the suite.",
                     int(_t.time())))
        con.commit()
    finally:
        con.close()
    assert cli(env, "memory", "refine", "--session", "s1", "--name", "l1") == 0
    assert "staged lesson" in capsys.readouterr().out


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


def test_retired_headless_commands_exit_2(env, capsys):
    for retired in ("chat", "plan", "checkpoint", "mcp"):
        assert cli(env, retired) == 2
        err = capsys.readouterr().err
        assert "retired" in err or "unknown command" in err, err
