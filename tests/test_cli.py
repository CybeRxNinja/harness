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


def test_tui_launch_confines_temp_to_the_project(env, monkeypatch):
    """`harness tui` exports TMPDIR/TMP/TEMP = <root>/.opencode/harness/tmp
    before exec, so the whole opencode tree (bun JIT caches, LSP servers,
    agent-run tools) writes scratch inside the project — enforced by env,
    not by asking agents. execvp must never actually run: stop it here.
    """
    import os as _os
    from harness import cli as C

    monkeypatch.setattr(C, "_find_opencode", lambda: "/usr/bin/opencode")
    # env tracking so cmd_tui's exports are undone with the test
    for key in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(key, _os.environ.get(key, ""))
    # keep cmd_tui's inert setdefault from leaking past this test
    monkeypatch.setenv("OPENCODE_EXPERIMENTAL_LSP_TOOL",
                       _os.environ.get("OPENCODE_EXPERIMENTAL_LSP_TOOL", "true"))
    seen = {}

    def fake_execvp(binary, argv):
        seen.update(binary=binary, tmpdir=_os.environ.get("TMPDIR"),
                    tmp=_os.environ.get("TMP"), temp=_os.environ.get("TEMP"))
        raise SystemExit(0)  # stop before replacing the test process

    monkeypatch.setattr(C.os, "execvp", fake_execvp)
    with pytest.raises(SystemExit):
        cli(env, "tui")

    scratch = env / ".opencode" / "harness" / "tmp"
    assert scratch.is_dir(), "the scratch dir must exist before exec"
    assert seen["tmpdir"] == str(scratch), seen
    assert seen["tmp"] == str(scratch) and seen["temp"] == str(scratch)
    assert seen["binary"] == "/usr/bin/opencode"


def test_setup_reports_zero_new_when_nothing_is_missing(env, capsys):
    """The count is what the run WROTE, not how many seeds exist.

    Printing len(installed) made every `harness setup` after the first claim
    "19 new" over a store that already had all 19.
    """
    assert cli(env, "setup") == 0
    first = capsys.readouterr().out
    assert "19 new" in first, first

    assert cli(env, "setup") == 0
    second = capsys.readouterr().out
    assert "0 new" in second, second
    assert "19 already present" in second, second


def test_setup_update_skills_is_opt_in_and_names_what_it_changed(env, capsys):
    """`--update-skills` is the only way a stale installed seed ever refreshes.

    The default must stay missing-only (user edits are never clobbered) and the
    opt-in must not be a silent overwrite.
    """
    from harness.cli import build_parser
    assert build_parser().parse_args(["setup"]).update_skills is False
    assert build_parser().parse_args(["setup", "--update-skills"]).update_skills is True

    assert cli(env, "setup") == 0
    capsys.readouterr()
    skill = env / "home" / "skills" / "build" / "ponytail" / "SKILL.md"
    assert skill.is_file()
    skill.write_text("---\nname: ponytail\ndescription: >-\n  stale folded.\n---\n# old\n")

    # default run: the stale file is left alone, and reported as 0 new
    assert cli(env, "setup") == 0
    assert "0 new" in capsys.readouterr().out
    assert "folded" in skill.read_text(), "default run clobbered the installed skill"

    # opt-in: refreshed, and the run says which skill it rewrote
    assert cli(env, "setup", "--update-skills") == 0
    out = capsys.readouterr().out
    assert "ponytail" in out and "1 new" in out, out
    assert "Routable" not in skill.read_text()
    assert "description: Lazy senior dev" in skill.read_text(), skill.read_text()[:200]

    # and it is idempotent — a second update reports nothing changed
    assert cli(env, "setup", "--update-skills") == 0
    assert "0 new" in capsys.readouterr().out


def test_cli_risk_check_outputs_json(env, capsys):
    from harness.cli import main
    assert main(["risk", "check", "rm -rf ./dist", "--json"]) == 0
    dec = json.loads(capsys.readouterr().out)
    assert dec["ask"] is True and dec["risk"] == "destructive"
    assert dec["irreversible"] is True


def test_retired_relay_subcommands_exit_2(env, capsys):
    for retired in ("serve", "router"):
        assert cli(env, retired) == 2
        assert "retired" in capsys.readouterr().err


def test_retired_headless_commands_exit_2(env, capsys):
    for retired in ("chat", "plan", "checkpoint", "mcp"):
        assert cli(env, retired) == 2
        err = capsys.readouterr().err
        assert "retired" in err or "unknown command" in err, err
