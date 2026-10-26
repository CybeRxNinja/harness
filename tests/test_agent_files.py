"""Bundled agent markdown files: native ownership, risk policy rendering."""
import json
import re
import time
from pathlib import Path

from harness.agents import (
    agent_id,
    agents_dir,
    bundled_agents,
    expected_agent_files,
    install_managed_agents,
    status,
    uninstall_managed_agents,
)


def test_agent_files_are_native_markdown_and_namespaced(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-state"))
    installed = install_managed_agents()
    assert len(installed) == 14
    assert all(p.name.startswith("harness-") for p in installed)
    assert (agents_dir() / "harness-orchestrator.md").exists()
    assert not (agents_dir() / "orchestrator.md").exists()
    text = (agents_dir() / "harness-orchestrator.md").read_text()
    assert text.startswith("---\n")
    assert 'mode: "primary"' in text
    assert "permissions:" in text
    assert re.search(r'action: "shell", resource: "git push \*", effect: "ask"', text)
    ask_text = (agents_dir() / "harness-ask.md").read_text()
    assert re.search(r'action: "subagent", resource: "\*", effect: "deny"', ask_text)
    assert "You are the ORCHESTRATOR." in text


def test_agent_files_use_json_as_source_of_truth(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-state"))
    install_managed_agents()
    prompts = [spec.get("prompt", "") for spec in bundled_agents().values()]
    for expected_path, content in expected_agent_files().items():
        assert expected_path.exists(), expected_path
        live = expected_path.read_text()
        assert live == content
    assert {spec.get("mode") for spec in bundled_agents().values()} <= {"primary", "subagent", None}
    assert any(p.startswith("You explore, you do not change anything.") for p in prompts)


def test_agent_file_status_detects_stale_and_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-state"))
    install_managed_agents()
    msg, ok = status()
    assert ok and "14 harness agents" in msg

    target = agents_dir() / "harness-orchestrator.md"
    target.write_text(target.read_text()[:-30])
    msg, ok = status()
    assert not ok and "STALE" in msg

    target.write_text("changed by user")
    msg, ok = status()
    assert not ok and "STALE" in msg

    target.unlink()
    msg, ok = status()
    assert not ok and "missing 1 harness agent file" in msg

    install_managed_agents()
    msg, ok = status()
    assert ok

    uninstall_managed_agents()
    msg, ok = status()
    assert not ok and "missing 14 harness agent file" in msg


def test_agent_prompts_are_distinct_for_subagents():
    sub = {n: s for n, s in bundled_agents().items() if s.get("mode") == "subagent"}
    want = {"explore", "quick", "deep", "librarian", "plan-consultant",
            "plan-reviewer", "code-reviewer", "test-engineer", "security-auditor"}
    assert set(sub) == want
    prompts = [s["prompt"] for s in sub.values()]
    assert len(set(prompts)) == len(prompts)
    for name, spec in sub.items():
        assert len(spec["prompt"]) > 80
        assert "model" not in spec


def test_orchestrator_prompt_mentions_all_roles():
    orch = bundled_agents()["orchestrator"]["prompt"]
    for role in ("explore", "quick", "deep", "librarian", "plan-consultant",
                 "plan-reviewer", "code-reviewer", "test-engineer",
                 "security-auditor"):
        assert role in orch, role


def test_bundled_json_has_no_model_pins_or_deprecated_tools():
    for name, spec in bundled_agents().items():
        assert "tools" not in spec, name
        assert "model" not in spec, name
        perms = spec.get("permission", {})
        assert isinstance(perms, dict), name
        bash = perms.get("bash")
        assert bash != "deny", name


def test_subagents_get_the_routed_model_and_primaries_inherit(tmp_path, monkeypatch):
    """Opt-in install-time pins (`models.pin_agents: true`): subagents carry
    the model route() chose for their role. The default leaves agent files
    with no `model:` line at all — model choice is per dispatch (see
    test_agent_files_default_to_per_call_models in test_models.py).

    Primary agents must NOT be pinned — a primary's model is the session the
    user selected, so a pin there would look like routing but never apply. The
    description badge is what the orchestrator sees when picking a worker.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HARNESS_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-state"))
    now = int(time.time())
    meta = {"cost": {"input": 0, "output": 0}, "limit": 128000,
            "reasoning": True, "tool_call": True,
            "reasoning_options": [{"type": "effort", "values": ["low", "high"]}]}
    (home / "models-available.json").write_text(json.dumps(
        {"refreshed_at": now, "models": ["freeco/tiny-free"]}))
    (home / "models-dev.json").write_text(json.dumps({
        "refreshed_at": now,
        "providers": {"freeco": {"models": {"tiny-free": meta}}},
    }))
    # opt-in: the default (pins off) renders no model/badge anywhere
    (home / "harness.jsonc").write_text(json.dumps({"models": {"pin_agents": True}}))

    files = expected_agent_files()
    explore = files[agents_dir() / "harness-explore.md"]
    assert 'model: "freeco/tiny-free#low"' in explore
    assert "[cheap \u00b7 free]" in explore, "the tier/free badge must be readable"
    assert "\\u00b7" not in explore
    head = (files[agents_dir() / "harness-orchestrator.md"]).split("---", 2)[1]
    assert "model:" not in head


def test_agent_files_carry_the_v2_identity_colour(tmp_path, monkeypatch):
    """opencode v2's `color:` agent option is emitted for all 12 agents.

    v2 accepts a six-digit hex on an agent (frontmatter or `agents.<id>.color`)
    and uses it to colour the agent in the UI. The bundled JSON is the source
    of truth; render_agent validates the value before writing, so a bad palette
    entry is dropped rather than installed as unusable frontmatter.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-state"))
    install_managed_agents()
    files = sorted(agents_dir().glob("harness-*.md"))
    assert len(files) == 14
    for path in files:
        head = path.read_text().split("---", 2)[1]
        assert re.search(r'^color: "#[0-9a-f]{6}"$', head, re.M), f"{path.name} has no v2 colour"
        assert "permissions:" in head


def test_a_broken_package_never_prunes_the_agent_directory(tmp_path, monkeypatch):
    """expected_agent_files() is empty only when the bundled JSON is unreadable.
    Installing in that state must not read "nothing expected" as "delete all",
    and a normal install leaves no temp files behind."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-state"))
    import harness.agents as A
    install_managed_agents()
    before = sorted(p.name for p in agents_dir().glob("harness-*.md"))
    assert before and not list(agents_dir().glob("*.tmp"))
    monkeypatch.setattr(A, "bundled_agents", lambda: {})
    assert A.install_managed_agents() == []
    assert sorted(p.name for p in agents_dir().glob("harness-*.md")) == before
