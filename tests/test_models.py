import json
import os
os.environ["HARNESS_MOCK"] = "1"


def _user_model_home(tmp_path, monkeypatch, model="acme/bar-1"):
    cfgdir = tmp_path / "cfg"
    (cfgdir / "opencode").mkdir(parents=True)
    (cfgdir / "opencode" / "opencode.json").write_text(json.dumps({"model": model}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    return model


def test_resolve_explicit_passthrough(tmp_path, monkeypatch):
    _user_model_home(tmp_path, monkeypatch)
    from harness import models as M
    assert M.resolve_model("acme/other", {}) == "acme/other"


def test_resolve_bare_falls_back_to_user_default(tmp_path, monkeypatch):
    want = _user_model_home(tmp_path, monkeypatch, "acme/bar-1")
    from harness import models as M
    assert M.resolve_model("", {}) == want
    assert M.resolve_model("tag:reasoning", {}) == want  # legacy router id
    assert M.resolve_model("auto-fastest", {}) == want


def test_resolve_profile_with_slash(tmp_path, monkeypatch):
    _user_model_home(tmp_path, monkeypatch)
    from harness import models as M
    assert M.resolve_model("", {"model_profile": "acme/via-env"}) == "acme/via-env"


def test_resolve_missing_raises(tmp_path, monkeypatch):
    cfgdir = tmp_path / "empty"
    cfgdir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    from harness import models as M
    import pytest
    with pytest.raises(RuntimeError):
        M.resolve_model("", {})


def test_parse_tool_calls():
    from harness.models import parse_tool_calls
    text, calls = parse_tool_calls('thinking\n[[tool:read {"path": "x.py"}]]\ntail')
    assert text == "thinking\n\ntail"
    assert calls[0]["function"]["name"] == "read"
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "x.py"}
    text, calls = parse_tool_calls('[[tool:a {"x": 1}]]\nmid\n[[tool:b {bad json]]')
    assert len(calls) == 1 and calls[0]["function"]["name"] == "a"
    assert "[[tool:b" in text  # malformed block preserved
    text, calls = parse_tool_calls("plain answer")
    assert text == "plain answer" and calls == []


def test_render_prompt_includes_tools_and_truncates():
    from harness.models import render_prompt, MAX_PROMPT_CHARS
    tools = [{"type": "function", "function": {"name": "read", "description": "r",
             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    p = render_prompt([{"role": "user", "content": "hi"}], tools)
    assert "[[tool:name" in p and "- read:" in p and "USER: hi" in p
    big = render_prompt([{"role": "user", "content": "x" * (MAX_PROMPT_CHARS + 100)}])
    assert len(big) <= MAX_PROMPT_CHARS + 100 and "truncated" in big


def test_chat_mock_never_spawns(tmp_path, monkeypatch):
    _user_model_home(tmp_path, monkeypatch)
    import subprocess
    def _boom(*a, **k):
        raise AssertionError("must not spawn in MOCK mode")
    monkeypatch.setattr(subprocess, "run", _boom)
    from harness import models as M
    m = M.chat([{"role": "user", "content": "hi"}], model="", cfg={})
    assert "mock" in m["content"] and m["_route"]["provider"] == "opencode"


def test_chat_runs_opencode_and_parses(tmp_path, monkeypatch):
    _user_model_home(tmp_path, monkeypatch, "acme/bar-1")
    monkeypatch.delenv("HARNESS_MOCK")
    import subprocess
    import shutil
    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/usr/bin/opencode")
    captured = {}
    class R:
        returncode = 0
        stdout = 'done\n[[tool:grep {"pattern": "x"}]]'
        stderr = ""
    def _fake(cmd, **k):
        captured["cmd"] = cmd
        assert cmd[:3] == ["/usr/bin/opencode", "run", "--model"]
        assert cmd[3] == "acme/bar-1"
        return R()
    monkeypatch.setattr(subprocess, "run", _fake)
    from harness import models as M
    m = M.chat([{"role": "user", "content": "go"}], model="", cfg={},
               tools=[{"type": "function", "function": {"name": "grep"}}])
    assert m["content"] == "done"
    assert m["tool_calls"][0]["function"]["name"] == "grep"
    assert "AVAILABLE TOOLS" in captured["cmd"][4]


def test_chat_failure_modes(tmp_path, monkeypatch):
    _user_model_home(tmp_path, monkeypatch)
    monkeypatch.delenv("HARNESS_MOCK")
    import subprocess
    import shutil
    import pytest
    from harness import models as M
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        M.chat([{"role": "user", "content": "hi"}], cfg={})
    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/usr/bin/opencode")
    class R:
        returncode = 1
        stdout = ""
        stderr = "boom"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    with pytest.raises(RuntimeError):
        M.chat([{"role": "user", "content": "hi"}], cfg={})
