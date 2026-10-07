"""Kilo side-panel grammar + Window tok/s rate: failing-first pins for the tui.tsx rework.

The working-tree change rebuilds the sidebar rows (justified label/value
pairs split by splitRow, no LABEL_W column) and measures the Window rate as
opencode's own tok/s (lastTurn/turnRate/asMs feeding data.currentRate).
Each pin below is verified ABSENT (new) or PRESENT (removed) in
`git show HEAD:harness/plugin/tui.tsx` by test_kilo_pins_fail_on_pristine_head,
so the same pins run red on the old code and green on the new.
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


def test_kilo_pins_fail_on_pristine_head():
    """Failing-first proof: every NEW pin is absent from HEAD, every REMOVED
    pin is present in HEAD — i.e. the live pins below run red on old code."""
    pristine = _pristine_text()
    for pin in NEW_PINS:
        assert pin not in pristine, f"not a new pin (already in HEAD): {pin}"
    for pin in REMOVED_PINS:
        assert pin in pristine, f"not a removed pin (missing from HEAD): {pin}"


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
