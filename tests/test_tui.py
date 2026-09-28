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
    assert "const populated = data.skills.length > 0 && data.agents.length > 0" in tui
    assert "slowTries >= SLOW_MAX_TRIES" in tui
    # the slow half stays skipped once latched (cheap poll), and a new session
    # drops the latch instead of inheriting it.
    assert "if (full || !scanned) {" in tui
    assert "scanned = false" in tui
    assert "slowTries = 0" in tui


def test_tui_partial_population_does_not_latch():
    """One warm store must not latch: agents arrive before skills.

    Pass 1 can see agents (local builtins, instant) while the server-side
    skill seeding has not landed yet — with an `||` predicate that pass
    latched `scanned` and froze Skills at 0 until a manual refresh. The
    early latch requires ALL slow outputs non-empty; a half-populated pass
    leaves the latch down so the 8s poll re-runs the slow half, and the
    bound above still settles a genuinely empty store.
    """
    tui = _tui_text()
    assert "const populated = data.skills.length > 0 && data.agents.length > 0" in tui
    assert "data.skills.length > 0 || data.agents.length > 0" not in tui
    assert "if (slowOk && (populated || slowTries >= SLOW_MAX_TRIES)) scanned = true" in tui
    assert "if (full || !scanned) {" in tui


def test_tui_waits_rows_carry_live_icon():
    """Waits rows open with the live mark in a stable icon column.

    Every todo/worker row already opened with its status glyph; Waits rows
    were bare text (`label · 3m12s left`). The polish gives each row the
    live `◐` mark like the marks above, so the column stays stable.
    Value strings are byte-identical — colour/icon only, no behavior change.
    """
    tui = _tui_text()
    # stable icon column, never bare text.
    assert "out.waits = wrows.map((r) => `◐ " in tui
    assert "◐ ${wcut(r?.label, 15)}" in tui
    # value strings unchanged by the polish.
    assert "1 waiting" in tui
    assert "(no waits pending)" in tui


def test_tui_value_emphasis_wiring():
    """Value-vs-muted hierarchy: values pop, calm states recede to muted.

    The Row owns the hierarchy (`valueFg`/`tone` props, colour only —
    strings unchanged); each row wires its pressure/occupancy into it, and
    empty/calm states stay dimmed instead of shouting in the label colour.
    """
    tui = _tui_text()
    # the props exist and the Row falls back to muted without them.
    assert "valueFg?: () => string" in tui
    assert "tone?: (line: string) => string" in tui
    assert "props.valueFg ? props.valueFg() : th.muted" in tui
    assert "props.tone ? props.tone(l) : th.muted" in tui
    # rows wire it: at least one per-row valueFg and tone.
    assert "valueFg={()" in tui
    assert "tone={(l)" in tui
    assert "tone={() => th.base}" in tui
    # muted calm states: collapsed footer, empty window/tokens, idle pool,
    # unseeded stores — colour only, same strings as before.
    assert "openCount() ? th.base : th.muted" in tui
    assert "return pct() >= 80 ? th.warn : th.base" in tui
    assert "return data.tokens ? th.base : th.muted" in tui
    assert "return view.workersActive ? th.base : th.muted" in tui
    assert "return (view.skills ?? 0) > 0 ? th.base : th.muted" in tui
    # value strings byte-identical under the new colours.
    assert "click a row" in tui
    assert "${openCount()} expanded" in tui


def test_tui_user_names_word_boundary_truncation():
    """User-facing names cut at a word boundary (`wcut`), never mid-word.

    The Memory rows showed `progress [s_5526202c]: [mock:…` — cut at 30
    cols mid-token. Todo/Workers/Waits names and the 32-col detail rows use
    the same word-boundary cut; the Memory humanizer already did.
    """
    tui = _tui_text()
    assert "function wcut(value: unknown, max: number)" in tui
    # Todo text, Worker name, Wait label — the user-facing names.
    assert "wcut(r?.text, 30)" in tui
    assert "wcut(r?.name, 15)" in tui
    assert "wcut(r?.label, 15)" in tui
    # detail rows (covers Memory display) + the fact humanizer itself.
    assert "wcut(l, 32)" in tui
    assert "wcut(m[3], 30)" in tui


def test_tui_no_bold_prop():
    """No `bold` prop: the host never verified one, so emphasis is colour-only."""
    tui = _tui_text()
    assert "bold" not in tui.lower()


def test_tui_waits_tick_timer_self_clearing_and_cleanup():
    """The Waits countdown ticks between host repaints without a DB read.

    The 8s poll is what refreshes the rows, so a `28s left` label sat stale
    until the next pass. A 1s timer only bumps `rev` — the detail lines
    recompute left() from the stored deadlines on every render (see
    liveWaits) — and it runs only while an unexpired wait exists: it starts
    on load when rows pend, clears itself past the last deadline (with one
    cheap reconciling load), stands down off-screen like the poll, and is
    released with the plugin instead of dangling.
    """
    tui = _tui_text()
    assert "const waitAlive = (): boolean =>" in tui, "only unexpired rows keep the tick"
    assert "tickTimer = setInterval(() => {" in tui and "}, 1000)" in tui, "1s tick, same primitive as the poll"
    tick = tui[tui.index("tickTimer = setInterval(() => {"):tui.index("const toggleSidebar")]
    assert "if (!waitAlive()) {" in tick and "stopWaitTick()" in tick, "self-clear past the last deadline"
    assert "void load()" in tick, "one reconciling pass on drain, not a stale row until POLL_MS"
    assert "if (lastRender === 0 || Date.now() - lastRender > POLL_MS * 4) return" in tick, (
        "off-screen stand-down, like the poll"
    )
    assert "d.rev = Number(d.rev ?? 0) + 1" in tick, "the tick only bumps rev — no DB read"
    assert "db.query" not in tick and "readProjectState" not in tick, "no store touch per tick"
    assert "clearInterval(tickTimer)" in tui, "the timer is really released"
    assert "ensureWaitTick()" in tui, "each load reconciles the tick with the rows just read"
    cleanup = tui[tui.index("return () => {"):]
    assert "clearInterval(timer)" in cleanup, "the poll cleanup still releases the poll"
    assert "stopWaitTick()" in cleanup, "no dangling Waits timer past plugin release"


def test_tui_waits_bar_reuses_window_cells_with_legacy_fallback():
    """Wait rows draw their elapsed/total share through the Window bar().

    waitPct is 0% at record time, 100% at deadline, and -1 when the row
    carries no usable total (pre-migration rows, drifted table); waitTail
    then falls back to the byte-identical old ` left` text instead of a bar.
    """
    tui = _tui_text()
    assert "function bar(pct: number, cells = 8): string" in tui, "the Window usage-bar precedent"
    assert "function waitPct(row: Rec): number" in tui
    assert "function waitTail(row: Rec): string" in tui
    pct = tui[tui.index("function waitPct"):tui.index("function waitTail")]
    assert "const total = Number(row?.total ?? row?.total_ms ?? 0)" in pct
    assert "const elapsed = total - Math.max(0, deadline - Date.now())" in pct
    assert "return -1" in pct, "no usable total is a sentinel, never a 0% bar"
    assert "return ` ${bar(pct, 8)}`" in tui, "8 cells, reusing bar() — not a second bar implementation"
    assert 'if (pct < 0) return " left"' in tui, "legacy text form byte-identical"


def test_tui_waits_total_ms_read_with_legacy_fallback():
    """The state-DB read takes total_ms when the column exists, else degrades.

    Fresh/migrated DBs answer the total_ms column; a pre-migration DB throws
    on the unknown column and the fallback reads label+deadline — those rows
    keep the current text form via waitTail (above), never a broken row.
    """
    tui = _tui_text()
    assert "SELECT label, deadline, total_ms AS total FROM waits ORDER BY deadline LIMIT ?" in tui
    assert "SELECT label, deadline FROM waits ORDER BY deadline LIMIT ?" in tui, "pre-total fallback"
    assert "total: Number((r as Rec)?.total ?? 0) || 0" in tui, "missing totals normalize to 0 (the waitTail gate)"
    assert "out.waitRows = wrows.map((r) => ({" in tui, "stored rows feed the render-time recompute"
    assert "out.waits = wrows.map((r) => `◐ ${wcut(r?.label, 15)} · ${left(r?.deadline)}${waitTail(r)}`)" in tui


def test_tui_waits_live_recompute_wiring():
    """Waits detail lines recompute at render time; load-time rows stay home.

    The 1s tick is a bare rev bump, so the lines must re-derive left() from
    the stored deadlines on every render (liveWaits) — while the load-time
    rows remain the count source and the drifted-shape fallback, the signature
    stays tick-free so a manual collapse survives the countdown, and the load
    wires the stored rows through.
    """
    tui = _tui_text()
    assert "const liveWaits = (): string[] => {" in tui
    body = tui[tui.index("const liveWaits"):tui.index("const load = async")]
    assert "if (!rows) return data.waits" in body, "drifted shape still renders something"
    assert ".filter((r: Rec) => Number(r?.deadline ?? 0) > Date.now() - WAIT_GRACE_MS)" in body
    assert "`◐ ${wcut(r?.label, 15)} · ${left(r?.deadline)}${waitTail(r)}`" in body, (
        "same shape as the load-time rows"
    )
    assert "data.waitRows = state.waitRows" in tui, "the load wires the stored rows through"
    assert "return liveWaits()" in tui, "the Waits Row renders the recompute, not the snapshot"
    assert 'out.waitSig = wrows.map((r) => `${String(r?.label ?? "")}~${Number(r?.deadline ?? 0)}`)' in tui, (
        "stable identity (label + deadline), never the ticking countdown"
    )
