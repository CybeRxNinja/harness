"""Install idempotence and no default-config mutation."""
import json
from pathlib import Path

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_MOCK", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_plugin_install_does_not_write_opencode_json_or_prune_it(env):
    from harness.cli import install_plugin
    install_plugin()
    assert not (env / "cfg" / "opencode" / "opencode.json").exists()

    install_plugin()
    assert not (env / "cfg" / "opencode" / "opencode.json").exists()


def test_plugin_install_is_rerun_safe_and_supersedes_legacy_files(env):
    from harness.cli import install_plugin
    dest = install_plugin()
    stamp = (dest / "server.ts").stat().st_mtime_ns + (dest / "tui.tsx").stat().st_mtime_ns
    install_plugin()
    stamp2 = (dest / "server.ts").stat().st_mtime_ns + (dest / "tui.tsx").stat().st_mtime_ns
    assert stamp2 == stamp, "identical bytes must not be recopied"
    legacy = (dest.parent / "harness.ts")
    legacy.write_text("// stale single-file install")
    install_plugin()
    assert not legacy.exists()


def test_managed_agents_are_native_files_not_config_json_changes(env):
    from harness.agents import install_managed_agents, agents_dir
    from harness.cli import _opencode_config_path
    install_managed_agents()
    assert (agents_dir() / "harness-orchestrator.md").exists()
    assert not _opencode_config_path().exists(), \
        "native agent files must not be written into opencode.json"
