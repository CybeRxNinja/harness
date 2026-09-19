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
