"""The Memory row is a briefing, not the newest six rows.

Reading it used to be a decoding exercise: every fact printed bare and cut at
30 cols, so a decision, a blocker and a stray note all looked the same
(`Posted both. Updating the…`), and because ~90% of the store is turn activity
the handful of facts that matter never even reached the six-row window.

`digestFacts` fixes both halves: each fact is classified by the marker it was
written with, labelled in human words (`Blocked`, `Why`, `Fixed`, `Landed`,
`Now`), aged, and shown one per kind — blockers first. This file runs the
helpers exactly as written (sliced out of tui.tsx, no JSX, no Bun global) under
plain node.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / ".opencode" / "harness" / "tmp"


def _tui_text():
    sys.path.insert(0, str(REPO_ROOT))
    from harness.cli import _plugin_files

    path = Path(_plugin_files()[1])
    assert REPO_ROOT in path.parents
    return path.read_text()


def _slice(text, start, end):
    a = text.find(start)
    b = text.find(end, a + 1) if a >= 0 else -1
    assert 0 <= a < b, f"anchors missing or out of order: {start!r} -> {end!r}"
    return text[a:b]


def _driver(tui):
    """The four pure blocks the digest needs, spliced into one node-runnable .ts."""
    kinds = _slice(tui, "const FACT_KINDS:", '/**\n * "how long ago" for a worker row')
    wcut = _slice(tui, "function wcut(value: unknown, max: number): string {", "/**\n * Split a detail line")
    digest = _slice(tui, "/**\n * Which KIND a stored fact is", "/**\n * NEXT-LESSON HELPERS")
    driver = r'''
const now = Math.floor(Date.now() / 1000)
const row = (text: string, secs = 60, source = "turn") => ({ text, source, ts: now - secs })
const kinds = {
  progress: factKind("progress [ses_1]: I'll load the ponytail skill."),
  done: factKind("done [ses_1]: worker finished"),
  blockedNote: factKind("blocked [ses_1]: bun is not installed"),
  decision: factKind("Decision: keep the facts index as a fast path"),
  decided: factKind("decided the panel keeps append-only slots"),
  fix: factKind("Root cause: a colour NAME on an update unmounted the sidebar"),
  fixed: factKind("fixed: the count never reached the view state"),
  blocker: factKind("Blocker: the release workflow still needs a tag"),
  outcome: factKind("Tests passed: 246 passed in 51s."),
  error: factKind("error: opencode run timed out"),
  lesson: factKind("lesson: rows with nothing to say hide themselves"),
  note: factKind("the mailbox gets a delivered flag so inbox() does not repeat"),
  bodyNote: factBody("blocked [ses_1]: bun is not installed"),
  bodyDecision: factBody("Decision: keep the facts index as a fast path"),
  bodyPlain: factBody("the mailbox gets a delivered flag"),
}
// newest first, exactly as the SQL hands them over: a wall of progress notes on
// top of the facts that matter (the live store's real shape).
const rows = [
  row("progress [ses_1]: I'll load the ponytail skill.", 30),
  row("progress [ses_1]: Posted both. Updating the diff.", 60),
  row("progress [ses_1]: Posted both. Updating the diff.", 90),
  row("Tests passed: 246 passed in 51s.", 120),
  row("Blocker: bun is not installed so bun cases skip", 3600),
  row("Decision: keep the facts index as a fast path", 7200),
  row("Root cause: a colour NAME on an update unmounted the sidebar", 9000),
]
const full = digestFacts(rows, 81)
const small = digestFacts(rows.slice(3, 6), 3)
const empty = digestFacts([])
// the read window is capped: `total` is the on-disk count, so the remainder is
// still honest when it is larger than the window
const capped = digestFacts(rows.slice(0, 2), 900)
console.log(JSON.stringify({ kinds, full, small, empty, capped }))
'''
    path = SCRATCH / "memory_digest.ts"
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path.write_text(kinds + "\n" + wcut + "\n" + digest + "\n" + driver)
    return path


NODE = shutil.which("node")


def test_fact_kinds_and_bodies_under_node():
    """Each marker keeps its meaning, and the row stops repeating it."""
    if not NODE:
        print("node not found - behavioural Memory digest case skipped")
        return
    script = _driver(_tui_text())
    r = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                       timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)["kinds"]
    assert out["progress"] == "progress"
    assert out["done"] == "progress", "a done note is where the work got to"
    assert out["blockedNote"] == "blocker", "a blocked note states a real blocker"
    assert out["decision"] == "decision"
    assert out["decided"] == "decision"
    assert out["fix"] == "fix"
    assert out["fixed"] == "fix"
    assert out["blocker"] == "blocker"
    assert out["outcome"] == "outcome"
    assert out["error"] == "blocker"
    assert out["lesson"] == "lesson"
    assert out["note"] == "note"
    # the label column carries the kind; the body must not repeat its marker,
    # and the writer's session tag never reaches the row.
    assert out["bodyNote"] == "bun is not installed"
    assert out["bodyDecision"] == "keep the facts index as a fast path"
    assert out["bodyPlain"] == "the mailbox gets a delivered flag"


def test_digest_leads_with_the_blocker_and_states_the_remainder():
    """Four kinds beat four progress notes, ages pin right, remainder honest."""
    if not NODE:
        print("node not found - behavioural Memory digest case skipped")
        return
    script = _driver(_tui_text())
    r = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                       timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)

    full = out["full"]
    # DETAIL_LIMIT is 5 and one slot is the remainder line
    assert len(full) == 5, full
    # padding aligned the labels; normalise it before matching the words
    norm = [" ".join(l.split()) for l in full]
    assert norm[0].startswith("✕ Blocked bun is not installed"), full
    assert norm[1].startswith("⚑ Why keep the facts index"), full
    assert norm[2].startswith("✓ Fixed a colour NAME"), full
    assert norm[3].startswith("● Landed Tests passed: 246"), full
    assert norm[4] == "… +77 older", full
    # the repeated narration collapsed: one Now line, not two
    assert not any(l.startswith("◐") for l in full), full
    assert "ses_1" not in " ".join(full), "session tags never reach the row"
    # every line fits the 37-cell panel: the row must never be mid-ellipsised
    # by the host's truncator (that mangles the sentence into "Root...egression")
    assert all(len(l) <= 37 for l in full if not l.startswith("…")), full

    # a store that fits in the row prints no remainder line
    small = out["small"]
    assert len(small) == 3 and not small[-1].startswith("…"), small
    assert small[0].startswith("✕ Blocked"), small
    assert out["empty"] == []
    # the remainder counts the store, not the read window (2 shown of 900)
    assert out["capped"][-1] == "… +898 older", out["capped"]
