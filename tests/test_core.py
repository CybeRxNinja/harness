import json, os
os.environ["HARNESS_MOCK"] = "1"

def test_router_parse():
    from harness.router import parse_model, candidates, rank
    assert parse_model("auto-fastest")["kind"] == "auto"
    assert parse_model("tag:coding+min_ctx:32k")["min_ctx"] == 32768
    assert parse_model("openrouter/foo")["kind"] == "pin"
    c = rank(candidates(parse_model("tag:coding"), {}), {})
    assert isinstance(c, list)

def test_chat_mock():
    from harness.router import chat
    m = chat([{"role": "user", "content": "hi"}], model="tag:coding", cfg={})
    assert "mock" in m.get("_route", {}) or "content" in m

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

def test_skills_scan():
    from harness import skills as S
    from harness.config import load_config
    from pathlib import Path
    cfg, _ = load_config(Path("."))
    names = [s["name"] for s in S.scan(Path("."), cfg)]
    assert "using-agent-skills" in names

def test_cli_chat():
    from harness.cli import main
    assert main(["chat", "hello in mock", "--mode", "ask"]) == 0
