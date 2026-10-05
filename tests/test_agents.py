"""Bundled agent JSON: prompts and policy invariants live here."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_bundled_json_has_no_version_or_model_key_anywhere():
    text = (ROOT / "harness" / "harness-opencode.json").read_text()
    assert '"version"' not in text
    data = json.loads(text)
    for name, spec in data["agent"].items():
        assert "model" not in spec, name


def test_specialized_subagents_ship_with_their_own_instructions():
    data = json.loads((ROOT / "harness" / "harness-opencode.json").read_text())
    subs = {n: s for n, s in data["agent"].items() if s.get("mode") == "subagent"}
    want = ("explore", "librarian", "plan-consultant", "plan-reviewer",
            "code-reviewer", "test-engineer", "security-auditor")
    for name in want:
        assert name in subs, name
        spec = subs[name]
        assert spec.get("description"), f"{name} needs a description"
        assert len(str(spec.get("prompt", ""))) > 80, name
        assert "model" not in spec, f"{name} must inherit the user's default model"
        assert spec.get("permission", {}).get("task") == "deny", name
        edit = spec.get("permission", {}).get("edit")
        assert edit == ("allow" if name == "test-engineer" else "deny"), (name, edit)
    prompts = [s["prompt"] for s in subs.values()]
    assert len(set(prompts)) == len(prompts)
    orchestrator = data["agent"]["orchestrator"]["prompt"]
    for name in want:
        assert name in orchestrator, f"orchestrator prompt does not route to {name}"


def test_agent_prompts_carry_the_resource_discipline():
    data = json.loads((ROOT / "harness" / "harness-opencode.json").read_text())
    orch = data["agent"]["orchestrator"]["prompt"]
    assert "WORKFLOW DISCIPLINE" in orch
    assert "never load a whole large file" in orch
    assert "screenshots or images" in orch
    assert ".opencode/harness/tmp/" in orch
    assert "at most 2 per wave" in orch
    review = data["agent"]["code-reviewer"]["prompt"]
    assert "static review only" in review
    assert "browser" in review
    testy = data["agent"]["test-engineer"]["prompt"]
    assert ".opencode/harness/tmp/" in testy
    assert "never install" in testy
    assert "ONE browser/server session" in testy
    assert "TOKEN DISCIPLINE" in orch
    assert "single pass" in orch
    assert "no pass 3" in orch.lower()

    subs = {n: s for n, s in data["agent"].items() if s.get("mode") == "subagent"}
    for name, spec in subs.items():
        assert "Frugality:" in spec.get("prompt", ""), name


def test_orchestrator_plans_waves_and_sizes_verification():
    data = json.loads((ROOT / "harness" / "harness-opencode.json").read_text())
    orch = data["agent"]["orchestrator"]["prompt"]
    assert "WAVES — plan the whole sequence before the first spawn" in orch
    assert "Never spawn harness-explore AND harness-plan-consultant on the same question" in orch
    assert "8 spawns and 4 waves per task" in orch
    assert "never spawn that kind again for the same check" in orch
    assert "load at most ONE skill" in orch
    assert "never a batch at session start" in orch
    assert "TEST BUDGET" in orch
    assert "ONE canonical command" in orch
    assert "never build a test framework" in orch
    assert "not the whole gate again" in orch
    assert "three-line edit gets a targeted check" in orch
    review = data["agent"]["code-reviewer"]["prompt"]
    assert "DELTA ON RE-RUN" in review
    assert "one verdict each (fixed / still open / regressed)" in review
    assert "do not re-audit untouched code" in review
    testy = data["agent"]["test-engineer"]["prompt"]
    assert "RIGHT-SIZING" in testy
    assert "ONE canonical command" in testy
    assert "do not add a second test framework" in testy
    assert "delta re-check" in orch and "ONE skill" in orch
    assert "canonical test command" in orch
    assert "prefer the lsp tool (definitions/references/symbols)" in orch
    assert "todowrite" in orch


def test_harness_writes_no_version_key_anywhere():
    root = Path(__file__).resolve().parent.parent
    bundled = (root / "harness" / "harness-opencode.json").read_text()
    cli = (root / "harness" / "cli.py").read_text()
    assert '"version"' not in bundled
    assert '"version"' not in cli
    assert "os.execvp(binary, [binary])" in cli
    assert 'os.environ.setdefault("OPENCODE_EXPERIMENTAL_LSP_TOOL", "true")' in cli
