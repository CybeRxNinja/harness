import json, os
os.environ["HARNESS_MOCK"] = "1"

def test_models_backend(tmp_path, monkeypatch):
    from harness.models import resolve_model, chat, parse_tool_calls
    import pytest
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    assert resolve_model("acme/foo", {}) == "acme/foo"
    with pytest.raises(RuntimeError):
        resolve_model("tag:reasoning", {})  # legacy id, no user default configured
    m = chat([{"role": "user", "content": "hi"}], model="acme/foo", cfg={})
    assert "mock" in m.get("content", "") and m["_route"]["provider"] == "opencode"
    text, calls = parse_tool_calls('x [[tool:read {"path": "a"}]] y')
    assert calls and calls[0]["function"]["name"] == "read"

def test_kernel(tmp_path):
    from harness.kernel import Kernel
    k = Kernel(tmp_path, "s1")
    r = k.execute("x = 1 + 1")
    assert r["ok"]
    r = k.execute("import socket")
    assert not r["ok"]

def test_tools_jail(tmp_path):
    from harness import tools as T
    import pytest
    (tmp_path / "a.txt").write_text("hello")
    assert "hello" in T.read(tmp_path, "a.txt")
    try:
        T.read(tmp_path, "/etc/hostname")
        assert False, "should jail"
    except PermissionError:
        pass

def test_config_layers(tmp_path, monkeypatch):
    from harness import config
    cfg, info = config.load_config(tmp_path)
    assert "budgets" in cfg
    try:
        config.set_value(tmp_path, "token", "x", "user")
        assert False
    except PermissionError:
        pass

def test_skills_scan(tmp_path, monkeypatch):
    from harness import skills as S
    from harness.config import load_config
    from pathlib import Path
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    assert "using-agent-skills" in S.ensure_seed_skills()
    cfg, _ = load_config(Path("."))
    names = [s["name"] for s in S.scan(Path("."), cfg)]
    assert "using-agent-skills" in names

def test_cli_chat():
    from harness.cli import main
    assert main(["chat", "hello in mock", "--mode", "ask"]) == 0
