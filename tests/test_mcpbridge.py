import json
import os
import subprocess
import sys

HARNESS = [sys.executable, "-m", "harness"]


def _rpc(payloads, root="."):
    p = subprocess.run(HARNESS + ["--root", root, "mcp"], input="\n".join(
        json.dumps(m) for m in payloads).encode(), capture_output=True, timeout=60)
    assert p.returncode == 0, p.stderr.decode()[:300]
    return [json.loads(l) for l in p.stdout.decode().splitlines() if l.strip()]


def test_mcp_initialize_and_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    out = _rpc([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}],
               root=str(tmp_path))
    by_id = {m["id"]: m for m in out}
    assert by_id[1]["result"]["protocolVersion"] == "2024-11-05"
    names = [t["name"] for t in by_id[2]["result"]["tools"]]
    assert {"skills_list", "skill_view", "memory_recall"} <= set(names)


def test_mcp_skill_view_and_recall(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    from harness.skills import ensure_seed_skills
    assert ensure_seed_skills(), "seeds should install on empty home"
    assert ensure_seed_skills() == [], "second run installs nothing (no clobber)"
    out = _rpc([{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "skills_list", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "skill_view", "arguments": {"name": "using-agent-skills"}}},
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                 "params": {"name": "memory_recall", "arguments": {"query": "nothing here"}}}],
               root=str(tmp_path))
    by_id = {m["id"]: m for m in out}
    assert "using-agent-skills" in by_id[3]["result"]["content"][0]["text"]
    assert "When to Use" in by_id[4]["result"]["content"][0]["text"]
    assert isinstance(by_id[5]["result"]["content"][0]["text"], str)


def test_merge_skips_mcp_block(tmp_path, monkeypatch):
    # opencode gets skills as NATIVE plugin tools; the stdio MCP server is
    # for non-opencode clients only, so the merge must not add it.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness.cli import ensure_opencode_config
    ensure_opencode_config()
    import json as _j
    d = _j.loads((tmp_path / "cfg" / "opencode" / "opencode.json").read_text())
    assert "harness-skills" not in d.get("mcp", {})
    assert "harness" in d.get("provider", {})
