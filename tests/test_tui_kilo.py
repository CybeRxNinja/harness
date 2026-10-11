"""Kilo side-panel grammar + Window tok/s rate: failing-first pins for the tui.tsx rework.

The working-tree change rebuilt the sidebar rows (justified label/value
pairs split by splitRow, no LABEL_W column) and measures the Window rate as
opencode's own tok/s (lastTurn/turnRate/asMs feeding data.currentRate).
The base check, test_kilo_pins_hold_in_committed_head, holds the rework's
pins against the committed `git show HEAD:harness/plugin/tui.tsx` — the
rework landed in the base, so the new pins must be present and the removed
ones absent, and a revert of those rows goes red.
"""

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
    open. HEAD does not carry the doc block at all, so the teeth of this pin
    are proven in-memory below (restore the wording, watch it leak).

    (b) The latch closed on whatever the section read at the `slowUntil`
    deadline, so skills seeded AFTER it stayed invisible until a manual
    `/harness-refresh` (the "Skills 0 installed" with skills on disk class). A
    later load that still finds the latch over an empty section now re-opens
    the window once: the same time-boxed slow half re-runs, no new host call,
    and LATE_REARMS is what keeps it from being a loop.

    Failing-first for (b): every pin below is absent from HEAD.
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
    pristine = _pristine_text()
    for pin in ("stillEmpty", "LATE_REARMS", "slowReopens", "else if (scanned"):
        assert pin not in pristine, f"not a new pin (already in HEAD): {pin}"


def test_family_fanout_capped_and_short_model_gone():
    """The load-path audit fixes, as failing-first pins.

    A family read used to fan one store walk out per child, every pass — a
    20-worker burst cost 20 walks every 8s. Now a module cap (FAM_CAP) slices
    the child ids, and a capped family EXPLAINS itself: the Tokens detail
    carries a "+N more sessions" note, so the capped total can never read as
    the whole family (the rule DETAIL_LIMIT's own "+N more" follows). The
    audit also removed the dead `shortModel` helper the full-name rework left
    behind — nothing references it any more.

    Failing-first: every new pin is absent from HEAD, and the helper it
    deletes is present there.
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

    # failing-first: the pins above run red on pristine HEAD
    for pin in ("const FAM_CAP", "familyScan", "data.famHidden", "countsAt"):
        assert pin not in pristine, f"not a new pin (already in HEAD): {pin}"
    assert "function shortModel(rid: unknown): string {" in pristine
