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
