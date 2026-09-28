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


def _region(tui, start, end):
    """Source slice between two anchors, so a pin can be scoped to one block.

    Same idiom as the inline `tui[tui.index(a):tui.index(b)]` slices, but a
    missing/renamed anchor is a readable failure instead of a ValueError —
    which is the whole point when the pin exists to notice a refactor.
    """
    a, b = tui.find(start), tui.find(end)
    assert 0 <= a < b, f"anchors missing or out of order: {start!r} -> {end!r}"
    return tui[a:b]


def test_tui_cold_scan_timeout_does_not_latch():
    """A scan whose store syncs time out must NOT latch `scanned`.

    The cold location store answers empty on the first pass and fills on the
    next; latching on that first empty pass froze the panel at "0 skills"
    forever because the 8s poll (`if (full || (!scanned && Date.now() >=
    slowNext))`) never re-ran the slow half. The latch therefore requires the
    per-sync verdict (`slowOk`, from the settles INSIDE scanSlow — the outer
    race maps any settlement to true) AND content, and unlatched polls keep
    re-entering the slow half.
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
    # the latch: syncs landed AND showed something (or the retry window ran
    # out, covered by test_tui_empty_store_settles_after_bounded_retries) —
    # never an unconditional `scanned = true` on the line after the await.
    assert "if ((slowOk && populated) || Date.now() >= slowUntil) scanned = true" in tui
    # unlatched polls re-enter the slow half, so the cold first pass gets a
    # second chance on a later poll instead of freezing at zero.
    assert "if (full || (!scanned && Date.now() >= slowNext)) {" in tui


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
    """A genuinely empty store still settles: the retries are time-bounded.

    Without a bound, a setup with no skills would re-sync the location stores
    on every poll forever. The budget is a wall-clock WINDOW (SLOW_WINDOW_MS,
    opened on the first slow pass) rather than a try count, plus a floor
    between two retries (SLOW_RETRY_MS) so the window is not spent at the
    500ms tick rate; after the window passes the latch accepts the empty
    result, the cheap poll skips the slow half again, and a session change
    resets the latch so a new project's cold stores must re-prove.
    """
    tui = _tui_text()
    assert "const SLOW_WINDOW_MS = 30_000" in tui
    assert "const SLOW_RETRY_MS = 2000" in tui
    assert "const populated = data.skills.length > 0 && data.agents.length > 0" in tui
    # the window opens on the first slow pass and is never re-armed, so a
    # genuinely empty setup settles instead of syncing forever
    assert "if (slowUntil === 0) slowUntil = Date.now() + SLOW_WINDOW_MS" in tui
    assert "Date.now() >= slowUntil" in tui
    # the slow half stays skipped once latched (cheap poll), and a new session
    # drops the latch and both clocks instead of inheriting them.
    assert "if (full || (!scanned && Date.now() >= slowNext)) {" in tui
    assert "scanned = false" in tui
    assert "slowUntil = 0" in tui
    assert "slowNext = 0" in tui


def test_tui_partial_population_does_not_latch():
    """One warm store must not latch: agents arrive before skills.

    Pass 1 can see agents (local builtins, instant) while the server-side
    skill seeding has not landed yet — with an `||` predicate that pass
    latched `scanned` and froze Skills at 0 until a manual refresh. The
    early latch requires ALL slow outputs non-empty; a half-populated pass
    leaves the latch down so the poll re-runs the slow half, and the window
    above still settles a genuinely empty store.
    """
    tui = _tui_text()
    assert "const populated = data.skills.length > 0 && data.agents.length > 0" in tui
    assert "data.skills.length > 0 || data.agents.length > 0" not in tui
    assert "if ((slowOk && populated) || Date.now() >= slowUntil) scanned = true" in tui
    assert "if (full || (!scanned && Date.now() >= slowNext)) {" in tui


def test_tui_slow_retry_budget_is_a_time_window_not_a_try_count():
    """The slow-scan budget is wall-clock, because the tick is 500ms.

    A count of 5 retries was 1.7s of retrying at the 500ms refresh cadence,
    so the latch settled on a still-cold location store and the panel froze at
    "0 skills" for the rest of the session. The budget is now a 30s window
    plus a 2s floor between retries, and no count constant survives.
    """
    tui = _tui_text()
    assert "const SLOW_WINDOW_MS = 30_000" in tui
    assert "const SLOW_RETRY_MS = 2000" in tui
    # both clocks are the wall clock, and only the retry floor is re-armed
    assert "slowNext = Date.now() + SLOW_RETRY_MS" in tui
    assert "if (slowUntil === 0) slowUntil = Date.now() + SLOW_WINDOW_MS" in tui
    # the count-based budget is gone for good — a re-added try cap would
    # reintroduce the freeze it replaced
    assert "SLOW_MAX_TRIES" not in tui
    assert "slowTries" not in tui


def test_tui_early_latch_needs_syncs_landed_and_both_lists_populated():
    """Inside the window, the latch needs BOTH verdicts — never either.

    The window is the only alternative way to latch, so a pass that failed its
    store syncs (`slowOk` false) or that read a half-populated store cannot
    latch early; and `populated` stays a conjunction, because agents (local
    builtins) land long before server-seeded skills.
    """
    tui = _tui_text()
    assert "const populated = data.skills.length > 0 && data.agents.length > 0" in tui
    assert "if ((slowOk && populated) || Date.now() >= slowUntil) scanned = true" in tui
    assert "data.skills.length > 0 || data.agents.length > 0" not in tui
    # the latch is scoped to the slow gate, so the message walk that runs on
    # EVERY load can never be what latches it
    gate = _region(tui, "if (full || (!scanned && Date.now() >= slowNext)) {",
                   "        scanModels(data_)")
    assert "scanned = true" in gate, "only the slow pass may latch the panel"


def test_tui_full_pass_bypasses_the_slow_retry_floor():
    """`full` is the caller's escape hatch and short-circuits the floor.

    A session change, the first load and `/harness-refresh` all pass `full`;
    the retry floor paces background retries only, so a manual refresh landing
    inside the 2s floor must still re-run the slow half. Hence `full` is the
    first disjunct and the clock is conjuncted with `!scanned`.
    """
    import re
    tui = _tui_text()
    m = re.search(r"if \(full \|\| ([^{]+)\) \{", tui)
    assert m, "the slow gate must still short-circuit on `full`"
    assert m.group(1).strip() == "(!scanned && Date.now() >= slowNext)", m.group(1)
    # the floor is never an independent branch of the gate
    assert "if (full || Date.now() >= slowNext)" not in tui


def test_tui_session_change_resets_both_slow_budget_fields():
    """A new session must re-prove: latch AND both clocks reset together.

    A session change can mean a new project directory with cold stores again.
    Resetting only `scanned` would inherit the old session's window — possibly
    already expired, which latches the very first cold pass.
    """
    tui = _tui_text()
    body = _region(tui, "const noteSession = (sid: unknown) =>", "/** Every slot render")
    assert "      scanned = false\n      slowUntil = 0\n      slowNext = 0\n" in body
    # the fast path must not treat the new session's first pass as a repeat
    assert 'seenSig = ""' in body


def test_tui_header_separator_is_a_literal_middot_not_the_box_gap():
    """The header spaces itself with CHARACTERS: opencode drops box `gap`.

    Live on 2.0.18 the row rendered `harnessses_f29…` — the host did not space
    the header's texts, so the brand carries its own trailing space and the
    tail emits the `·` as a literal, which reads the same on a host that
    honours `gap` and one that drops it. A bare text node would be trimmed
    back to `harness<tail>`, so all three are explicit expression containers.
    """
    tui = _tui_text()
    header = _region(tui, 'flexDirection="row" minWidth={0}>', 'id="window"')
    assert "gap=" not in header, "the box gap is not what spaces the header"
    assert '{"harness "}' in header, "the brand carries the space"
    assert 'return tail ? `· ${tail}` : ""' in header, "the tail carries the `·`"
    assert '{" ⋯"}' in header, "the in-flight glyph carries its own space"
    assert "\n                    harness\n" not in header, (
        "a bare brand text node renders unspaced"
    )


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


def test_tui_fast_tick_constants_keep_the_net_cadence():
    """FAST_MS/NET_EVERY are one interval re-ticked, not a second timer.

    The panel learned about a finished turn from the 8s poll alone, so its
    Context numbers trailed opencode's own by up to 8s. The refresh is now a
    500ms change probe with an unconditional pass every NET_EVERY ticks. The
    net cadence is DERIVED from POLL_MS/FAST_MS, so the old 8s safety net
    survives the retune exactly; and the poll interval is re-ticked rather
    than added to, keeping the net timer count at one.
    """
    import re

    tui = _tui_text()
    assert "const POLL_MS = 8000" in tui
    assert "const FAST_MS = 500" in tui
    # derived, not a second hardcoded number that can drift from POLL_MS
    assert "const NET_EVERY = Math.max(1, Math.round(POLL_MS / FAST_MS))" in tui
    poll = int(re.search(r"const POLL_MS = (\d+)", tui).group(1))
    fast = int(re.search(r"const FAST_MS = (\d+)", tui).group(1))
    net = max(1, round(poll / fast))
    assert net == 16, f"{poll}ms/{fast}ms must be a whole number of fast ticks per net pass"
    assert net * fast == poll, "the net pass must land ON the old cadence, not near it"
    # the poll interval is re-ticked, not replaced by an extra one
    assert "}, FAST_MS)" in tui


def test_tui_probe_reads_both_db_and_wal_stamps():
    """The probe's DB stamp covers the file AND its -wal sidecar.

    SQLite in WAL mode keeps most writes in `sessions.db-wal` until a
    checkpoint, so stat'ing the base file alone misses exactly the writes the
    panel cares about (a todo/worker/wait/fact row) and the fast path would
    then skip the state read for up to a full net pass. The stamp is also
    mtime+size — either half alone can miss a same-mtime rewrite — and the
    probe reports it as an explicit `db` change so the pass can skip the
    single most expensive step when the disk has not moved.
    """
    tui = _tui_text()
    stamp = _region(tui, "const dbStamp = (path: string): string => {", "/** Last project-DB stamp")
    assert "st = fs?.statSync?.(path)" in stamp
    assert "`${st.mtimeMs}:${st.size}`" in stamp, "mtime AND size"
    assert 'return "-"' in stamp, "an unreadable path is a sentinel, never a throw out of probe()"
    probe = _region(tui, "const probe = (): Rec => {", "const readProjectState")
    assert "const path = stateDb(dir)" in probe
    assert 'const stamp = `${dbStamp(path)}/${dbStamp(`${path}-wal`)}`' in probe, (
        "the -wal sidecar is part of the fingerprint"
    )
    assert "db = stamp !== dbSeen" in probe and "dbSeen = stamp" in probe, (
        "'the disk moved' is reported, not just folded into the signature"
    )


def test_tui_probe_is_a_reverse_index_scan_of_o1_fields():
    """probe() is O(1) local reads: a reverse index scan, never a message walk.

    A 400-message session walked end to end on every tick is the exact cost
    the gate exists to avoid, so the last assistant row is found by index from
    the end and the scan breaks there (opencode's own header rule) — the rest
    of the session is never touched. The rest of the signature is count +
    tokens/cost/model/agent/family size: what the Window/Models numbers, the
    Tokens row and the footer chip read. No store sync, no SQLite open.
    """
    tui = _tui_text()
    probe = _region(tui, "const probe = (): Rec => {", "const readProjectState")
    # the message array: indexed backwards, stopping at the last assistant row
    assert "const msgs = asArray(d?.session?.message?.list?.(sessionID))" in probe
    assert "n = msgs.length" in probe, "the count is a length read, not a tally"
    assert "for (let i = n - 1; i >= 0; i--) {" in probe, "reverse index scan"
    assert "const m: Rec = msgs[i]" in probe
    assert 'if (String(m?.role ?? m?.type) !== "assistant") continue' in probe
    assert "break" in probe, "the scan stops at the last assistant row"
    for bad in ("for (const m of msgs", "msgs.map(", "[...msgs]", "msgs.forEach("):
        assert bad not in probe, f"probe must not iterate the whole store: {bad}"
    # the session's own numbers, each an O(1) read
    assert "const s = d?.session?.get?.(sessionID) ?? null" in probe
    for field in (
        "totalTokens(s?.tokens)",
        "Number(s?.cost ?? 0) || 0",
        "modelId(s?.model)",
        'String(s?.agent ?? "")',
        "familyIds(d, sessionID).length",
    ):
        assert field in probe, field
    # a probe that opened the DB or hit the network is not a cheap probe
    for bad in ("readProjectState", "db.query", "?.sync?.("):
        assert bad not in probe, f"probe must stay local: {bad}"
    assert "return { sig: sig.join(\"|\"), db }" in probe


def test_tui_fast_path_skips_the_pass_when_the_signature_is_unchanged():
    """An unchanged fingerprint returns before any pass is started.

    The point of the fast tick is that idle costs the probe and nothing else:
    no message walk, no store sync, no SQLite open, no repaint. The stored
    signature starts empty (first tick always acts) and is dropped on a session
    change, so a new session's first pass is never mistaken for a repeat of
    the old one's.
    """
    tui = _tui_text()
    assert 'let seenSig = ""' in tui, "no fingerprint to match on the first tick"
    fast = _region(tui, "if (netTick % NET_EVERY !== 0) {", "void load()\n      }, FAST_MS)")
    assert "p = probe()" in fast
    assert "p = null" in fast, "a throwing probe is a no-change tick, never a crash"
    assert "if (!p?.sig || p.sig === seenSig) return" in fast, (
        "the early return IS the gate: unchanged => no pass at all"
    )
    assert fast.index("if (!p?.sig || p.sig === seenSig) return") < fast.index("load("), (
        "the return precedes the pass, it is not a post-hoc skip"
    )
    # the fast pass is the local-only one: no store sync, DB read only if moved
    assert "void load(false, { sync: false, db: p.db })" in fast
    note = _region(tui, "const noteSession = (sid: unknown) => {", "/** Every slot render goes through here")
    assert 'seenSig = ""' in note, "a new session invalidates the fingerprint"


def test_tui_load_reports_whether_a_pass_ran_and_then_commits_the_signature():
    """`load` returns a verdict, and the fingerprint commits only on it.

    A pass that was queued behind the loading mutex did not run: reporting
    true there would commit a signature the panel never actually rendered, and
    a change seen while that pass was in flight would be swallowed until the
    next net pass. So the guard returns false, the accepting end returns true,
    and the tick's `.then` is the only place the signature is stored.
    """
    tui = _tui_text()
    sig = "const load = async (full = false, opts: Rec = {}): Promise<boolean> => {"
    assert sig in tui
    guard = _region(tui, sig, "loading = true")
    assert "pendingFull = pendingFull || full" in guard, "a full request is still queued"
    assert "return false" in guard, "a queued pass is not a pass"
    body = _region(tui, sig, "const noteSession")
    import re

    verdicts = re.findall(r"return (?:true|false)", body)
    assert verdicts == ["return false", "return true"], (
        f"exactly two verdicts, in order: {verdicts}"
    )
    # the fast path's local-only knobs
    assert "if (opts.db !== false) {" in body, "a still-quiet DB skips the SQLite open"
    assert "if (opts.sync !== false) {" in body, "store syncs belong to the net pass"
    assert "void settle(data_?.session?.sync?.(sessionID, { children: true }), SCAN_MS)" in body
    fast = _region(tui, "if (netTick % NET_EVERY !== 0) {", "void load()\n      }, FAST_MS)")
    assert ".then((ran) => {" in fast
    assert "if (ran) seenSig = p!.sig" in fast, "commit only when a pass really ran"
    assert "seenSig = p!.sig" in fast and fast.count("seenSig =") == 1, (
        "the tick's callback is the only writer of the fast-path signature"
    )
    assert "seenSig = p!.sig" not in _region(tui, sig, "const noteSession"), (
        "load() itself must never commit the caller's fingerprint"
    )


def test_tui_net_pass_stays_unconditional_on_its_tick():
    """Every NET_EVERY-th tick is still the old unconditional pass.

    The probe deliberately does not read row bodies, so an edit to an OLDER
    assistant row (a retried step's cost) slips past it; the net pass is what
    re-proves a cold location store, refreshes the time-derived rows and
    reconciles that blind spot. It must stay a bare `load()` — no probe gate,
    no `{sync:false}` — or the panel stops self-healing.
    """
    tui = _tui_text()
    assert "let netTick = 0" in tui
    assert "netTick += 1" in tui
    assert "if (netTick % NET_EVERY !== 0) {" in tui, "the fast branch is the non-net tick"
    # byte-exact: the net branch is `void load()` and nothing else, then the
    # interval closes on FAST_MS
    assert "        void load()\n      }, FAST_MS)" in tui, (
        "the net pass must be the unconditional load() it always was"
    )
    fast = _region(tui, "if (netTick % NET_EVERY !== 0) {", "void load()\n      }, FAST_MS)")
    assert fast.count("void load(") == 1 and "void load(true)" not in fast, (
        "the fast branch never claims a full pass"
    )
    assert "if (lastRender === 0 || Date.now() - lastRender > POLL_MS * 4) return" in tui, (
        "the off-screen stand-down still gates the whole tick"
    )


def test_tui_settle_releases_its_box_timer():
    """settle() clears the race's losing 5s timer.

    A race leaves its loser pending for the whole box, and one pass boxes four
    store syncs — so without the release the panel carried a handful of 5s
    timers between passes, each firing into a pass that already finished. The
    handle is held in a `box` var and dropped in `.finally`, which covers
    both racers (the promise won, or the time-box did).
    """
    tui = _tui_text()
    body = _region(tui, "function settle(p: any, ms: number): Promise<boolean> {", "function familyIds")
    assert "let box: any = null" in body, "the box handle is kept so the loser can be released"
    assert "box = setTimeout(() => resolve(false), ms)" in body
    assert ").finally(() => {" in body, "a finished race must not leave a timer pending"
    assert "if (box !== null) clearTimeout(box)" in body
    assert body.count("clearTimeout") == 1, "one release path, in the finally"


def test_tui_dispose_releases_every_timer_it_started():
    """Dispose releases the poll, the family nudge and the Waits tick.

    The fast retune added a second kind of scheduled work (a pending family
    nudge firing a pass after release), so the cleanup has to cover it: a
    one-shot that outlives the plugin calls back into a torn-down ctx.
    """
    tui = _tui_text()
    # the release callback is the last thing setup() returns: the file's single
    # `return () => {` runs to EOF, so this slice is exactly the cleanup
    cleanup = tui[tui.index("return () => {"):]
    assert "if (timer) clearInterval(timer)" in cleanup, "the (re-ticked) poll interval"
    assert "if (famTimer !== null) {" in cleanup
    assert "clearTimeout(famTimer)" in cleanup
    assert "famTimer = null" in cleanup, "released, not just fired at"
    assert "stopWaitTick()" in tui, "the Waits tick stands down with the plugin"
    # the nudge is a one-shot that the fast path can leave pending
    assert "famTimer = setTimeout(() => {" in tui
    assert "if (famTimer === null) {" in tui, "never stacked, so one clear is enough"


def test_tui_fast_path_ride_the_existing_poll_timer():
    """The refresh added no timer: the probe rides the poll that already ran.

    Two intervals total, before and after: the refresh poll (now re-ticked at
    FAST_MS) and the Waits countdown tick. A third `setInterval` would mean the
    fast path bought its own timer instead of reusing the one in place.
    """
    tui = _tui_text()
    assert tui.count("setInterval(") == 2, (
        f"expected exactly 2 intervals (poll + waits tick), found {tui.count('setInterval(')}"
    )
    assert "timer = setInterval(() => {" in tui
    assert "tickTimer = setInterval(() => {" in tui
    assert "}, FAST_MS)" in tui and "}, 1000)" in tui
    # both the probe and the fast pass live inside that one poll callback
    assert tui.index("p = probe()") < tui.index("}, FAST_MS)") < tui.index(
        "tickTimer = setInterval(() => {"
    )
