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
    assert d["model"] == "openai/gpt"  # user value kept, never pinned
    assert "harness" not in d.get("provider", {})  # no relay provider
    assert set(("orchestrator", "ask", "debug", "review")) <= set(d["agent"])
    for spec in d["agent"].values():  # agents inherit the user default
        assert "model" not in spec
        assert "harness/" not in json.dumps(spec)
    # idempotent
    ensure_opencode_config()
    d2 = json.loads(open(out).read())
    assert d2 == d


def test_merge_cleans_legacy_relay(tmp_path, monkeypatch):
    import json
    cfgdir = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    from harness.cli import ensure_opencode_config
    dest = tmp_path / "cfg" / "opencode" / "opencode.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({
        "model": "harness/auto-fastest",
        "provider": {"harness": {"options": {"baseURL": "http://127.0.0.1:8787/v1"}}},
        "agent": {"orchestrator": {"model": "harness/tag:reasoning", "prompt": "mine"}},
    }))
    out = ensure_opencode_config()
    d = json.loads(open(out).read())
    assert "harness" not in d.get("provider", {})
    assert "model" not in d  # harness default dropped (user sets their own)
    assert d["agent"]["orchestrator"].get("prompt") == "mine"  # user keys kept
    assert "model" not in d["agent"]["orchestrator"]  # harness pin dropped


def test_find_opencode(tmp_path, monkeypatch):
    from harness import cli
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\ntrue\n")
    fake.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda *a, **k: str(fake))
    assert cli._find_opencode() == str(fake)
