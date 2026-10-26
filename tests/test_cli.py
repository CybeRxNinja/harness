"""Kept CLI subcommands, driven through main() with a throwaway project.

Live sessions run inside opencode; the CLI keeps the installer and the
inspect/repair operators (doctor/config/skills/memory/plugin/setup/tui).
"""
import json
import time

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


def test_models_list_marks_free_and_working(env, capsys):
    """`models list` answers the two questions the owner asks before a dispatch:
    which models are free, and which of them actually answered a probe."""
    home = env / "home"
    home.mkdir(exist_ok=True)
    now = int(time.time())
    meta = {"cost": {"input": 0, "output": 0}, "limit": 8000, "tool_call": True}
    # kilo/lyria is excluded by the curated patterns (a music model), and
    # acme/no-tools has tool_call false — neither can run a job, so the list
    # must not offer them as dispatch candidates.
    (home / "models-available.json").write_text(json.dumps({
        "refreshed_at": now,
        "models": ["freeco/tiny-free", "acme/paid-one",
                   "kilo/google/lyria-3-clip-preview", "acme/no-tools"]}))
    (home / "models-dev.json").write_text(json.dumps({
        "refreshed_at": now,
        "providers": {
            "freeco": {"models": {"tiny-free": meta}},
            "acme": {"models": {"paid-one": meta,
                                "no-tools": {**meta, "tool_call": False}}},
            "kilo": {"models": {"google/lyria-3-clip-preview": meta}},
        },
    }))
    (home / "models-health.json").write_text(json.dumps({
        "freeco/tiny-free#low": {"ok": True, "reason": "", "cost": 0.0,
                                 "checked_at": now},
        "__prov__:acme": {"ok": False, "reason": "provider.auth: no key",
                           "checked_at": now},
    }))

    assert cli(env, "models", "list") == 0
    out = capsys.readouterr().out
    assert "freeco/tiny-free" in out and "works" in out
    assert "acme/paid-one" in out and "provider down" in out
    assert "lyria" not in out and "no-tools" not in out  # never dispatchable

    # --working drops what is known broken; the free filter is independent
    assert cli(env, "models", "list", "--working") == 0
    out = capsys.readouterr().out
    assert "freeco/tiny-free" in out and "acme/paid-one" not in out
    assert cli(env, "models", "list", "--free", "--working") == 0
    assert "freeco/tiny-free" in capsys.readouterr().out


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


def test_skills_sources_and_match(env, capsys):
    assert cli(env, "skills", "sources", "--search", "superpowers") == 0
    listed = capsys.readouterr().out
    assert "obra/superpowers" in listed

    from harness.skills import ensure_seed_skills
    ensure_seed_skills()
    assert cli(env, "skills", "match", "test driven development for a bug") == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("test-driven-development"), first


def test_skills_add_reports_a_bundled_id_it_shadows(env, capsys):
    """A project skill outranks the bundled seed of the same id; saying so is
    the difference between a choice and a surprise."""
    skill = env / "pack" / "skills" / "ponytail" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: ponytail\ndescription: pack copy of the ladder\n---\nbody\n")
    assert cli(env, "skills", "add", str(env / "pack")) == 0
    assert "replaces the bundled ponytail" in capsys.readouterr().out


def test_skills_add_installs_into_the_host_directory(env, capsys):
    """Placement is the point: the host advertises what it can discover."""
    skill = env / "pack" / "skills" / "alpha"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: Alpha\ndescription: Alpha route, use when alpha\n---\n# Alpha\nbody\n"
    )
    assert cli(env, "skills", "add", str(env / "pack"), "--dry-run") == 0
    assert "would install" in capsys.readouterr().out
    assert not (env / ".opencode" / "skills").exists()

    assert cli(env, "skills", "add", str(env / "pack")) == 0
    out = capsys.readouterr().out
    assert "installed" in out and "alpha" in out
    assert (env / ".opencode" / "skills" / "alpha" / "SKILL.md").exists()

    # and the CLI's own listing sees it (host roots joined skill_roots)
    assert cli(env, "skills", "list") == 0
    assert "Alpha" in capsys.readouterr().out


def test_skills_add_agents_and_update_cover_the_agent_half(env, capsys):
    """`--agents` imports the pack's agents/*.md where opencode discovers them,
    and `skills update` re-syncs that half too."""
    agent = env / "pack" / "agents"
    agent.mkdir(parents=True)
    (agent / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews code. Use when reviewing.\n---\n# Reviewer\nbody\n"
    )
    assert cli(env, "skills", "add", str(env / "pack"), "--agents", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "would install" in out and "agent reviewer" in out
    assert not (env / ".opencode" / "agents").exists(), "dry run must not write"

    assert cli(env, "skills", "add", str(env / "pack"), "--agents") == 0
    assert "1 agent(s) installed" in capsys.readouterr().out
    written = env / ".opencode" / "agents" / "reviewer.md"
    assert written.exists() and "# Reviewer" in written.read_text()

    # upstream moves: one `skills update` covers skills and agents
    (agent / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews code. Use when reviewing.\n---\n# Reviewer\nUPDATED\n"
    )
    assert cli(env, "skills", "update") == 0
    assert "UPDATED" in written.read_text()


def test_skills_draft_is_staged_then_approved(env, capsys):
    import re

    from harness.memory import save_fact
    from harness.store import connect

    mem = env / ".opencode" / "harness" / "MEMORY.md"
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_text(
        "# MEMORY.md\n"
        "- [cache] keep the facts index as a fast path\n"
        "- [cache] a missing index falls back to the LIKE scan\n"
    )
    con = connect(env)
    save_fact(con, "Fixed: the facts index is optional, never required", "turn")
    save_fact(con, "Blocker: a stale facts index hides a row until the next write", "turn")
    con.close()

    assert cli(env, "skills", "draft", "facts-index", "--query", "facts index") == 0
    out = capsys.readouterr().out
    pid = re.search(r"staged skill (\d+)", out).group(1)
    assert "review: harness skills pending" in out

    assert cli(env, "skills", "approve", "--ids", pid) == 0
    assert "approved" in capsys.readouterr().out
    # The approved skill must land where the HOST advertises it
    # (.opencode/skills, opencode's project discovery) — not harness' private
    # overlay, which the model never sees.
    base = env / ".opencode" / "skills" / "facts-index"
    body = (base / "SKILL.md").read_text()
    assert "## Procedure" in body and "## Pitfalls" in body
    prov = json.loads((base / ".harness-source.json").read_text())
    assert prov["repo"] == "harness-memory" and prov["files"] == ["SKILL.md"]


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
    written = env / ".opencode" / "skills" / "my-skill" / "SKILL.md"
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
    assert not (env / ".opencode" / "skills" / "other-skill").exists()


def test_approve_refuses_to_overwrite_an_imported_skill(env, capsys):
    """Approving a draft whose id already belongs to a pack skill would replace
    someone else's content AND its provenance (so `skills update` loses it)."""
    skill = env / "pack" / "skills" / "code-review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: code-review\ndescription: Reviews code\n---\nUPSTREAM BODY\n")
    assert cli(env, "skills", "add", str(env / "pack")) == 0
    capsys.readouterr()

    from harness.store import connect
    con = connect(env)
    try:
        con.execute("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,0)",
                    ("skill-create", "code-review", "---\nname: code-review\n---\nDRAFT BODY", "gist"))
        con.commit()
        pid = con.execute("SELECT id FROM pending").fetchone()[0]
    finally:
        con.close()

    assert cli(env, "skills", "approve", "--ids", str(pid)) == 1
    out = capsys.readouterr().out
    assert "refusing" in out and str(env / "pack") in json.loads(
        (env / ".opencode/skills/code-review/.harness-source.json").read_text())["repo"]
    written = env / ".opencode" / "skills" / "code-review" / "SKILL.md"
    assert "UPSTREAM BODY" in written.read_text(), "the imported skill is intact"
    assert "DRAFT BODY" not in written.read_text()
    # still pending, so the decision is not lost
    con = connect(env)
    try:
        assert con.execute("SELECT COUNT(*) FROM pending WHERE id=?", (pid,)).fetchone()[0] == 1
    finally:
        con.close()

    # a memory-authored skill is the draft's own: re-approving is allowed
    (env / ".opencode" / "skills" / "code-review" / ".harness-source.json").write_text(
        json.dumps({"repo": "harness-memory", "files": ["SKILL.md"]}))
    assert cli(env, "skills", "approve", "--ids", str(pid)) == 0
    assert "DRAFT BODY" in written.read_text()


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
    # `mcp` is live again as a wrapper over opencode's own MCP manager.
    for retired in ("chat", "plan", "checkpoint"):
        assert cli(env, retired) == 2
        err = capsys.readouterr().err
        assert "retired" in err or "unknown command" in err, err
