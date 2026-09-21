"""The MCP stdio server (harness mcp) had no tests at all: dispatch, the three
tools, the JSON-RPC error envelope, and the readline-not-read() contract.
"""
import json, os, subprocess, sys


def _cfg(root):
    from harness.config import load_config
    return load_config(root)[0]


def test_dispatch_speaks_minimal_jsonrpc(tmp_path):
    from harness import mcp_server as M
    cfg = _cfg(tmp_path)

    init = M.dispatch("initialize", {}, tmp_path, cfg)
    assert init["protocolVersion"] == M.PROTOCOL
    assert init["serverInfo"]["name"] == "harness-skills"
    assert "tools" in init["capabilities"]

    names = [t["name"] for t in M.dispatch("tools/list", {}, tmp_path, cfg)["tools"]]
    assert names == ["skills_list", "skill_view", "memory_recall"]
    for tool in M.dispatch("tools/list", {}, tmp_path, cfg)["tools"]:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"


def test_skills_list_and_view(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    from harness import mcp_server as M
    from harness import skills as S
    assert "using-agent-skills" in S.ensure_seed_skills()
    cfg = _cfg(tmp_path)

    listed = M.dispatch("tools/call", {"name": "skills_list", "arguments": {}}, tmp_path, cfg)
    text = listed["content"][0]["text"]
    assert "using-agent-skills" in text and text.startswith("- ")

    body = M.dispatch("tools/call",
                      {"name": "skill_view", "arguments": {"name": "using-agent-skills"}},
                      tmp_path, cfg)["content"][0]["text"]
    assert "## Procedure" in body

    # a missing skill is a readable answer, not a stack trace
    missing = M.dispatch("tools/call",
                         {"name": "skill_view", "arguments": {"name": "nope"}},
                         tmp_path, cfg)["content"][0]["text"]
    assert "error" in missing.lower()


def test_memory_recall_on_an_empty_store(tmp_path):
    from harness import mcp_server as M
    out = M.dispatch("tools/call", {"name": "memory_recall", "arguments": {"query": "anything"}},
                     tmp_path, _cfg(tmp_path))
    assert "(nothing recalled)" in out["content"][0]["text"]


def test_unknown_tool_and_method_raise(tmp_path):
    import pytest
    from harness import mcp_server as M
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError):
        M.dispatch("tools/call", {"name": "nope", "arguments": {}}, tmp_path, cfg)
    with pytest.raises(ValueError):
        M.dispatch("does/not/exist", {}, tmp_path, cfg)


def _rpc(proc, obj, expect_reply=True):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()
    if not expect_reply:
        return None
    return json.loads(proc.stdout.readline())


def test_stdio_server_round_trip(tmp_path):
    """Live stdio: newline-delimited frames, notifications silent, clean EOF exit.

    This also locks the deadlock fix — the loop must read a line at a time, since
    a live client holds stdin open between messages.
    """
    env = dict(os.environ, HARNESS_MOCK="1", XDG_CONFIG_HOME=str(tmp_path / "cfg"),
               HARNESS_HOME=str(tmp_path / "home"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "harness", "--root", str(tmp_path), "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(tmp_path),
    )
    try:
        init = _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init["id"] == 1 and init["result"]["serverInfo"]["name"] == "harness-skills"

        # notifications must not produce a reply
        _rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)
        listed = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert [t["name"] for t in listed["result"]["tools"]][0] == "skills_list"

        called = _rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "skills_list", "arguments": {}}})
        assert isinstance(called["result"]["content"][0]["text"], str)

        bad = _rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "nope"})
        assert bad["error"]["code"] == -32603 and "unknown method" in bad["error"]["message"]

        # a garbage frame is ignored rather than killing the server
        proc.stdin.write("not json\n")
        proc.stdin.flush()
        again = _rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
        assert again["id"] == 5
    finally:
        proc.stdin.close()
    assert proc.wait(timeout=60) == 0


def test_stdio_trace_logs_frames(tmp_path):
    trace = tmp_path / "trace.log"
    env = dict(os.environ, HARNESS_MOCK="1", XDG_CONFIG_HOME=str(tmp_path / "cfg"),
               HARNESS_MCP_TRACE=str(trace))
    proc = subprocess.Popen(
        [sys.executable, "-m", "harness", "--root", str(tmp_path), "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env, cwd=str(tmp_path),
    )
    try:
        _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    finally:
        proc.stdin.close()
    proc.wait(timeout=60)
    assert "recv:" in trace.read_text()
