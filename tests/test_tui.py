def test_tui_config_merge(tmp_path, monkeypatch):
    import json
    cfgdir = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    from harness.cli import ensure_opencode_config
    dest = tmp_path / "cfg" / "opencode" / "opencode.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"model": "openai/gpt", "provider": {"openai": {}}}))
    out = ensure_opencode_config()
    d = json.loads(open(out).read())
    assert d["model"] == "openai/gpt"  # user value kept
    assert "harness" in d["provider"]  # harness added
    assert d["provider"]["harness"]["options"]["apiKey"] == "{env:HARNESS_TOKEN}"
    assert set(("orchestrator", "ask", "debug", "review")) <= set(d["agent"])
    # idempotent
    ensure_opencode_config()
    d2 = json.loads(open(out).read())
    assert d2 == d
