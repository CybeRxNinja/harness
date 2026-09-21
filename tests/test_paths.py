def test_opencode_paths_honour_xdg(monkeypatch, tmp_path):
    """One definition of opencode's config/plugin layout (the CLI and doctor
    each used to compute it, and disagreed about the installed shape)."""
    from harness.paths import opencode_config_dir, opencode_plugins_dir, plugin_install_dir
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert opencode_config_dir() == tmp_path / "xdg" / "opencode"
    assert opencode_plugins_dir() == tmp_path / "xdg" / "opencode" / "plugins"
    assert plugin_install_dir().name == "harness"
    assert plugin_install_dir().parent.name == "plugins"

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert opencode_plugins_dir() == tmp_path / "home" / ".config" / "opencode" / "plugins"


def test_state_file_reports_without_creating(tmp_path):
    from harness.paths import state_file
    p = state_file(tmp_path, "sessions.db")
    assert p.name == "sessions.db" and p.parent.name == "harness"
    assert not p.parent.exists(), "reporting must not create directories"


def test_state_dir_layout_and_migration(tmp_path):
    from harness.paths import state_dir, migrate_legacy
    r = tmp_path / "proj"
    r.mkdir()
    assert migrate_legacy(r) == "absent"
    # legacy moves once, never merges over existing data
    legacy = r / ".harness"
    legacy.mkdir()
    (legacy / "sessions.db").write_text("old")
    assert migrate_legacy(r) == "moved"
    assert state_dir(r).name == "harness"
    assert state_dir(r).parent.name == ".opencode"
    assert (state_dir(r) / "sessions.db").exists()
    assert not legacy.exists()
    legacy.mkdir()
    (legacy / "other.db").write_text("x")
    assert migrate_legacy(r) == "both"
