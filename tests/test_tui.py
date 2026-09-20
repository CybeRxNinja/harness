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


def test_relay_baseurl_follows_port(tmp_path, monkeypatch):
    import json
    cfgdir = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    monkeypatch.setenv("HARNESS_URL", "http://127.0.0.1:9999")
    from harness.cli import ensure_opencode_config
    ensure_opencode_config()
    d = json.loads((cfgdir / "opencode" / "opencode.json").read_text())
    assert d["provider"]["harness"]["options"]["baseURL"] == "http://127.0.0.1:9999/v1"
    # custom edits elsewhere survive; rerun is idempotent
    d["provider"]["harness"]["options"]["apiKey"] = "CUSTOM"
    (cfgdir / "opencode" / "opencode.json").write_text(json.dumps(d))
    monkeypatch.delenv("HARNESS_URL")
    ensure_opencode_config()
    d2 = json.loads((cfgdir / "opencode" / "opencode.json").read_text())
    assert d2["provider"]["harness"]["options"]["apiKey"] == "CUSTOM"
    assert d2["provider"]["harness"]["options"]["baseURL"] == "http://127.0.0.1:8787/v1"


def test_find_opencode(tmp_path, monkeypatch):
    from harness import cli
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\ntrue\n")
    fake.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda *a, **k: str(fake))
    assert cli._find_opencode() == str(fake)
