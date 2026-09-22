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
