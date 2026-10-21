"""Panel visuals: an icon + hex accent per row, and nothing when there is nothing.

The sidebar used to spend rows on placeholders — `Files  unavailable`,
`Models  not used`, a Window row reading `—`. A stat panel that narrates its
own emptiness is noise, so a row whose source has no data now HIDES instead
(`hide()` on Files/Window/Tokens/Models), and the "unavailable" sentence is
gone rather than restyled.

The rest is the visual language: one geometric icon and one literal-hex
accent per section (ACCENT), an animated head on the progress bars, a
sparkline of recent output, and a frame clock that only repaints while
something on screen is moving. Every colour is HEX for the reason
`test_tui_colors.py` records — a colour NAME on a live `fg` unmounted the
whole sidebar on opencode 2.1.1.

Structural assertions over the live entrypoint (HARNESS_TUI_PATH replays
another tree), plus one behavioural run of the sliced pure helpers under node.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / ".opencode" / "harness" / "tmp"


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


def _row(tui, row_id):
    """One <Row ... id="x" .../> block, up to the next Row or the closing box."""
    start = tui.index(f'id="{row_id}"')
    end = tui.find('<Row\n                  id="', start + 1)
    if end < 0:
        end = tui.index("</box>", start)
    return tui[start:end]


def test_rows_with_no_data_hide_instead_of_narrating_emptiness():
    """Files unknown / Window+Tokens cold / Models unused render nothing at all."""
    tui = _tui_text()
    files = _row(tui, "files")
    assert "hide={() => {" in files and "return !data.vcsKnown" in files, (
        "a repository the host cannot read is not a stat — the row must hide"
    )
    assert "repository state unavailable" not in tui, "the placeholder sentence is gone"
    assert 'return "unavailable"' not in tui, "no value text announces the gap either"

    for row_id in ("window", "tokens"):
        block = _row(tui, row_id)
        assert "return !data.tokens" in block, f"{row_id} must hide before usage exists"
    models = _row(tui, "models")
    assert "return !(view.models ?? 0)" in models, "an unused Models row is not a stat"
    # The rows that already hid on emptiness keep doing so.
    for needle in ('return !(view.todos ?? 0)', "return !view.workers", "return !(view.facts ?? 0)"):
        assert needle in tui, needle


def test_every_row_has_an_icon_and_a_literal_hex_accent():
    """Colour carries the section: one icon + one #rrggbb accent per row."""
    tui = _tui_text()
    start = tui.index("const ACCENT: Record<string, string> = {")
    block = tui[start:tui.index("\n}", start)]
    pairs = dict(re.findall(r'(\w+): "(#[0-9a-fA-F]{6})"', block))
    rows = ("next", "window", "tokens", "models", "todo", "workers", "waits", "files", "skills", "agents", "memory")
    assert set(rows) <= set(pairs), f"missing accents: {set(rows) - set(pairs)}"
    for row_id in rows:
        region = _row(tui, row_id)
        assert f"accent={{ACCENT.{row_id}}}" in region, f"{row_id} does not use its accent"
        assert re.search(r'icon="[^"]+"', region), f"{row_id} has no icon"
    # The icon breathes while its row is open, and the value falls back to the
    # same accent (the Window row still overrides with the pressure ramp).
    assert "const rowIconFg = (id: string, accent: string): string => {" in tui
    assert "mixHex(accent, \"#000000\", 0.5)" in tui
    assert "props.valueFg?.() ?? props.accent" in tui


def test_animation_frame_clock_is_gated_and_released():
    """A frame tick that repaints only while something moves, and is cleared."""
    tui = _tui_text()
    assert "const ANIM_MS = 420" in tui
    anim = _slice(tui, "const animating = (): boolean => {", "// The Waits countdown ticks")
    # Off-screen stand-down first, then every reason to animate.
    assert "if (lastRender === 0 || Date.now() - lastRender > POLL_MS * 4) return false" in anim
    assert "if (Array.isArray(view.open) && view.open.length) return true" in anim
    assert "if (view.scanning) return true" in anim
    assert "if (Date.now() < animUntil) return true" in anim
    # The tick itself only bumps the store counter — it is a repaint clock,
    # never a second data poll.
    tick = _slice(tui, "animTimer = setInterval(() => {", "}, ANIM_MS)")
    assert "if (!animating()) return" in tick
    assert "d.frame = Number(d.frame ?? 0) + 1" in tick
    for banned in ("load(", "probe()", "readProjectState", "db.query"):
        assert banned not in tick, f"the frame clock must not read data: {banned}"
    # Released with the plugin, like the poll and the Waits tick.
    cleanup = tui[tui.index("return () => {"):]
    assert "if (animTimer !== null) clearInterval(animTimer)" in cleanup
    # A click and a landed load both wake the animation for a moment.
    assert "wake(4000)" in tui and "wake(2400)" in tui


def test_visual_helpers_are_pure_and_bounded_under_node():
    """mixHex/heat/abar/spark/gradient: hex out, fixed width, no surprises."""
    node = shutil.which("node")
    if not node:
        print("node not found - behavioural visual helper case skipped")
        return
    tui = _tui_text()
    source = _slice(tui, "function hexRgb", "function waitPct")
    driver = source + r"""
function main() {
  const hex = /^#[0-9a-f]{6}$/i
  const accents = Object.values(ACCENT)
  const grad = gradient("harness", 3)
  const out = {
    mixLow: mixHex("#000000", "#ffffff", 0),
    mixMid: mixHex("#000000", "#ffffff", 0.5),
    mixHigh: mixHex("#000000", "#ffffff", 1),
    mixJunk: mixHex("nope", "#ffffff", 0.5),
    heatLow: heat(0),
    heatMid: heat(50),
    heatHigh: heat(100),
    heatClamp: heat(-40),
    barEmpty: abar(0, 8, 0),
    barWidths: [0, 13, 50, 100].map((p) => abar(p, 8, 0).length),
    barFullHeadless: abar(100, 8, 8),
    barHeadMoves: abar(50, 8, 0) !== abar(50, 8, 1),
    sparkEmpty: spark([]),
    sparkZeros: spark([0, 0, 0]),
    sparkShape: spark([1, 2, 3]),
    sparkWidth: spark([1, 2, 3, 4, 5, 6, 7, 8]).length,
    spin0: spin(0),
    spinWrap: spin(SPIN.length) === spin(0),
    accentHex: accents.every((c) => hex.test(c)),
    gradientWidth: grad.length,
    gradientHex: grad.every((p) => hex.test(p.fg)),
    gradientWalk: gradient("abc", 1)[0].fg === BRAND[1],
  }
  console.log(JSON.stringify(out))
}
main()
"""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    script = SCRATCH / "visuals_helpers.ts"
    script.write_text(driver)
    r = subprocess.run([node, str(script)], capture_output=True, text=True,
                       timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)

    assert out["mixLow"] == "#000000"
    assert out["mixMid"] == "#808080"
    assert out["mixHigh"] == "#ffffff"
    assert out["mixJunk"] == "#ffffff", "an unparsable colour never escapes as a name"
    assert out["heatLow"] == "#5fd75f", "calm is green"
    assert out["heatMid"] == "#ffd75f", "half is amber"
    assert out["heatHigh"] == "#ff5f5f", "pressure is red"
    assert out["heatClamp"] == out["heatLow"], "out-of-range input clamps, never wraps"
    assert out["barEmpty"] == "░" * 8
    assert out["barWidths"] == [8, 8, 8, 8], "the bar never changes width"
    assert out["barFullHeadless"] == "█" * 8
    assert out["barHeadMoves"] is True, "the head travels with the frame"
    assert out["sparkEmpty"] == ""
    assert out["sparkZeros"] == "▁▁▁"
    assert out["sparkShape"] == "▃▆█"
    assert out["sparkWidth"] == 8
    assert out["spin0"] == "⠋" and out["spinWrap"] is True
    assert out["accentHex"] is True
    assert out["gradientWidth"] == 7 and out["gradientHex"] is True
    assert out["gradientWalk"] is True
