"""Bundled agent markdown files: native ownership, risk policy rendering."""
import json
import re
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
    installed = install_managed_agents()
    assert len(installed) == 12
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
    install_managed_agents()
    msg, ok = status()
    assert ok and "12 harness agents" in msg

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
    assert not ok and "missing 12 harness agent file" in msg


def test_agent_prompts_are_distinct_for_subagents():
    sub = {n: s for n, s in bundled_agents().items() if s.get("mode") == "subagent"}
    want = {"explore", "librarian", "plan-consultant", "plan-reviewer",
            "code-reviewer", "test-engineer", "security-auditor"}
    assert set(sub) == want
    prompts = [s["prompt"] for s in sub.values()]
    assert len(set(prompts)) == len(prompts)
    for name, spec in sub.items():
        assert len(spec["prompt"]) > 80
        assert "model" not in spec


def test_orchestrator_prompt_mentions_all_roles():
    orch = bundled_agents()["orchestrator"]["prompt"]
    for role in ("explore", "librarian", "plan-consultant", "plan-reviewer",
                 "code-reviewer", "test-engineer", "security-auditor"):
        assert role in orch, role


def test_bundled_json_has_no_model_pins_or_deprecated_tools():
    for name, spec in bundled_agents().items():
        assert "tools" not in spec, name
        assert "model" not in spec, name
        perms = spec.get("permission", {})
        assert isinstance(perms, dict), name
        bash = perms.get("bash")
        assert bash != "deny", name
