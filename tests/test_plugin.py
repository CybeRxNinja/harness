import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_plugin_files_exist():
    from harness.cli import _plugin_files
    files = _plugin_files()
    assert len(files) == 1 and Path(files[0]).exists()
    text = Path(files[0]).read_text()
    assert "export default" in text and "experimental.session.compacting" in text
    assert "skills_list" in text and "memory_recall" in text
    # loader contract lock (opencode v2.0.8): default-exported object with id +
    # effect (setup loads clean but is never invoked — marker probe 2026-09-20).
    # Zero runtime imports: the server resolves plugin imports in an isolated
    # registry and node_modules is reconciled away, so any value-level import
    # fails load with ResolveMessage (err_51f4c6d8). Tools are plain objects
    # with JSON-Schema inputSchema, which the registry accepts natively.
    assert 'id: "harness"' in text and "effect:" in text
    assert "export default HarnessPlugin" in text
    assert "inputSchema" in text
    assert "\nimport type " in text and "\nimport {" not in text and "\nimport " not in text.replace("\nimport type ", "")


def test_plugin_install_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness.cli import _opencode_config_path
    r = subprocess.run([sys.executable, "-m", "harness", "plugin", "install"],
                       capture_output=True, text=True, timeout=60,
                       cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[:300]
    d = json.loads((_opencode_config_path()).read_text())
    assert not [p for p in d.get("plugin", []) if "harness/plugin" in str(p)], "legacy specs removed"
    installed = Path(os.environ["XDG_CONFIG_HOME"]) / "opencode" / "plugins" / "harness.ts"
    assert installed.exists()
    r = subprocess.run([sys.executable, "-m", "harness", "plugin", "install"],
                       capture_output=True, text=True, timeout=60,
                       cwd=str(REPO_ROOT))
    assert r.returncode == 0
    # install merges agents (model-free, inherit user default) and no relay
    d = json.loads((_opencode_config_path()).read_text())
    assert "harness" not in d.get("provider", {})
    assert "orchestrator" in d.get("agent", {})
    r = subprocess.run([sys.executable, "-m", "harness", "plugin", "uninstall"],
                       capture_output=True, text=True, timeout=60,
                       cwd=str(REPO_ROOT))
    assert r.returncode == 0 and "removed plugin file" in r.stdout
    d = json.loads((_opencode_config_path()).read_text())
    assert "harness" not in d.get("provider", {})
    assert not (Path(os.environ["XDG_CONFIG_HOME"]) / "opencode" / "plugins" / "harness.ts").exists()
