"""`skills_list {query}` ranks the likely skills first.

The host advertises every skill description at each step, but a pack import can
push the list past the point where skimming works. The tool keeps the same
output shape and gains an optional `query`: matched skills come first (with a
`◆` mark) and a `likely for this task:` line names them, so the model loads one
instead of reading thirty rows.

The matcher mirrors `skills.match()` in harness/skills.py; this file runs the
real TS helper under plain node so behaviour, not just its presence, is pinned.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / ".opencode" / "harness" / "tmp"


def _srv_text():
    sys.path.insert(0, str(REPO_ROOT))
    from harness.cli import _plugin_files

    path = Path(_plugin_files()[0])
    assert REPO_ROOT in path.parents
    return path.read_text()


def _slice(text, start, end):
    a = text.find(start)
    b = text.find(end, a + 1) if a >= 0 else -1
    assert 0 <= a < b, f"anchors missing or out of order: {start!r} -> {end!r}"
    return text[a:b]


def _run(source: str, driver: str):
    node = shutil.which("node")
    if not node:
        print("node not found - behavioural skills ranking case skipped")
        return None
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path = SCRATCH / "skills_router.ts"
    path.write_text(source + "\n" + driver)
    r = subprocess.run([node, str(path)], capture_output=True, text=True,
                       timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


def test_match_skills_ranks_name_hits_and_ignores_noise():
    srv = _srv_text()
    source = _slice(srv, "const SKILL_STOP =", "/**\n * Bundled skills")
    out = _run(source, r'''
const items = [
  { id: "harness-ponytail", name: "ponytail", description: "Laziness ladder for writing code" },
  { id: "harness-test-driven-development", name: "test-driven-development", description: "Red-green-refactor. Use when implementing logic or fixing bugs." },
  { id: "harness-git-workflow", name: "git-workflow", description: "Commits, branches and releases" },
]
console.log(JSON.stringify({
  named: matchSkills(items, "test driven development for a bug").map((s: any) => [s.name, s.score]),
  none: matchSkills(items, "zebra quantum"),
  blank: matchSkills(items, ""),
  limit: matchSkills(items, "test driven development", 1).length,
}))
''')
    assert out["named"][0][0] == "test-driven-development", out
    assert out["named"][0][1] >= 2
    assert out["none"] == [] and out["blank"] == []
    assert out["limit"] == 1


def test_stemmer_makes_a_request_match_a_skill_that_says_it_differently():
    srv = _srv_text()
    source = _slice(srv, "const SKILL_STOP =", "/**\n * Bundled skills")
    out = _run(source, r'''
const items = [
  { id: "test-driven-development", name: "test-driven-development", description: "Red-green-refactor. Use when implementing logic or fixing bugs." },
  { id: "code-review-and-quality", name: "code-review-and-quality", description: "Conducts multi-axis code review. Use before merging any change." },
  { id: "git-workflow", name: "git-workflow", description: "Commits, branches and releases" },
]
console.log(JSON.stringify({
  stems: [["failing"], ["tests"], ["fixing"], ["passed"], ["parser"]].map(([w]) => [w, skillStem(w)]),
  ranked: matchSkills(items, "fix the failing tests in the parser").map((s: any) => s.name),
  nudge: skillNudgeLine(items, "fix the failing tests in the parser"),
  quiet: skillNudgeLine(items, "zebra quantum farming around noon"),
  tiny: skillNudgeLine(items, "hi"),
  explicit: skillNudgeLine(items, "use test-driven-development for the failing tests"),
}))
''')
    assert out["stems"] == [["failing", "fail"], ["tests", "test"], ["fixing", "fix"],
                            ["passed", "pass"], ["parser", "parser"]], out
    assert out["ranked"][0] == "test-driven-development", out
    assert out["nudge"].startswith("Harness skills: likely for this task")
    assert "test-driven-development" in out["nudge"] and "(fix, test)" in out["nudge"]
    assert "skill_view" in out["nudge"], "the hint says how to act and when not to"
    assert out["quiet"] == "" and out["tiny"] == "", "no match and too short are both silent"
    assert out["explicit"] == "", "a message that already names the match is left alone"


def test_nudge_is_one_line_and_wired_into_the_context_hook():
    srv = _srv_text()
    fn = _slice(srv, "async function nudgeSkills", "/**\n * Bundled skills")
    assert 'if (!cfg.skillNudge) return' in fn, "skills.nudge: false turns it off"
    assert "skillNudged.get(sessionID) === key" in fn, "one message is nudged once"
    assert "event.system.push" in fn, "the hint is a system line, not a message"
    assert "Array.isArray(event?.system)" in fn, "a host without the array is skipped"
    assert "await nudgeSkills(ctx, directory, String(event?.sessionID" in srv
    # the nudge runs before auto-memory in the same hook and never breaks it
    order = srv.index("await nudgeSkills(ctx, directory")
    assert order < srv.index("await captureMemory(directory"), "hint first, capture second"


def test_skills_list_query_is_optional_and_marks_the_likely_ones():
    srv = _srv_text()
    handler = _slice(srv, 'name: "skills_list"', 'name: "skill_view"')
    assert "query: { type: \"string\"" in handler
    assert "const ranked = query ? matchSkills(items, query) : []" in handler
    assert "`likely for this task: ${ranked.map" in handler, "the ranked ids are named up front"
    assert "ranked.includes(s) ? \"◆\" : \"-\"" in handler, "matched rows are marked"
    # no query keeps the original output byte-for-byte: the guessed line is ""
    # and every row keeps the "-" marker
    assert ": \"\"\n            const text" in handler
    assert "\\n(${items.length} skills — call skill_view with an id to load one)" in handler
    # both entrypoints resolve through the project-aware roots (fixture parity)
    assert "skillRoots(projectDir)" in srv
