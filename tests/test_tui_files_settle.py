"""Files row: ctx.vcs.status() rows are not settle()'s verdict.

settle() answers Promise<boolean> — true when the wrapped promise beat the
time-box, false when the box won. HEAD called it as
`const rows = await settle(ctx.vcs.status(), SCAN_MS)` and gated on
`Array.isArray(rows)`: a boolean is never an array, so data.files was [] on
every pass and the Files row could never list a change. The fix captures the
status rows through a closure and gates on the verdict AND the rows
(`done && Array.isArray(rows)`) — the same shape the git fallback below it
already used.

Failing-first: HARNESS_TUI_REV=HEAD reads the pre-fix tree for the subject,
so the structural test goes red there and green on the working tree.
"""

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / ".opencode" / "harness" / "tmp"


def _tui_text():
    """Live TUI entrypoint; HARNESS_TUI_REV=HEAD replays the pre-fix tree."""
    rev = os.environ.get("HARNESS_TUI_REV")
    if rev:
        r = subprocess.run(
            ["git", "show", f"{rev}:harness/plugin/tui.tsx"],
            capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT),
        )
        assert r.returncode == 0, r.stderr[-500:]
        return r.stdout
    override = os.environ.get("HARNESS_TUI_PATH")
    if override:
        return Path(override).read_text()
    from harness.cli import _plugin_files
    return Path(_plugin_files()[1]).read_text()


def _slice(tui, start, end):
    """Source slice between anchors, with `end` searched AFTER `start`."""
    a = tui.find(start)
    b = tui.find(end, a + 1) if a >= 0 else -1
    assert 0 <= a < b, f"anchors missing or out of order: {start!r} -> {end!r}"
    return tui[a:b]


def test_files_row_gates_on_verdict_and_rows_not_verdict_as_rows():
    """The vcs.status branch keeps the rows apart from settle()'s boolean."""
    tui = _tui_text()
    assert "function settle(p: any, ms: number): Promise<boolean>" in tui
    region = _slice(tui, 'if (typeof ctx?.vcs?.status === "function") {', "} else {")
    # the bug: settle's boolean bound as rows, then asked if it is an array
    assert "const rows = await settle(ctx.vcs.status(), SCAN_MS)" not in region
    assert "= Array.isArray(rows)" not in region
    # the fix: rows arrive through a closure, the gate needs verdict AND rows
    assert "let rows: Rec[] | null = null" in region
    assert "const done = await settle((async () => {" in region
    assert "rows = await ctx.vcs.status()" in region
    assert "data.files = done && Array.isArray(rows)" in region
    assert "? rows.map((r: Rec) => ({" in region
    # a failed or timed-out status still settles the row, never hangs it
    assert "data.files = []" in region


def test_settle_verdict_is_boolean_under_node():
    """Behavioral: settle() answers a boolean even when the inner value is rows.

    Slices the live settle() and runs it under plain node: a resolved rows
    array still settles to `true` (so Array.isArray on the verdict is false —
    the confusion that emptied the Files row), the closure shape maps the row,
    and a hung status times out to an empty row.
    """
    tui = _tui_text()
    settle = _slice(tui, "function settle(p: any, ms: number): Promise<boolean> {",
                    "function familyScan")
    driver = settle + r'''
async function main() {
  const rowsValue = [{ file: "a.ts", status: "M", additions: 1, deletions: 2 }]
  const verdict = await settle(Promise.resolve(rowsValue), 50)
  let rows = null
  const done = await settle((async () => { rows = rowsValue })(), 50)
  let late = null
  const doneLate = await settle(new Promise(() => {}), 20)
  console.log(JSON.stringify({
    verdictIsBoolean: typeof verdict === "boolean",
    verdictIsArray: Array.isArray(verdict),
    fixedMaps: done && Array.isArray(rows) ? rows.length : -1,
    timeoutSettlesEmpty: doneLate === false && late === null,
  }))
}
main()
'''
    SCRATCH.mkdir(parents=True, exist_ok=True)
    script = SCRATCH / "files_settle.ts"
    script.write_text(driver)
    r = subprocess.run(["node", str(script)], capture_output=True, text=True,
                       timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)
    assert out["verdictIsBoolean"] is True
    assert out["verdictIsArray"] is False, "the verdict is never the rows"
    assert out["fixedMaps"] == 1
    assert out["timeoutSettlesEmpty"] is True
