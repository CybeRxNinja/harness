"""Side-panel colours: every `fg` is HEX, never a colour NAME.

opencode 2.1.1 unmounted the whole sidebar slot — `harness crashed in slot
sidebar.content: undefined is not an object (evaluating 'e.length')` — as soon
as the Window row's tint changed after mount. The colour being applied was
`ok = ctx.theme.text.feedback.success ?? "green"`: a theme without
feedback.success left the NAME "green" on a live `fg`, and the host's
text-UPDATE path rejects the colour names its create path accepted.

Measured on 2.1.1 (`opencode -c` in a pty): `th.muted` -> theme hex and hex ->
hex both update fine; `th.muted` -> the named "green" crashes the slot; pinning
the colour so it never updates makes the panel render again. So `col()`
normalises every theme colour to hex — literals pass through, known names are
mapped, everything else lands on the floor — and the theme block uses it for
all six colours.

Structural assertions plus one behavioural run of the sliced `col()` under deno
(the only TS-capable runtime on this machine; the case skips loudly when deno
is absent, so a node-only CI still runs the structural half).
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
    # every assertion below judge a stale plugin instead of the working tree.
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


def _colour_block(tui):
    """The theme block: `const th = {` through `const bad = …`."""
    return _slice(tui, "const th = {", "const warn = col(")


def _col_source(tui):
    """The pure helper: NAMED_HEX + col, from its map to the end of col()."""
    return _slice(tui, "const NAMED_HEX: Record<string, string> = {",
                  "\n/** One log line per failing thunk")


def test_every_theme_colour_goes_through_col():
    """base/muted/accent/warn/ok/bad/warn are normalised, not raw theme reads."""
    tui = _tui_text()
    block = _colour_block(tui)
    for key in ("base", "muted", "accent", "warn"):
        assert re.search(rf"{key}: col\(", block), f"theme.{key} bypasses col()"
    for key in ("ok", "bad", "warn"):
        assert re.search(rf"const {key} = col\(", tui), f"{key} bypasses col()"
    # The regression itself: a named-colour floor is back anywhere in the panel.
    for name in ("green", "red", "yellow", "white", "gray", "magenta"):
        assert f'?? "{name}"' not in tui, f"named colour {name!r} is back on a live fg"
        assert f'|| "{name}"' not in tui, f"named colour {name!r} is back on a live fg"


def test_named_map_values_are_hex():
    """Any name we do accept is translated to hex before it reaches the host."""
    tui = _tui_text()
    body = _slice(tui, "const NAMED_HEX: Record<string, string> = {", "}")
    pairs = re.findall(r'(\w+):\s*"([^"]+)"', body)
    assert pairs, "NAMED_HEX has no entries"
    for name, value in pairs:
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{name} maps to non-hex {value!r}"
    names = {n.lower() for n, _ in pairs}
    assert {"green", "red", "yellow", "white", "gray", "magenta"} <= names, names


def test_col_normalises_a_named_colour_at_runtime():
    """Behavioural: hex passes, a name becomes hex, junk/non-strings floor.

    The exact crash input is `col("green")` — it must never return "green".
    """
    deno = shutil.which("deno")
    if not deno:
        # No TS runtime here; the structural cases above still pin the rule.
        print("deno not found - behavioural col() case skipped")
        return
    tui = _tui_text()
    driver = _col_source(tui) + r"""
function main() {
  const out = {
    hex: col("#abcdef", "#000001"),
    short: col("#abc", "#000001"),
    rgb: col("rgb(1,2,3)", "#000001"),
    named: col("green", "#000001"),
    namedUpper: col("  RED ", "#000001"),
    unknownName: col("chartreuse", "#000001"),
    empty: col("", "#000001"),
    nonString: col(undefined, "#000001"),
    object: col({ fg: "#ffffff" }, "#000001"),
  }
  console.log(JSON.stringify(out))
}
main()
"""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    script = SCRATCH / "colors_col.ts"
    script.write_text(driver)
    r = subprocess.run([deno, "run", "--quiet", str(script)],
                       capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)
    assert out["hex"] == "#abcdef"
    assert out["short"] == "#abc"
    assert out["rgb"] == "rgb(1,2,3)"
    # the crash input: a name must come back as hex, never as the name itself
    assert out["named"] == "#5fd75f", out["named"]
    assert out["namedUpper"] == "#ff5f5f", out["namedUpper"]
    assert out["unknownName"] == "#000001"
    assert out["empty"] == "#000001"
    assert out["nonString"] == "#000001"
    assert out["object"] == "#000001"
