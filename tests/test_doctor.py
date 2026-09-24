"""doctor reported a healthy install as missing: it checked the legacy
single-file plugin path and the pre-migration .harness DB. Both are locked here.
"""
import json, os, subprocess, sys
from pathlib import Path


def _shipped() -> Path:
    return Path(__file__).resolve().parent.parent / "harness" / "plugin"


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

    (d / "server.ts").write_bytes((_shipped() / "harness.ts").read_bytes())
    (d / "tui.tsx").write_bytes((_shipped() / "tui.tsx").read_bytes())
    msg, ok = plugin_status(plug)
    assert ok and "server + tui" in msg, msg

    # the old bare harness.ts is superseded, not accepted
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    (plug / "harness.ts").write_text("// legacy")
    msg, ok = plugin_status(plug)
    assert not ok and "legacy" in msg, msg


def test_plugin_status_flags_a_stale_install(tmp_path):
    """An installed dir that merely exists can be an old build: that is how
    the old Window formula and the general-only subagents survived an
    already-'installed' system."""
    from harness.doctor import plugin_status
    plug = tmp_path / "plugins"
    d = plug / "harness"
    d.mkdir(parents=True)
    (d / "server.ts").write_bytes((_shipped() / "harness.ts").read_bytes())
    # the pre-fix tui.tsx: lifetime totals over the context limit
    (d / "tui.tsx").write_text(
        "out.push(`${numfmt(total())} / ${numfmt(data.contextLimit)} of window`)")

    msg, ok = plugin_status(plug)
    assert not ok and "STALE" in msg and "tui.tsx" in msg, msg
    assert "harness plugin install" in msg, msg


def test_agents_status_catches_a_config_merged_by_an_older_harness(tmp_path,
                                                                    monkeypatch):
    from harness.doctor import agents_status, run
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    msg, ok = agents_status()
    assert not ok and "not found" in msg, msg

    dest = tmp_path / "cfg" / "opencode" / "opencode.json"
    dest.parent.mkdir(parents=True)
    # an old merge: primaries only, no mode:subagent specialists
    dest.write_text(json.dumps({"agent": {"orchestrator": {}, "ask": {}}}))
    msg, ok = agents_status()
    assert not ok and "explore" in msg and "code-reviewer" in msg, msg
    assert "harness plugin install" in msg, msg

    from harness.cli import _bundled_opencode_json
    dest.write_text(json.dumps(
        {"agent": {n: {} for n in _bundled_opencode_json()["agent"]}}))
    msg, ok = agents_status()
    assert ok and "harness agents" in msg, msg
    assert run(tmp_path)["checks"]["agents"] == msg


def test_doctor_reports_an_installed_plugin_as_ok(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    d = cfg / "opencode" / "plugins" / "harness"
    d.mkdir(parents=True)
    (d / "server.ts").write_bytes((_shipped() / "harness.ts").read_bytes())
    (d / "tui.tsx").write_bytes((_shipped() / "tui.tsx").read_bytes())
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))

    from harness.doctor import run
    out = run(tmp_path)
    assert "server + tui" in out["checks"]["plugin"]
    assert str(d) in out["checks"]["plugin"]
    assert "STALE" not in out["checks"]["plugin"]


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
