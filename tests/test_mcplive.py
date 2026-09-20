import json
import os
import select
import subprocess
import sys
from pathlib import Path

os.environ["HARNESS_MOCK"] = "1"

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_mcp_live_pipe_no_deadlock(tmp_path, monkeypatch):
    """Feed requests on a HELD-OPEN pipe (like real MCP clients). The old
    read(n) implementation deadlocked here; readline must answer promptly."""
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    p = subprocess.Popen(
        [sys.executable, "-m", "harness", "--root", str(tmp_path), "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT))

    def ask(mid, method, params=None):
        p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                                   "params": params or {}}) + "\n").encode())
        p.stdin.flush()
        r, _, _ = select.select([p.stdout], [], [], 15)
        assert r, f"no response to {method} within 15s (deadlock?)"
        return json.loads(p.stdout.readline().decode())

    try:
        r1 = ask(1, "initialize")
        assert r1["result"]["protocolVersion"] == "2024-11-05"
        r2 = ask(2, "tools/list")
        assert {t["name"] for t in r2["result"]["tools"]} == {
            "skills_list", "skill_view", "memory_recall"}
    finally:
        try:
            p.stdin.close()
        except BrokenPipeError:
            pass
        p.wait(timeout=15)
