def test_plugin_files_exist():
    from harness.cli import _plugin_files
    from pathlib import Path
    files = _plugin_files()
    assert len(files) == 1 and Path(files[0]).exists()
    text = Path(files[0]).read_text()
    assert "export default" in text and "experimental.session.compacting" in text
    assert "skills_list" in text and "memory_recall" in text


def test_plugin_install_idempotent(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness.cli import ensure_opencode_config  # noqa
    from harness.cli import _opencode_config_path
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "harness", "plugin", "install"],
                       capture_output=True, text=True, timeout=60,
                       cwd="/home/jailbreaker20/Projects/harness")
    assert r.returncode == 0, r.stderr[:300]
    d = json.loads((_opencode_config_path()).read_text())
    assert not [p for p in d.get("plugin", []) if "harness/plugin" in str(p)], "legacy specs removed"
    from pathlib import Path as _P
    installed = _P.home() / ".config" / "opencode" / "plugins" / "harness.ts"
    import os as _o
    if _o.environ.get("XDG_CONFIG_HOME"):
        installed = _P(_o.environ["XDG_CONFIG_HOME"]) / "opencode" / "plugins" / "harness.ts"
    assert installed.exists()
    r = subprocess.run([sys.executable, "-m", "harness", "plugin", "install"],
                       capture_output=True, text=True, timeout=60,
                       cwd="/home/jailbreaker20/Projects/harness")
    assert r.returncode == 0
