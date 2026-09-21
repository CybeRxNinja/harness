"""doctor reported a healthy install as missing: it checked the legacy
single-file plugin path and the pre-migration .harness DB. Both are locked here.
"""
import json, os, subprocess, sys


def test_plugin_status_reads_the_directory_layout(tmp_path):
    from harness.doctor import plugin_status
    plug = tmp_path / "plugins"

    msg, ok = plugin_status(plug)
    assert not ok and "harness plugin install" in msg

    d = plug / "harness"
    d.mkdir(parents=True)
    (d / "server.ts").write_text("// server")
    msg, ok = plugin_status(plug)
    assert not ok, "server-only (no tui.tsx) never shows in the TUI plugin list"
    assert "tui.tsx" in msg, msg

    (d / "tui.tsx").write_text("// tui")
    msg, ok = plugin_status(plug)
    assert ok and "server + tui" in msg, msg

    # the old bare harness.ts is superseded, not accepted
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    (plug / "harness.ts").write_text("// legacy")
    msg, ok = plugin_status(plug)
    assert not ok and "legacy" in msg, msg


def test_doctor_reports_an_installed_plugin_as_ok(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    d = cfg / "opencode" / "plugins" / "harness"
    d.mkdir(parents=True)
    (d / "server.ts").write_text("// s")
    (d / "tui.tsx").write_text("// t")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))

    from harness.doctor import run
    out = run(tmp_path)
    assert "server + tui" in out["checks"]["plugin"]
    assert str(d) in out["checks"]["plugin"]


def test_doctor_measures_the_current_db_location(tmp_path, monkeypatch):
    from harness.paths import state_file
    db = state_file(tmp_path, "sessions.db")
    db.parent.mkdir(parents=True)
    db.write_bytes(b"x" * 60_000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    from harness.doctor import run
    assert run(tmp_path)["checks"]["db_mb"] > 0


def test_doctor_still_counts_a_legacy_db(tmp_path, monkeypatch):
    legacy = tmp_path / ".harness"
    legacy.mkdir()
    (legacy / "sessions.db").write_bytes(b"y" * 60_000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    from harness.doctor import run
    assert run(tmp_path)["checks"]["db_mb"] > 0


def test_doctor_does_not_create_state_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness.doctor import run
    run(tmp_path)
    assert not (tmp_path / ".opencode").exists(), "a report must not mutate the project"


def test_module_entrypoint_and_doctor_subcommand(tmp_path):
    env = dict(os.environ, HARNESS_MOCK="1", XDG_CONFIG_HOME=str(tmp_path / "cfg"))

    r = subprocess.run([sys.executable, "-m", "harness", "--root", str(tmp_path), "doctor"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["checks"]["python"]

    bare = subprocess.run([sys.executable, "-m", "harness"], capture_output=True, text=True,
                          env=env, timeout=120)
    assert bare.returncode == 2 and "usage" in (bare.stdout + bare.stderr).lower()


def test_verbose_doctor_adds_budget_and_category_details(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness.doctor import run
    out = run(tmp_path, verbose=True)
    assert "budgets" in out["checks"] and isinstance(out["checks"]["categories"], list)
    assert "budgets" not in run(tmp_path)["checks"]
