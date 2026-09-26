def test_tui_config_merge(tmp_path, monkeypatch):
    import json
    cfgdir = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    from harness.cli import ensure_opencode_config
    dest = tmp_path / "cfg" / "opencode" / "opencode.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"model": "openai/gpt", "provider": {"openai": {}}}))
    out = ensure_opencode_config()
    d = json.loads(open(out).read())
    assert d["model"] == "openai/gpt"  # user value kept, never pinned
    assert "harness" not in d.get("provider", {})  # no relay provider
    assert set(("orchestrator", "ask", "debug", "review")) <= set(d["agent"])
    # specialized subagents merge too: the task tool can only route to agents
    # opencode knows about, and the built-in fallback is `general`
    assert {"explore", "code-reviewer", "test-engineer"} <= set(d["agent"])
    assert d["agent"]["code-reviewer"]["mode"] == "subagent"
    for spec in d["agent"].values():  # agents inherit the user default
        assert "model" not in spec
        # relay ids only ever appeared as a value ("harness/auto-fastest"),
        # so require the quote — a prompt may legitimately name a path like
        # .opencode/harness/tmp/
        assert '"harness/' not in json.dumps(spec)
    # idempotent
    ensure_opencode_config()
    d2 = json.loads(open(out).read())
    assert d2 == d


def test_merge_cleans_legacy_relay(tmp_path, monkeypatch):
    import json
    cfgdir = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    from harness.cli import ensure_opencode_config
    dest = tmp_path / "cfg" / "opencode" / "opencode.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({
        "model": "harness/auto-fastest",
        "provider": {"harness": {"options": {"baseURL": "http://127.0.0.1:8787/v1"}}},
        "agent": {"orchestrator": {"model": "harness/tag:reasoning", "prompt": "mine"}},
    }))
    out = ensure_opencode_config()
    d = json.loads(open(out).read())
    assert "harness" not in d.get("provider", {})
    assert "model" not in d  # harness default dropped (user sets their own)
    assert d["agent"]["orchestrator"].get("prompt") == "mine"  # user keys kept
    assert "model" not in d["agent"]["orchestrator"]  # harness pin dropped


def test_find_opencode(tmp_path, monkeypatch):
    from harness import cli
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\ntrue\n")
    fake.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda *a, **k: str(fake))
    assert cli._find_opencode() == str(fake)


def _tui_text():
    """Read the TUI entrypoint under test.

    HARNESS_TUI_PATH overrides the path so the cold-scan tests can run
    unmodified against a pristine copy (e.g. `git show HEAD:` output staged
    in .opencode/harness/tmp/) for failing-first proof; the product tree is
    never touched.
    """
    import os
    from pathlib import Path
    override = os.environ.get("HARNESS_TUI_PATH")
    if override:
        return Path(override).read_text()
    from harness.cli import _plugin_files
    return Path(_plugin_files()[1]).read_text()


def test_tui_cold_scan_timeout_does_not_latch():
    """A scan whose store syncs time out must NOT latch `scanned`.

    The cold location store answers empty on the first pass and fills on the
    next; latching on that first empty pass froze the panel at "0 skills"
    forever because the 8s poll (`if (full || !scanned)`) never re-ran the
    slow half. The latch therefore requires the per-sync verdict (`slowOk`,
    from the settles INSIDE scanSlow — the outer race maps any settlement to
    true) AND content, and unlatched polls keep re-entering the slow half.
    """
    tui = _tui_text()
    # settle carries a verdict: True when the sync beat the time-box (a
    # rejection counts as settled — it finished, it just failed), False when
    # the time-box won.
    assert "function settle(p: any, ms: number): Promise<boolean>" in tui
    # the verdict is written by scanSlow itself from each store's own settle,
    # reset fresh every pass so a pass that never finishes leaves it false.
    assert "slowOk = synced.every(Boolean)" in tui
    assert "slowOk = false" in tui
    # the latch: syncs landed AND showed something (or bounded tries, covered
    # by test_tui_empty_store_settles_after_bounded_retries) — never an
    # unconditional `scanned = true` on the line after the await.
    assert "if (slowOk && (populated || slowTries >= SLOW_MAX_TRIES)) scanned = true" in tui
    # unlatched polls re-enter the slow half, so the cold first pass gets a
    # second chance on a later poll instead of freezing at zero.
    assert "if (full || !scanned) {" in tui


def test_tui_skills_show_syncing_while_unlatched():
    """While the slow half has not latched, Skills says `syncing…`.

    Before the latch, an empty skill list printed the settled
    "(none seeded at this location)" line — the panel presented a cold store
    as a settled empty one. The settled text is kept for a genuinely empty
    store; it is just deferred until the latch proves the stores settled.
    """
    tui = _tui_text()
    # the latch verdict reaches the view state every load.
    assert "d.scanned = scanned" in tui
    # cold + empty reads as in-flight, not as settled-empty.
    assert 'if (!data.skills.length && !view.scanned) return ["syncing…"]' in tui
    # ...while the settled empty text still exists for a store that proved empty.
    assert 'empty="(none seeded at this location)"' in tui


def test_tui_empty_store_settles_after_bounded_retries():
    """A genuinely empty store still settles: retries stop after N tries.

    Without a bound, a setup with no skills would re-sync the location stores
    on every poll forever. After SLOW_MAX_TRIES passes the latch accepts the
    empty result, the cheap 8s poll skips the slow half again, and a session
    change resets the latch so a new project's cold stores must re-prove.
    """
    tui = _tui_text()
    assert "const SLOW_MAX_TRIES = 5" in tui
    assert "slowTries += 1" in tui
    assert "const populated = data.skills.length > 0 || data.agents.length > 0" in tui
    assert "slowTries >= SLOW_MAX_TRIES" in tui
    # the slow half stays skipped once latched (cheap poll), and a new session
    # drops the latch instead of inheriting it.
    assert "if (full || !scanned) {" in tui
    assert "scanned = false" in tui
    assert "slowTries = 0" in tui
