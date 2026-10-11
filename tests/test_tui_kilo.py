"""Kilo side-panel grammar + Window tok/s rate: failing-first pins for the tui.tsx rework.

The working-tree change rebuilt the sidebar rows (justified label/value
pairs split by splitRow, no LABEL_W column) and measures the Window rate as
opencode's own tok/s (lastTurn/turnRate/asMs feeding data.currentRate).
The base check, test_kilo_pins_hold_in_committed_head, holds the rework's
pins against the committed `git show HEAD:harness/plugin/tui.tsx` — the
rework landed in the base, so the new pins must be present and the removed
ones absent, and a revert of those rows goes red.

Nodes whose subject later landed as a commit keep their pins but hold them
the other way round — present in HEAD, removed patterns absent — so they guard
against a REGRESSION of that commit. That is the case for the perf work of
63411e3 (test_probe_locality_comment_and_recoverable_latch's latch half and
test_family_fanout_capped_and_short_model_gone). Code still uncommitted in the
working tree (the Next-step row) keeps the honest failing-first direction:
absent from HEAD, present in the live file.
"""

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Added by the rework: absent from HEAD, must be present live.
NEW_PINS = [
    "function splitRow(l: string): [string, string] {",
    "gap={1}",
    'justifyContent="space-between"',
    "paddingLeft={0}",
    "function lastTurn(msgs: Rec[]): Rec[] {",
    "function turnRate(turn: Rec[]): string {",
    "function asMs(v: unknown): number {",
    "data.currentRate",
]

# Removed by the rework: present in HEAD, must be absent live.
REMOVED_PINS = [
    "LABEL_W",
    "padEnd(LABEL_W)",
    "cut(name, 18).padEnd(18)",
]


def _tui_text():
    """Live TUI entrypoint, HARNESS_TUI_PATH-aware (same convention as
    test_tui.py, so these pins can run against any staged copy)."""
    override = os.environ.get("HARNESS_TUI_PATH")
    if override:
        return Path(override).read_text()
    from harness.cli import _plugin_files
    return Path(_plugin_files()[1]).read_text()


def _pristine_text():
    """HEAD's tui.tsx, materialized at runtime via git (read-only, no working
    tree touch, no staged file for conftest's scratch reap to eat)."""
    r = subprocess.run(
        ["git", "show", "HEAD:harness/plugin/tui.tsx"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0, r.stderr[-500:]
    return r.stdout


def test_kilo_pins_hold_in_committed_head():
    """Base check: the rework is committed at HEAD — every NEW pin is present
    there and every REMOVED pin is gone. The mirror of the original
    failing-first proof: that one ran while the rework was still uncommitted
    (pins absent from HEAD) and inverted the moment the change landed, so it
    now guards the opposite way — a revert of the Kilo rows to HEAD~ fails
    here first.
    """
    pristine = _pristine_text()
    for pin in NEW_PINS:
        assert pin in pristine, f"new pin missing from HEAD: {pin}"
    for pin in REMOVED_PINS:
        assert pin not in pristine, f"removed pin still in HEAD: {pin}"


def test_kilo_row_grammar_live():
    """Sidebar rows use the Kilo grammar: split + justified, no label column."""
    tui = _tui_text()
    assert "function splitRow(l: string): [string, string] {" in tui
    assert "gap={1}" in tui
    assert tui.count('justifyContent="space-between"') >= 2
    assert "paddingLeft={0}" in tui
    assert "LABEL_W" not in tui
    assert "padEnd(LABEL_W)" not in tui
    assert "cut(name, 18).padEnd(18)" not in tui


def test_window_rate_helpers_live():
    """Window rate is opencode's tok/s: lastTurn/turnRate/asMs -> currentRate."""
    tui = _tui_text()
    assert "function lastTurn(msgs: Rec[]): Rec[] {" in tui
    assert "function turnRate(turn: Rec[]): string {" in tui
    assert "function asMs(v: unknown): number {" in tui
    assert "data.currentRate" in tui


def _region(tui, start, end):
    """Source slice between two anchors, so a pin can be scoped to one block.

    Same idiom as test_tui.py's `_region`, which the probe-locality half of
    the node below mirrors on purpose: a renamed anchor is a readable failure,
    not a ValueError that reads as an unrelated refactor.
    """
    a, b = tui.find(start), tui.find(end)
    assert 0 <= a < b, f"anchors missing or out of order: {start!r} -> {end!r}"
    return tui[a:b]


def test_probe_locality_comment_and_recoverable_latch():
    """Two audit items, one node: the probe's locality and the settle latch.

    (a) The `heldCounts` doc block sits between the probe and the project-state
    reader, so the slice test_tui.py takes of that region picked up the
    identifier `readProjectState` from it and tripped the probe's own locality
    guard. The comment was wrong, not the guard — the guard is what keeps a
    cheap tick from opening a DB. The block now names the reader without the
    identifier, so the region stays free of every token a local probe must not
    open. Commit 63411e3 shipped the reworded block, so the teeth of this pin
    are proven in-memory below (restore the wording, watch it leak).

    (b) The latch closed on whatever the section read at the `slowUntil`
    deadline, so skills seeded AFTER it stayed invisible until a manual
    `/harness-refresh` (the "Skills 0 installed" with skills on disk class). A
    later load that still finds the latch over an empty section now re-opens
    the window once: the same time-boxed slow half re-runs, no new host call,
    and LATE_REARMS is what keeps it from being a loop.

    (b) shipped in 63411e3, so its pins are in HEAD: the failing-first proof
    ran while the re-arm was uncommitted (pins absent from HEAD) and inverted
    when the perf commit landed, exactly as the Kilo rework's did — it now
    guards the opposite way, against reverting the recoverable latch.
    """
    tui = _tui_text()

    # (a) the probe -> reader slice carries no token a local probe may not open
    probe = _region(tui, "const probe = (): Rec => {", "const readProjectState")
    for bad in ("readProjectState", "db.query", "?.sync?.("):
        assert bad not in probe, f"probe must stay local: {bad}"
    # the doc block is still there, reworded — not deleted to dodge the guard
    assert "let heldCounts: { facts: number; workersActive: number } | null = null" in probe
    assert "project-state reader is module-level too" in probe
    # ...and the pin has teeth: the pre-fix wording puts the identifier back
    regressed = tui.replace(
        "Module scope because the\n     * project-state reader is module-level too",
        "Module scope because\n     * readProjectState is module-level",
    )
    assert regressed != tui, "the reworded sentence this pins is still there"
    assert "readProjectState" in _region(regressed, "const probe = (): Rec => {", "const readProjectState"), (
        "with the old wording the region leaks the identifier again"
    )

    # (b) the section's emptiness is read BEFORE the pass decides, so a signal
    # that lands after the deadline has something to contradict
    assert "const stillEmpty = data.skills.length === 0 && data.agents.length === 0" in tui
    assert tui.index("const stillEmpty =") < tui.index("if (full || (!scanned"), (
        "the emptiness read must precede the slow gate it arms"
    )

    gate = _region(tui, "if (full || (!scanned && Date.now() >= slowNext)) {",
                   "        scanModels(data_)")
    # the re-arm itself: un-latch, re-open the window, drop the retry floor
    assert "} else if (scanned && stillEmpty && slowReopens < LATE_REARMS) {" in gate
    assert ("          slowReopens += 1\n          scanned = false\n"
            "          slowUntil = Date.now() + SLOW_WINDOW_MS\n"
            "          slowNext = 0\n") in gate, "the re-arm re-opens the window, it does not rescan"
    # bounded by a budget, and the budget rides the session-change reset
    assert "const LATE_REARMS = 1" in tui
    assert "let slowReopens = 0" in tui
    note = _region(tui, "const noteSession = (sid: unknown) => {",
                   "/** Every slot render goes through here")
    assert "      slowReopens = 0\n" in note, (
        "the budget belongs to the session change, so one re-open per change"
    )
    # the time-boxed latch it recovers FROM is unchanged, floor and window both
    assert "if ((slowOk && populated) || Date.now() >= slowUntil) scanned = true" in gate
    assert "if (slowUntil === 0) slowUntil = Date.now() + SLOW_WINDOW_MS" in gate

    # failing-first for (b): every pin is new
    # Converted: 63411e3 "perf: ... make the scanned latch recoverable" shipped
    # (b), so its pins are committed code — the edge is the one the Kilo rework
    # took (see test_kilo_pins_hold_in_committed_head): every pin below must be
    # PRESENT in HEAD. The latch fix only added a branch, so nothing was removed
    # and there is no removed-side pin to mirror. Reverting the re-arm (`git
    # revert 63411e3 -- harness/plugin/tui.tsx`) drops these from HEAD and this
    # goes red before the live-file pins do.
    pristine = _pristine_text()
    for pin in ("stillEmpty", "LATE_REARMS", "slowReopens", "else if (scanned"):
        assert pin in pristine, f"latch fix regressed from HEAD: {pin}"


def test_family_fanout_capped_and_short_model_gone():
    """The load-path audit fixes, as failing-first pins.

    A family read used to fan one store walk out per child, every pass — a
    20-worker burst cost 20 walks every 8s. Now a module cap (FAM_CAP) slices
    the child ids, and a capped family EXPLAINS itself: the Tokens detail
    carries a "+N more sessions" note, so the capped total can never read as
    the whole family (the rule DETAIL_LIMIT's own "+N more" follows). The
    audit also removed the dead `shortModel` helper the full-name rework left
    behind — nothing references it any more.

    The perf commit 63411e3 shipped every half of this, so the committed side
    now runs the way test_kilo_pins_hold_in_committed_head does: the pins
    below must be PRESENT in HEAD, and the dead `shortModel` helper it deleted
    must stay ABSENT from HEAD. The failing-first proof ran while the perf work
    was still uncommitted; a revert of 63411e3 now goes red here.
    """
    tui = _tui_text()
    pristine = _pristine_text()

    # the cap, and the child ids sliced with it
    assert "const FAM_CAP = 24" in tui
    assert "function familyScan(data_: Rec, sid: string): { ids: string[]; hidden: number }" in tui
    assert "kids.slice(0, FAM_CAP)" in tui
    assert "hidden: Math.max(0, kids.length - FAM_CAP)" in tui

    # a capped family is never silent: the Tokens detail lines state the
    # remainder instead of letting a short total read as complete
    assert "if (data.famHidden > 0) out.push(`+${data.famHidden} more sessions" in tui
    assert "not in token totals" in tui

    # the two COUNT(*) scans are cached with a timestamp and refreshed only
    # on a full/slow pass, never on every DB open
    assert "let heldCounts: { facts: number; workersActive: number } | null = null" in tui
    assert "heldCounts = hold ? { facts: data.factsCount, workersActive: data.workersActive } : null" in tui
    assert "Date.now() - Number(view.countsAt) < POLL_MS" in tui
    assert "d.countsAt = data.countsAt" in tui

    # the dead helper is gone, doc reference included
    assert "shortModel" not in tui

    # Converted: 63411e3 "perf: index the two hot memory scans, cap family
    # fan-out, cache counts, make the scanned latch recoverable" shipped the
    # whole change, so the "uncommitted" premise inverted — same flip as
    # test_kilo_pins_hold_in_committed_head. Every new pin must now be PRESENT
    # in HEAD (a revert of the cap/caching goes red), and the one pattern the
    # commit DELETED must stay ABSENT from HEAD (re-adding the dead helper goes
    # red). Pin sets are unchanged; only the relation flipped.
    pristine = _pristine_text()
    for pin in ("const FAM_CAP", "familyScan", "data.famHidden", "countsAt"):
        assert pin in pristine, f"perf work regressed from HEAD: {pin}"
    assert "function shortModel(rid: unknown): string {" not in pristine, (
        "the dead shortModel helper is back in HEAD"
    )


def test_next_step_ladder_derived_ordered_and_hidden():
    """Tier B: the read-only sidebar closes the loop with a derived, actionable
    "Next" step — nothing but fields the panel ALREADY reads.

    `nextStep()` is a deterministic priority ladder over pre-populated
    `view.`/`data.` fields only (view.note / data.todos / contextLimit+pct /
    view.waits / view.workersActive) and drives a single `<Row id="next">` in
    the same repaint/rev system as its siblings. First match wins; it BUILDS an
    action thunk without running it (side-effect free), and each action reuses
    an existing mechanism — the `harness.refresh` keybind, an `open.add(...)`
    expand, or a toast — never a new host call, query, import, or poll. With no
    signal it returns null, so the row is hidden entirely (never an empty
    placeholder).

    Stale-code premise retired: the ladder now ships in HEAD, so the tail pins
    the working tree instead of an "absent from HEAD" marker that has inverted.
    """
    tui = _tui_text()

    # the ladder: a real function, bracketed by a tracked read (see Row())
    ladder = _region(
        tui,
        "const nextStep = (): null | { text: string; action: () => void } => {",
        "const tokenValue = (): string => {",
    )
    assert "void view.rev" in ladder
    assert ladder.index("void view.rev") < ladder.index("view.note"), (
        "the ladder starts with a tracked read, before any signal"
    )
    # rule (2): the ladder introduces no new source — sync/fetch/import/sqlite
    for banned in ("sync?.(", "fetch(", "import(", "sqlite", "db.query"):
        assert banned not in ladder, f"nextStep must read only pre-populated fields: {banned!r}"
    # every branch reads only fields the panel already populates
    for field in ("view.note", "data.todos", "data.contextLimit", "pct()",
                  "view.waits", "view.workersActive"):
        assert field in ladder, f"ladder branch input missing: {field!r}"
    # priority order (first match wins): one marker per branch, in sequence
    markers = [
        "retry the failed scan",   # 1. load error -> re-run refresh
        "finish: ",                # 2. unfinished todo
        "compact soon",            # 3. window pressure
        'openRow("waits")',        # 4. pending waits
        'openRow("workers")',      # 5. active workers
    ]
    for m in markers:
        assert m in ladder, f"ladder branch marker missing: {m!r}"
    pos = [ladder.index(m) for m in markers]
    assert pos == sorted(pos), "the ladder's priority order is inverted"
    # no signal at all -> null, so the row hides
    assert "return null" in ladder

    # the row lives in the same rev/repaint system: id next, derived, hidden on
    # null, and its click runs the chosen action (not just a toggle)
    row = _region(tui, 'id="next"', 'id="window"')
    assert 'label="Next"' in row
    assert tui.count('id="next"') == 1, "exactly one Next row"
    assert row.count("void view.rev") >= 4, "every next-row closure starts with a tracked read"
    assert "nextStep()" in row, "the row is derived from the ladder"
    assert "return nextStep() === null" in row, "hidden entirely when the ladder is null"
    assert "s?.action?.()" in row, "the next row runs the chosen action on click"
    assert "props.onActivate" in tui, "rows gained an onActivate hook (reuses the click path)"

    # Working-tree pin (NOT a git-HEAD comparison): the ladder and its actions
    # are all in the live file. The original tail asserted these were ABSENT
    # from HEAD ("failing-first, uncommitted") — but the ladder ships in HEAD,
    # so that premise inverted and went red for the wrong reason. Pin the truth
    # that never flips: the machinery is present in the working tree, and the
    # structural asserts above each carry their own teeth against the live file.
    for pin in ("nextStep", 'id="next"', "onActivate", "retryScan",
                "openRow", "suggestCompact", 'label="Next"'):
        assert pin in tui, f"ladder machinery missing from the live file: {pin}"


# ---------------------------------------------------------------------------
# FEATURE 1+2: Next-step durable lesson + "running stale code" line.
#
# Working-tree + behavioral pins ONLY (no git-HEAD comparison: the
# commit-then-inverted-premise flip bit this file twice). The two new helpers
# are pure, so they are sliced out of the live tui.tsx and run under plain node
# against a scratch MEMORY.md fixture and a fake mtime; the wiring pins read the
# live file.
SCRATCH = REPO_ROOT / ".opencode" / "harness" / "tmp"


def _slice(tui, start, end):
    """Source slice between two anchors (same idiom as _region, but index())."""
    a, b = tui.index(start), tui.index(end)
    assert 0 <= a < b, f"anchors missing or out of order: {start!r} -> {end!r}"
    return tui[a:b]


def test_lesson_path_mirrors_state_db():
    """The lesson reader resolves MEMORY.md beside sessions.db — the SAME
    <root>/.opencode/harness/ shape cli.py/doctor.py get from memory_file().
    Pinned as a path shape + mirror of stateDb(), no new host API."""
    tui = _tui_text()
    sdb = _slice(tui, "function stateDb(directory: string): string {", "\n\nconst HarnessTui = {")
    mem = _slice(tui, "function memoryPath(directory: string): string {",
                 "/** Below this many shared terms")
    assert "/.opencode/harness/sessions.db" in sdb
    assert "`${directory}/.opencode/harness/MEMORY.md`" in mem, mem
    # same directory segment: MEMORY.md sits where sessions.db does
    assert ".opencode/harness/" in sdb and ".opencode/harness/" in mem


def test_next_lesson_and_stale_wiring_working_tree():
    """Wiring pins on the live file: the cached read rides the slow gate, reads
    through the SAME dynamic node:fs import, the Next row surfaces one muted
    line, and the stale line renders warn-only under the header."""
    tui = _tui_text()

    # cached lesson read: declared, refreshed on the slow gate, via node:fs
    assert "let lessonsCache: string[] | null = null" in tui
    assert "if (fs === null) fs = await import(\"node:fs\")" in tui
    assert "lessonsCache = lessonLines(fs.readFileSync(path, \"utf8\"))" in tui
    gate = _region(tui, "if (full || (!scanned && Date.now() >= slowNext)) {",
                   "        scanModels(data_)")
    assert "void refreshLessons(directory)" in gate, (
        "the lesson refresh must ride the existing slow/window gate, not a new interval"
    )
    # the cache read is silent on a miss: no log, only comments in the catch
    catch = _slice(tui, "const refreshLessons = async (directory: string): Promise<void> => {",
                   "const readProjectState = async")
    assert "catch {" in catch
    assert "log(" not in catch, "a lesson cache-miss must be silent — no error spam"

    # the Next row's detail line is derived and muted (no tone -> th.muted)
    next_lines = _slice(tui, "lines={() => {\n                    void view.rev\n                    // FEATURE 1",
                       "empty=\"(no next step)\"")
    assert "pickLesson(lessonsCache ?? [], query)" in next_lines
    assert "return lesson ? [lesson] : []" in next_lines, "empty match renders nothing"
    assert "void view.rev" in next_lines
    # nextStep()'s own ladder stays free of the new data source (unchanged scope)
    ladder = _region(tui, "const nextStep = (): null | { text: string; action: () => void } => {",
                     "const tokenValue = (): string => {")
    assert "pickLesson" not in ladder and "import(" not in ladder, (
        "the ladder itself must not gain a source; only its detail line does"
    )

    # staleness: computed once at setup, guarded, rendered under the header
    assert "let panelStale = false" in tui
    assert "panelStale = isStaleFile(Number(st?.mtimeMs ?? 0), startMs)" in tui
    assert "isStaleFile(mtimeMs: number, startMs: number): boolean" in tui
    assert '{"restart opencode — panel update pending"}' in tui
    # the warn line is gated on panelStale alone (not view.note — that is the
    # load-error channel) and colored th.warn, and sits before the Next row
    stale_line = _region(tui, "{panelStale ? (", 'id="next"')
    assert "fg={th.warn}" in stale_line
    assert "view.note" not in stale_line, "the stale line must not hijack view.note"


def _lesson_driver(tui):
    """Slice wcut + the pure NEXT-LESSON helpers into one node-runnable .ts.

    Node 24 strips TS types by default; the sliced block has no JSX, no Bun
    global, no sqlite, so it runs under plain node exactly as written.
    """
    wcut = _slice(tui, "function wcut(value: unknown, max: number): string {",
                  "/**\n * Split a detail line")
    block = _slice(tui, "function memoryPath(directory: string): string {",
                   "/**\n * A usage bar for the context window")
    driver = r'''
import { readFileSync } from "node:fs"
const file = process.argv[2]
const startMs = Number(process.argv[3])
const mtimeMs = Number(process.argv[4])
const lessons = lessonLines(readFileSync(file, "utf8"))
const out = {
  n: lessons.length,
  memPath: memoryPath("/proj"),
  relevantLong: pickLesson(lessons, "restart opencode — panel update pending"),
  relevantShort: pickLesson(lessons, "finish: durable lessons shape next step"),
  irrelevant: pickLesson(lessons, "quick zebra unrelated yak"),
  oneTerm: pickLesson(lessons, "panel only now"),
  dupe: pickLesson(lessons, "panel header"),
  staleNewer: isStaleFile(mtimeMs, startMs),
  staleOlder: isStaleFile(startMs - 1000, startMs),
  staleEqual: isStaleFile(startMs, startMs),
  staleNaN: isStaleFile(NaN, startMs),
}
console.log(JSON.stringify(out))
'''
    path = SCRATCH / "kilo_lessons.ts"
    path.write_text(wcut + "\n" + block + driver)
    return path


MEM_FIXTURE = (
    "# MEMORY.md header line\n"
    "\n"
    "- [panel] restart opencode when the panel update pending after editing the tui plugin file\n"
    "- [brief] durable lessons shape the next step\n"
    "- [zoo] the quick brown fox jumps over lazy dogs\n"
    "- [tui] keep the window bar honest and never truncate the panel header mid word\n"
    "- [tui] a repeated panel tag collapses into one lesson entry for you\n"
)


def test_lesson_selection_and_staleness_under_node():
    """Behavioral: slice the two helpers, run under node with a scratch
    MEMORY.md + a fake mtime/start. Asserts a relevant lesson is selected (cut
    at a word boundary), an irrelevant one returns '', the >=2 term-overlap
    threshold holds, a repeated [tag] collapses, and the stale/not-stale/
    unavailable staleness branches."""
    tui = _tui_text()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    fixture = SCRATCH / "MEMORY.md"
    fixture.write_text(MEM_FIXTURE)
    script = _lesson_driver(tui)
    start = 1_000_000_000_000
    mtime = start + 5_000  # file edited 5s after process start -> stale
    r = subprocess.run(["node", str(script), str(fixture), str(start), str(mtime)],
                       capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)

    # path resolution + parse (header + blank excluded, five `- ` entries)
    assert out["memPath"] == "/proj/.opencode/harness/MEMORY.md"
    assert out["n"] == 5, out["n"]

    # relevant long line: selected, cut at a word boundary (never mid-word).
    # The entry KEEPS its [tag] prefix — the line shown is the whole entry.
    long_a = MEM_FIXTURE.splitlines()[2][2:]  # strip leading "- "
    rl = out["relevantLong"]
    assert rl and rl.endswith("…"), rl
    assert len(rl) <= 61, rl
    assert long_a.startswith(rl[:-1]), rl
    assert long_a[len(rl) - 1:len(rl)] == " ", "the cut must land on a word boundary"

    # relevant short line (under the 60-col budget): returned whole, uncut
    assert out["relevantShort"] == MEM_FIXTURE.splitlines()[3][2:]

    # irrelevant: shares <2 terms -> nothing
    assert out["irrelevant"] == ""
    # exactly one shared term is below the bar -> nothing (threshold = 2)
    assert out["oneTerm"] == ""
    # repeated [tui] tag collapses to the FIRST entry, never the second
    assert out["dupe"].startswith("[tui] keep the window bar honest"), out["dupe"]
    assert "collapses" not in out["dupe"], "the duplicate-tagged line must not be chosen"

    # staleness: newer mtime -> stale; older -> not; equal -> not; NaN -> skip
    assert out["staleNewer"] is True
    assert out["staleOlder"] is False
    assert out["staleEqual"] is False
    assert out["staleNaN"] is False
