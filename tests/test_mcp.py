"""mcp.py: policy gates, the stdio call, and the timeout path (a late reply used
to be handed to the next call — one answer behind).
"""
import sys

import pytest

GOOD = """import json, sys
n = 0
for line in sys.stdin:
    n += 1
    try:
        req = json.loads(line)
    except Exception:
        req = {}
    print(json.dumps({"n": n, "tool": req.get("tool")}), flush=True)
"""

SLOW = "import time\ntime.sleep(60)\n"


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return f"{sys.executable} {p}"


def _cfg(tmp_path, **servers):
    from harness.config import load_config
    cfg = load_config(tmp_path)[0]
    cfg["mcp"] = {"servers": servers}
    return cfg


def test_server_listing_and_audit(tmp_path):
    from harness import mcp
    cfg = _cfg(tmp_path,
               off={"cmd": _write(tmp_path, "g.py", GOOD), "enabled": False},
               missing={"cmd": "definitely-not-a-real-binary-xyz"})
    listed = {s["server"]: s for s in mcp.list_tools(cfg)}
    assert listed["off"]["enabled"] is False and listed["missing"]["cmd"]
    audited = {a["server"]: a["found"] for a in mcp.audit(cfg)}
    assert audited == {"off": True, "missing": False}


def test_policy_gates_come_before_any_spawn(tmp_path):
    from harness import mcp
    cfg = _cfg(tmp_path,
               guarded={"cmd": _write(tmp_path, "g.py", GOOD),
                        "tools": {"danger": "deny", "*": "ask"}})

    with pytest.raises(ValueError):
        mcp.call(cfg, "unknown", "x", {})
    with pytest.raises(PermissionError):
        mcp.call(cfg, "guarded", "x", {})  # '*' is "ask": needs approval
    with pytest.raises(PermissionError):
        mcp.call(cfg, "guarded", "danger", {}, auto_approve=True)  # deny wins even approved
    assert mcp._procs == {}, "a blocked call must not spawn anything"
    # approving an "ask" tool is what actually runs it
    assert mcp.call(cfg, "guarded", "x", {}, auto_approve=True) == {"n": 1, "tool": "x"}
    mcp._drop("guarded")

    off = _cfg(tmp_path, off={"cmd": _write(tmp_path, "g.py", GOOD), "enabled": False})
    with pytest.raises(ValueError):
        mcp.call(off, "off", "x", {}, auto_approve=True)
    with pytest.raises(ValueError):
        mcp.call(_cfg(tmp_path, nocmd={"enabled": True}), "nocmd", "x", {}, auto_approve=True)


def test_call_round_trips_json(tmp_path):
    from harness import mcp
    cfg = _cfg(tmp_path, good={"cmd": _write(tmp_path, "g.py", GOOD), "tools": {"*": "allow"}})
    try:
        assert mcp.call(cfg, "good", "skills_list", {"a": 1}) == {"n": 1, "tool": "skills_list"}
        # reuse: the process is kept alive and answers the second call in order
        assert mcp.call(cfg, "good", "skill_view", {}) == {"n": 2, "tool": "skill_view"}
    finally:
        mcp._drop("good")


def test_a_non_json_reply_degrades_to_text(tmp_path):
    from harness import mcp
    cfg = _cfg(tmp_path, plain={"cmd": _write(tmp_path, "p.py", "print('hello raw')\n"),
                                "tools": {"*": "allow"}})
    try:
        assert mcp.call(cfg, "plain", "x", {})["output"] == "hello raw"
    finally:
        mcp._drop("plain")


def test_timeout_drops_the_process_so_replies_stay_in_order(tmp_path, monkeypatch):
    from harness import mcp
    monkeypatch.setenv("HARNESS_MCP_TIMEOUT", "0.4")
    cfg = _cfg(tmp_path,
               slow={"cmd": _write(tmp_path, "s.py", SLOW), "tools": {"*": "allow"}},
               good={"cmd": _write(tmp_path, "g.py", GOOD), "tools": {"*": "allow"}})
    try:
        out = mcp.call(cfg, "slow", "any", {})
        assert "timeout" in out.get("error", ""), out
        assert "slow" not in mcp._procs, "a timed-out server is dropped, not left parked"
        assert "slow" not in mcp._last_used
        # the next call gets ITS OWN answer, not the abandoned one
        assert mcp.call(cfg, "good", "x", {}) == {"n": 1, "tool": "x"}
    finally:
        mcp._drop("slow")
        mcp._drop("good")


def test_timeout_is_env_tunable(monkeypatch):
    from harness import mcp
    monkeypatch.setenv("HARNESS_MCP_TIMEOUT", "2.5")
    assert mcp._timeout() == 2.5
    monkeypatch.setenv("HARNESS_MCP_TIMEOUT", "not-a-number")
    assert mcp._timeout() == 15.0
