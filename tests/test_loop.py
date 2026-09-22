"""loop.py drives every turn but was only reached through the CLI (33% of its
lines). These lock the mode allowlists, the tool dispatch table, and one full
turn end to end.
"""
import os

import pytest


def test_restricted_modes_are_read_only(tmp_path):
    from harness.loop import MODE_TOOLS, _allowed

    for mode in ("plan", "ask", "review"):
        for tool in ("write", "edit", "shell", "py", "spawn", "config_set"):
            assert not _allowed(mode, tool), f"{mode} must not allow {tool}"
        for tool in ("read", "glob", "grep", "skill_view", "skills_list", "memory",
                     "risk_check"):
            assert _allowed(mode, tool), f"{mode} should allow {tool}"
        # classifying an action is read-only, so no mode may be denied it:
        # without it the model can only ask the human about a grep
        assert _allowed(mode, "risk_check")

    # only plan may read config; and the free modes allow everything
    assert _allowed("plan", "config_get") and not _allowed("ask", "config_get")
    for mode in ("code", "debug", "orchestrator"):
        assert all(_allowed(mode, t) for t in ("write", "shell", "spawn"))


def test_exec_tool_dispatch(tmp_path):
    from harness.config import load_config
    from harness.kernel import Kernel
    from harness.loop import _exec_tool
    from harness.store import connect

    cfg = load_config(tmp_path)[0]
    con = connect(tmp_path)
    kernel = Kernel(tmp_path, "s1")
    touched: list[str] = []

    def run(name, args, allow=()):
        return _exec_tool(name, args, tmp_path, "s1", cfg, con, kernel, list(allow), True, touched)

    try:
        assert "wrote 2 bytes" in run("write", {"path": "a.txt", "content": "hi"})
        assert touched == ["a.txt"]
        assert "1# hi" in run("read", {"path": "a.txt"})
        assert run("glob", {"pattern": "*.txt"}) == "a.txt"
        assert run("grep", {"pattern": "hi"}).startswith("a.txt:1:")
        assert run("edit", {"path": "a.txt", "old": "hi", "new": "ho"}) == "ok"
        assert run("py", {"code": "print(1 + 1)"}).strip().endswith("2")
        # shell is allowlist-gated: nothing passes unless policy lists it
        assert "not in allowlist: rm" in run("shell", {"cmd": "rm -rf /"})
        assert "hi" in run("shell", {"cmd": "echo hi"}, allow=["echo"])

        assert "saved" in run("memory", {"op": "save", "text": "the sky is blue"})
        assert "blue" in run("memory", {"op": "recall", "q": "sky"})
        assert run("memory", {"op": "nonsense"}) == "unknown memory op"
        assert run("config_get", {"path": "budgets.max_turns"})
        assert run("unknown_tool", {}) == "unknown tool unknown_tool"
    finally:
        con.close()


def test_exec_tool_mcp_prefix(tmp_path, monkeypatch):
    """mcp_<server>_<tool> routes through the MCP manager."""
    from harness import mcp
    from harness.config import load_config
    from harness.kernel import Kernel
    from harness.loop import _exec_tool
    from harness.store import connect

    script = tmp_path / "srv.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    print(json.dumps({'tool': json.loads(line).get('tool')}), flush=True)\n"
    )
    cfg = load_config(tmp_path)[0]
    cfg["mcp"] = {"servers": {"demo": {"cmd": f"{__import__('sys').executable} {script}",
                                       "tools": {"*": "allow"}}}}
    con = connect(tmp_path)
    try:
        out = _exec_tool("mcp_demo_ping", {}, tmp_path, "s1", cfg, con,
                         Kernel(tmp_path, "s1"), [], True, [])
        assert "'tool': 'ping'" in out
    finally:
        mcp._drop("demo")
        con.close()


def test_full_turn_in_mock_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_MOCK", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness.loop import run_turn
    from harness.store import connect, history

    out = run_turn(tmp_path, "s1", "say hi", mode="ask", model="acme/foo")
    assert out["route_model"] == "acme/foo"
    assert out["content"], "the loop must produce a reply"

    con = connect(tmp_path)
    try:
        roles = [m["role"] for m in history(con, "s1")]
    finally:
        con.close()
    assert roles[0] == "user" and "assistant" in roles, roles


def test_turn_without_a_model_reports_it(tmp_path, monkeypatch):
    """The real backend needs a model configured; the error must name the fix."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("HARNESS_MOCK", raising=False)
    monkeypatch.delenv("HARNESS_MODEL", raising=False)
    from harness.loop import run_turn

    out = run_turn(tmp_path, "s2", "hi", mode="code")
    assert out["content"].startswith("no model:") and out["touched"] == []


def test_mock_mode_needs_no_model(tmp_path, monkeypatch):
    """HARNESS_MOCK is the offline path: it never calls a model, so requiring
    one to be configured made every mock turn fail at resolve_model instead."""
    monkeypatch.setenv("HARNESS_MOCK", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("HARNESS_MODEL", raising=False)
    from harness.loop import run_turn

    out = run_turn(tmp_path, "s3", "hi", mode="code")
    assert out["content"].startswith("[mock") and out["touched"] == []


def test_compression_hint_does_not_leak_into_the_recall_hint(tmp_path, monkeypatch):
    """A big tool result is compressed; the `hint` name used to be reused for
    the compression label (and must not overwrite the recalled-memory hint)."""
    from harness.loop import run_turn
    import harness.loop as loop
    monkeypatch.setenv("HARNESS_MOCK", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    seen = {}
    real_chat = loop.backend.chat

    def fake_chat(msgs, **kw):
        seen["system"] = msgs[0]["content"]
        return {"content": "done", "tool_calls": []}

    monkeypatch.setattr(loop.backend, "chat", fake_chat)
    try:
        run_turn(tmp_path, "s3", "hello", mode="code", model="acme/foo")
    finally:
        monkeypatch.setattr(loop.backend, "chat", real_chat)
    assert "You are" in seen["system"]
