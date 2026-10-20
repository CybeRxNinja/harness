"""Side-panel totality: a Row repaint can never take the host sidebar down.

opencode 2.1.0 mounts each plugin slot contribution inside a solid
ErrorBoundary. The plugin's own try/catch only covers the first paint; a Row
prop thunk that throws on a *reactive repaint* (the host toast read
"undefined is not an object (evaluating 'e.length')") escaped to the host
boundary and unmounted the whole sidebar. The fix routes every prop thunk
through safeCall — log once per `where` via the module-level `loggedFailures`
set, return a fallback — and String-coerces data-fed <text> children so a
bad value can never reach the renderer as undefined.

Pure source assertions, no runtime: source comes from the same `_plugin_files()`
seam as test_tui_files_settle.py (so an installed copy cannot fool it).
HARNESS_TUI_PATH=<file> points at another tree — HEAD is red here.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tui_text():
    """Live TUI entrypoint; HARNESS_TUI_PATH replays another tree (e.g. HEAD)."""
    override = os.environ.get("HARNESS_TUI_PATH")
    if override:
        return Path(override).read_text()
    # The seam must resolve THIS repo: an installed harness copy would make
    # every assertion below judge the stale plugin instead of the working tree.
    sys.path.insert(0, str(REPO_ROOT))
    from harness.cli import _plugin_files
    path = Path(_plugin_files()[1])
    assert REPO_ROOT in path.parents, f"seam escaped the repo: {path}"
    return path.read_text()


def _slice(tui, start, end):
    """Source slice between anchors, with `end` searched AFTER `start`."""
    a = tui.find(start)
    b = tui.find(end, a + 1) if a >= 0 else -1
    assert 0 <= a < b, f"anchors missing or out of order: {start!r} -> {end!r}"
    return tui[a:b]


def _safe_call(tui):
    """safeCall body: from its declaration to Row's doc comment."""
    return _slice(tui, "const safeCall = <T,>", "/**")


def _row(tui):
    """Row's whole component body, up to the side-panel slot registration."""
    return _slice(tui, "const Row = (props", "// ---- the side panel")


def test_safe_call_returns_fallback_and_logs_only_first_failure():
    """(where, fn, fallback): fn() on success, fallback in catch, one log/where."""
    tui = _tui_text()
    body = _safe_call(tui)
    assert "where: string, fn: () => T, fallback: T" in body
    assert "return fn()" in body
    assert "return fallback" in body
    catch = body[body.index("catch (e)"):]
    assert "loggedFailures.has(where)" in catch
    assert "loggedFailures.add(where)" in catch
    assert "log(`${where} failed: ${short(e)}`)" in catch
    # only the first failure per `where` reaches the host log
    assert catch.index("if (!loggedFailures.has(where))") < catch.index("log(")
    # the dedupe set is module-level: one line per thunk for the process life
    assert "\nconst loggedFailures = new Set<string>()" in tui


def test_row_prop_thunks_run_only_inside_safe_call():
    """No bare prop thunk survives in Row — the pre-2.1.0 throw on repaint."""
    row = _row(_tui_text())
    for bare in (
        "props.hide()", "props.value()", "props.valueParts()",
        "props.valueFg()", "props.lines()", "props.lineParts(l)",
        "props.tone(l)", "const all = props.lines()", "const fg = props.tone",
    ):
        assert bare not in row, f"bare thunk still reachable on repaint: {bare}"
    for wrapped in (
        "safeCall(`row:${props.id}.hide`",
        "safeCall(`row:${props.id}.value`",
        "safeCall(`row:${props.id}.valueParts`",
        "safeCall(`row:${props.id}.valueFg`",
        "safeCall(`row:${props.id}.lines`",
        "safeCall(`row:${props.id}.lineParts`",
        "safeCall(`row:${props.id}.tone`",
        "safeCall(`row:${props.id}.click`",
    ):
        assert wrapped in row, f"prop thunk not wrapped in safeCall: {wrapped}"
    # the click/onActivate path is inside the guarded thunk, not a bare handler
    click = _slice(row, "onMouseDown={() =>", "props.label")
    assert click.index("safeCall(`row:${props.id}.click`") < click.index("props.onActivate()")


def test_row_text_children_are_string_coerced():
    """Data-fed <text> children are total; definite/literal children unchanged."""
    row = _row(_tui_text())
    assert "{p.text}" not in row, "raw data text child can hand the renderer undefined"
    assert row.count('{String(p.text ?? "")}') >= 3, "data-fed <text> children are not String-coerced"
    assert '{" "}' in row
    assert "{props.label}" in row


def test_files_row_still_gates_on_verdict_and_rows():
    """Regression guard for the settle() fix that preceded this one."""
    assert "done && Array.isArray(rows)" in _tui_text()
