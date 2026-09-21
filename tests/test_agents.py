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


def test_legacy_bash_deny_is_migrated(tmp_path, monkeypatch):
    """Installs made before this fix carry bash deny in opencode.json; the merge
    must repair the harness-shipped value (setdefault alone would not) while
    leaving a user's own agent alone."""
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
    d = json.loads(dest.read_text())
    assert d["agent"]["ask"]["permission"]["bash"] == "ask", d["agent"]["ask"]
    assert d["agent"]["review"]["permission"]["bash"] == "ask", d["agent"]["review"]
    # read-only intent survives: edits stay denied
    assert d["agent"]["ask"]["permission"]["edit"] == "deny"
    # a user's own agent is never rewritten
    assert d["agent"]["mine"]["permission"]["bash"] == "deny", d["agent"]["mine"]
