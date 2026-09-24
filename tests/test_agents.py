def test_lsp_default_on_but_respects_user(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness.cli import _opencode_config_path
    dest = _opencode_config_path()
    # absent -> enabled
    from harness.cli import ensure_opencode_config
    ensure_opencode_config()
    d = json.loads(dest.read_text())
    assert d["lsp"] is True
    # user-disabled stays disabled
    d["lsp"] = False
    dest.write_text(json.dumps(d))
    ensure_opencode_config()
    assert json.loads(dest.read_text())["lsp"] is False
    # user object config preserved
    d["lsp"] = {"typescript": {"disabled": True}}
    dest.write_text(json.dumps(d))
    ensure_opencode_config()
    assert json.loads(dest.read_text())["lsp"] == {"typescript": {"disabled": True}}


def test_agent_permissions_modern():
    import json
    from pathlib import Path
    d = json.loads((Path("harness") / "harness-opencode.json").read_text())
    for name, spec in d["agent"].items():
        assert "tools" not in spec, f"{name} uses deprecated tools key"
        perm = spec.get("permission", {})
        assert isinstance(perm, dict), name
        # auto-approve compatible: only allow/ask/deny scalars or pattern maps
        for k, v in perm.items():
            assert isinstance(v, (str, dict)), (name, k)
            if isinstance(v, str):
                assert v in ("allow", "ask", "deny"), (name, k, v)
    assert "MCP tools" not in d["agent"]["orchestrator"]["prompt"]
    assert "skills_list" in d["agent"]["orchestrator"]["prompt"]


def test_no_agent_denies_bash():
    """opencode only advertises its `shell` tool when bash is not denied, and
    zen's free-tier gate 403s any request whose tool list has no `shell`
    ("OpenCode's free tier can only be used from within OpenCode"). A read-only
    agent must therefore keep the shell tool behind approval — `edit: deny`
    blocks writes, `bash: "ask"` prompts per command — never hide it."""
    import json
    from pathlib import Path
    d = json.loads((Path("harness") / "harness-opencode.json").read_text())
    for name, spec in d["agent"].items():
        bash = spec.get("permission", {}).get("bash")
        assert bash != "deny", (
            f"agent.{name} denies bash: no shell tool -> zen free models 403"
        )


def test_legacy_bash_policy_is_regenerated(tmp_path, monkeypatch):
    """Pre-existing installs carry a bare `bash: "deny"` or `"ask"` in
    opencode.json; the merge must replace the harness-shipped string with the
    generated risk map (setdefault alone would not) while leaving a user's own
    agent, and a user's own permission object, untouched."""
    import json
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness.cli import _opencode_config_path, ensure_opencode_config
    dest = _opencode_config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({
        "agent": {
            "ask": {"description": "Answers from the codebase. No writes.",
                    "mode": "primary",
                    "permission": {"edit": "deny", "bash": "deny", "task": "deny"}},
            "review": {"description": "Staff-engineer review: five axes, Nit/Blocker severity.",
                       "mode": "primary", "permission": {"edit": "deny", "bash": "deny"}},
            "mine": {"description": "my own read-only agent",
                     "permission": {"edit": "deny", "bash": "deny"}},
        }
    }))
    ensure_opencode_config()
    from harness import risk
    d = json.loads(dest.read_text())
    for name in ("ask", "review"):
        bash = d["agent"][name]["permission"]["bash"]
        assert isinstance(bash, dict), (name, bash)
        # exactly the classifier's policy: no drift between the rule the model
        # is told about and the rule opencode enforces
        assert bash == risk.bash_permission_map(), name
        assert bash["*"] == "allow" and bash["git push"] == "ask"
    # read-only intent survives: edits stay denied, never a prompt
    assert d["agent"]["ask"]["permission"]["edit"] == "deny"
    # a user's own agent is never touched
    assert d["agent"]["mine"]["permission"]["bash"] == "deny", d["agent"]["mine"]


def test_second_merge_is_a_no_op_and_a_user_object_survives(tmp_path, monkeypatch):
    """Re-running the merge must not churn the file, and a permission object the
    user wrote themselves must never be replaced by the generated map."""
    import json
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg2"))
    from harness.cli import _opencode_config_path, ensure_opencode_config
    dest = _opencode_config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({
        "agent": {"orchestrator": {"permission": {"bash": {"*": "deny", "git status *": "allow"}}}}}))
    ensure_opencode_config()
    first = json.loads(dest.read_text())
    assert first["agent"]["orchestrator"]["permission"]["bash"] == {"*": "deny", "git status *": "allow"}
    ensure_opencode_config()
    assert json.loads(dest.read_text()) == first


def test_specialized_subagents_ship_with_their_own_instructions():
    """The orchestrator can only route to subagents opencode knows about, and
    the fallback is the built-in `general`. Ship the harness roles as
    mode=subagent agents with distinct prompts (not one generic string
    relabeled), read-only unless the role writes, and name them in the
    orchestrator's prompt so the routing is not left to chance."""
    import json
    from pathlib import Path
    d = json.loads((Path("harness") / "harness-opencode.json").read_text())
    subs = {n: s for n, s in d["agent"].items() if s.get("mode") == "subagent"}
    want = ("explore", "librarian", "plan-consultant", "plan-reviewer",
            "code-reviewer", "test-engineer", "security-auditor")
    for name in want:
        assert name in subs, name
        spec = subs[name]
        assert spec.get("description"), f"{name} needs a description (opencode requires one)"
        assert len(str(spec.get("prompt", ""))) > 80, name
        assert "model" not in spec, f"{name} must inherit the user's default model"
        # depth 1: a subagent never spawns another subagent
        assert spec.get("permission", {}).get("task") == "deny", name
        edit = spec.get("permission", {}).get("edit")
        assert edit == ("allow" if name == "test-engineer" else "deny"), (name, edit)
    prompts = [s["prompt"] for s in subs.values()]
    assert len(set(prompts)) == len(prompts), "each subagent needs its own instructions"
    orchestrator = d["agent"]["orchestrator"]["prompt"]
    for name in want:
        assert name in orchestrator, f"orchestrator prompt does not route to {name}"


def test_agent_prompts_carry_the_resource_discipline():
    """The lean-task session that re-read product files ~20 times, booted two
    headless browsers in parallel and wrote scratch to /tmp was a prompt
    problem, not a model problem. The lanes and the scratch rule must live in
    the shipped prompts, where the behavior is decided."""
    import json
    from pathlib import Path
    d = json.loads((Path("harness") / "harness-opencode.json").read_text())
    orch = d["agent"]["orchestrator"]["prompt"]
    assert "WORKFLOW DISCIPLINE" in orch
    assert "never load a whole large file" in orch, "whole-file reads burned the budget"
    assert "screenshots or images" in orch, "full-page PNGs must stay out of context"
    assert ".opencode/harness/tmp/" in orch
    assert "at most 2 per wave" in orch
    review = d["agent"]["code-reviewer"]["prompt"]
    assert "static review only" in review, "reviewers must not boot browsers too"
    assert "browser" in review
    testy = d["agent"]["test-engineer"]["prompt"]
    assert ".opencode/harness/tmp/" in testy, "scratch belongs in the project"
    assert "never install" in testy, "workers must not mutate the system"
    assert "ONE browser/server session" in testy, "one launch, every viewport"


def test_harness_originated_prompts_refresh_but_a_user_rewrite_stays(tmp_path, monkeypatch):
    """Guidance updates must reach installs that already carry the agent —
    setdefault alone would leave them on the stale prompt forever. The tell is
    the opening line: a prompt still opening with the line this build ships is
    harness-originated and is brought up to date; any other opening is the
    owner's rewrite and stays untouched."""
    import json
    from pathlib import Path
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg9"))
    from harness.cli import _opencode_config_path, ensure_opencode_config
    shipped = json.loads((Path("harness") / "harness-opencode.json").read_text())
    head = shipped["agent"]["orchestrator"]["prompt"].split("\n", 1)[0]
    dest = _opencode_config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"agent": {
        "orchestrator": {"prompt": head + "\n\nan older harness body"},
        "code-reviewer": {"prompt": "my own review style, first line rewritten"},
    }}))
    ensure_opencode_config()
    d = json.loads(dest.read_text())
    assert d["agent"]["orchestrator"]["prompt"] == shipped["agent"]["orchestrator"]["prompt"]
    assert d["agent"]["code-reviewer"]["prompt"] == "my own review style, first line rewritten"
    # and re-running is a no-op: no churn for an already-current install
    ensure_opencode_config()
    assert json.loads(dest.read_text()) == d
    # a prompt from an OLDER release (a first line only harness ever shipped)
    # is recognized too — that is the state real upgraders are in
    from harness.cli import LEGACY_PROMPT_HEADS
    legacy = sorted(LEGACY_PROMPT_HEADS["orchestrator"])[0]
    dest.write_text(json.dumps({"agent": {
        "orchestrator": {"prompt": legacy + "\n\nan old body"}}}))
    ensure_opencode_config()
    d = json.loads(dest.read_text())
    assert d["agent"]["orchestrator"]["prompt"] == shipped["agent"]["orchestrator"]["prompt"]


def test_a_stale_generated_bash_map_is_refreshed_but_a_user_map_stays(tmp_path, monkeypatch):
    """A map generated by an OLDER build sits in opencode.json as a dict, and
    setdefault never rewrites it — so new ask rules (installs,
    outside-project scratch) would never reach an installed config. The
    harness-only anchor key tells ours from the owner's own object, which
    must survive untouched."""
    import json
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg4"))
    from harness import risk
    from harness.cli import _opencode_config_path, ensure_opencode_config
    dest = _opencode_config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    stale = {"*": "allow", "rm": "ask", "rm *": "ask",
             "harness checkpoint restore": "ask"}  # an older build's output
    dest.write_text(json.dumps({"agent": {
        "orchestrator": {"permission": {"bash": stale}},
        "mine": {"permission": {"bash": {"*": "deny"}}},
    }}))
    ensure_opencode_config()
    d = json.loads(dest.read_text())
    cur = risk.bash_permission_map()
    assert d["agent"]["orchestrator"]["permission"]["bash"] == cur
    assert cur.get("pip install *") == "ask", "new rules must reach old installs"
    # a user's own permission object has no harness anchor and is never touched
    assert d["agent"]["mine"]["permission"]["bash"] == {"*": "deny"}
