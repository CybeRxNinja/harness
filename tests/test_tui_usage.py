"""Skills/Agents rows: session usage instead of the installed catalogue.

Listing nineteen installed skills (and five of sixteen agents) told the owner
nothing about the session: the counts live in the value, and the expanded list
now answers "what did this session actually use". Skill loads are recorded per
session by the server half (`skill_uses`, written from the `skill_view` tool
and from opencode's native `skill` tool through execute.before); agents are
derived from the session's selected agent plus its family children, both rows
the panel already loads.

Both rows also grow an `↗` chip OUTSIDE their clickable header, so opening the
folder where skills/agents can be added by hand never toggles the row.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / ".opencode" / "harness" / "tmp"


def _plugin_texts():
    """(server, tui) from the same seam the other TUI tests use."""
    sys.path.insert(0, str(REPO_ROOT))
    from harness.cli import _plugin_files

    srv, tui = _plugin_files()
    srv, tui = Path(srv), Path(tui)
    assert REPO_ROOT in tui.parents and REPO_ROOT in srv.parents
    return srv.read_text(), tui.read_text()


def _slice(text, start, end):
    a = text.find(start)
    b = text.find(end, a + 1) if a >= 0 else -1
    assert 0 <= a < b, f"anchors missing or out of order: {start!r} -> {end!r}"
    return text[a:b]


def _row(tui, row_id):
    start = tui.index(f'id="{row_id}"')
    end = tui.find('<Row\n                  id="', start + 1)
    if end < 0:
        end = tui.index("</box>", start)
    return tui[start:end]


def test_skills_row_shows_installed_count_and_session_usage():
    _, tui = _plugin_texts()
    skills = _row(tui, "skills")
    # inventory in the value, usage in the detail list
    assert '`${used} used · ${installed} installed`' in skills
    assert "data.skillsUsed.map((s, i) =>" in skills
    assert '`${i === 0 ? "●" : "▪"} ${s}`' in skills
    assert "data.skills.map((s) =>" not in skills, "the catalogue is not a usage list"
    assert 'empty="(none used this session)"' in skills
    # the data comes from the per-session table, newest first
    assert "out.skillsUsed =" in tui
    assert "SELECT name FROM skill_uses WHERE session = ? ORDER BY ts DESC LIMIT 20" in tui
    assert "data.skillsUsed = state.skillsUsed" in tui


def test_agents_row_shows_available_count_and_session_usage():
    _, tui = _plugin_texts()
    agents = _row(tui, "agents")
    assert '`${used} used · ${avail} available`' in agents
    assert "[...data.agentsUsed]" in agents
    assert '`${a === cur ? "●" : "◆"} ${a}`' in agents
    assert "[...data.agents]" not in agents, "the roster is not the session's usage"
    # usage comes from rows the panel already loads: selected agent + family
    helper = _slice(tui, "function usedAgents(", "function stateDb")
    assert "data_?.session?.get?.(sid)?.agent" in helper
    assert "familyIds(data_, sid).slice(1)" in helper
    assert "data_?.session?.get?.(id)?.agent" in helper
    assert "data.agentsUsed = usedAgents(data_, sessionID)" in tui


def test_skill_loads_are_recorded_per_session():
    srv, _ = _plugin_texts()
    assert "async function recordSkillUse(projectDir: string, sessionID: string, name: unknown)" in srv
    body = _slice(srv, "async function recordSkillUse(", "function workerName")
    assert 'replace(/^harness-/, "")' in body, "names match what the panel lists"
    assert "DELETE FROM skill_uses WHERE session = ? AND name = ?" in body, "one row per skill per session"
    assert "INSERT INTO skill_uses(session, name, ts)" in body
    assert 'CREATE TABLE IF NOT EXISTS skill_uses' in srv
    # both load paths count: the harness tool and opencode's native skill tool
    handler = _slice(srv, 'name: "skill_view"', 'name: "memory_recall"')
    assert "execute: async (input: Rec, context: Rec) =>" in handler
    assert "recordSkillUse(directory, String(context?.sessionID ?? \"\")" in handler
    hook = _slice(srv, 'ctx.tool.hook("execute.before"', 'log(`worker hook failed')
    assert 'tool === "skill" || tool === "skill_view"' in hook
    assert "await recordSkillUse(directory, String(event?.sessionID ?? \"\"), id)" in hook
    # canonical schema (Python owns the DB; the plugin mirrors it)
    schema = (REPO_ROOT / "harness" / "store.py").read_text()
    assert "CREATE TABLE IF NOT EXISTS skill_uses(" in schema
    assert "idx_skill_uses_session" in schema


def test_folder_chip_is_outside_the_row_toggle():
    _, tui = _plugin_texts()
    for row_id, kind in (("skills", "skills"), ("agents", "agents")):
        block = _row(tui, row_id)
        assert f'action={{() => openFolder("{kind}")}}' in block
    # the header box closes BEFORE the chip: a separate hit target, so opening
    # the folder can never also toggle the row.
    row = _slice(tui, "const Row = (props", "// ---- the side panel")
    assert "</box>\n        {props.action ? (" in row
    chip = _slice(row, "{props.action ? (", "</text>\n        ) : null}")
    assert '{" ↗"}' in chip
    assert "safeCall(`row:${props.id}.action`" in chip
    assert "props.action?.()" in chip
    assert "const openFolder = (kind: \"skills\" | \"agents\")" in tui
    open_fn = _slice(tui, "const openFolder = (kind:", "const dbStamp")
    # skills open the HOST's project dir: it is auto-discovered and advertised,
    # and it is where `harness skills add` installs packs.
    assert "/.opencode/skills" in open_fn and "/.opencode/agents" in open_fn
    assert "mkdirSync(dir, { recursive: true })" in open_fn
    assert "Bun.spawn(explorerCmd(dir)" in open_fn
    assert "opened ${dir}" in open_fn, "the toast confirms what opened"
    assert "folder: ${dir}" in open_fn, "a headless host still gets the path"


def test_explorer_cmd_maps_each_platform_under_node():
    node = shutil.which("node")
    if not node:
        print("node not found - behavioural explorerCmd case skipped")
        return
    _, tui = _plugin_texts()
    source = _slice(tui, "function explorerCmd", "function waitPct")
    driver = source + r"""
function main() {
  console.log(JSON.stringify({
    linux: explorerCmd("/tmp/x", "linux"),
    mac: explorerCmd("/tmp/x", "darwin"),
    win: explorerCmd("C:/x", "win32"),
    fallback: explorerCmd("/tmp/x", "freebsd"),
    empty: explorerCmd("", "linux"),
  }))
}
main()
"""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    script = SCRATCH / "explorer_cmd.ts"
    script.write_text(driver)
    r = subprocess.run([node, str(script)], capture_output=True, text=True,
                       timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)
    assert out["linux"] == ["xdg-open", "/tmp/x"]
    assert out["mac"] == ["open", "/tmp/x"]
    assert out["win"] == ["explorer.exe", "C:/x"]
    assert out["fallback"] == ["xdg-open", "/tmp/x"]
    assert out["empty"] == ["xdg-open", ""], "never crashes on a missing path"
