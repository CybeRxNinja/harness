import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUN = shutil.which("bun")

# Loads the plugin exactly like opencode's plugin host does (default export +
# setup(ctx)) against a recording stub context, then exercises the executors.
SMOKE = r"""
const file = process.argv[2]
const mod: any = await import(file)
const plug = mod.default

const fail = (m: string): never => {
  throw new Error(m)
}
if (typeof plug !== "object" || plug === null) fail("default export is not an object")
if (typeof plug.id !== "string" || plug.id.length === 0) fail("default export has no id")
if (typeof plug.setup !== "function") fail("default export has no setup function")
if ("effect" in plug) fail("default export must use setup, not effect")

// The live server lists built-in skills whose `path` is a virtual /builtin/*.md
// that does not exist on disk — their body only exists in the inline `content`.
const BUILTIN = {
  id: "opencode",
  name: "OpenCode",
  description: "builtin skill",
  path: "/builtin/opencode.md",
  content: "# OpenCode\n\nBuiltin body.",
}

const skills: any[] = [BUILTIN]
const tools: any[] = []
const hooks: string[] = []
const condensed: any[] = []

const ctx: any = {
  location: { directory: process.cwd() },
  options: {},
  skill: {
    list: async () => skills,
    transform: async (cb: any) => {
      await cb({ list: () => skills, add: (s: any) => skills.push(s) })
    },
  },
  tool: {
    transform: async (cb: any) => {
      await cb({ add: (t: any) => tools.push(t) })
    },
    hook: async (name: string, fn: any) => {
      hooks.push(`tool:${name}`)
      const big = Array.from({ length: 400 }, (_, i) => `line ${i % 2} ${"x".repeat(40)}`)
      const ok = { status: "completed", result: { content: [{ type: "text", text: big.join("\n") }] } }
      await fn(ok)
      condensed.push(ok.result.content[0].text.length < big.join("\n").length)
      const err = "Traceback (most recent call last):\n" + "y".repeat(9000)
      const bad = { status: "error", result: { content: [{ type: "text", text: err }] } }
      await fn(bad)
      condensed.push(bad.result.content[0].text.length === err.length)
    },
  },
  session: {
    hook: async (name: string, fn: any) => {
      hooks.push(`session:${name}`)
      await fn({ sessionID: "ses_smoke", system: [] })
    },
  },
}

const returned = await plug.setup(ctx)
if (returned !== undefined && typeof returned !== "function") {
  fail(`setup returned ${typeof returned}; the host would call it as a cleanup function`)
}

// every registered tool must be callable and answer with a content string
const seeds = skills.filter((s: any) => s.id !== "opencode")
const execs: any = {}
for (const t of tools) {
  const input =
    t.name === "skill_view" ? { id: seeds[0]?.id ?? "missing" } : t.name === "memory_recall" ? { query: "smoke" } : {}
  // no sessionID on purpose: nothing here may write to a real project store
  const out = await t.execute(input, {})
  execs[t.name] = typeof out?.content
}

// skill_view must never surface a raw ENOENT: a virtual builtin path falls back
// to the inline content, and a missing reference file answers with text.
const sv = tools.find((t: any) => t.name === "skill_view")
const tryExec = async (input: any): Promise<any> => {
  try {
    const out = await sv.execute(input, {})
    return { ok: true, text: String(out?.content ?? "") }
  } catch (e: any) {
    return { ok: false, text: String(e?.message ?? e) }
  }
}
const views = {
  builtin: await tryExec({ id: "opencode" }),
  builtinRef: await tryExec({ id: "opencode", path: "references/nope.md" }),
  unknown: await tryExec({ id: "does-not-exist" }),
  noId: await tryExec({}),
}

console.log(
  JSON.stringify({
    id: plug.id,
    returned: returned === undefined ? null : "function",
    skills: seeds.map((s: any) => ({ id: s.id, path: typeof s.path, content: typeof s.content })),
    views,
    tools: tools.map((t: any) => ({
      name: t.name,
      input: t.input?.type,
      execute: typeof t.execute,
      direct: t.options?.codemode === false,
    })),
    execs,
    hooks,
    condensed,
  }),
)
"""


# Feature matrix: drives every registered feature against a stub ctx backed by a
# real .opencode/harness/sessions.db (facts) and a virtual builtin skill, then
# prints what each feature did.
FEATURES = r"""
import { mkdtempSync, mkdirSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { Database } from "bun:sqlite"

const file = process.argv[2]
const plug: any = (await import(file)).default

const BUILTIN = {
  id: "opencode",
  name: "OpenCode",
  description: "Built in. " + "word ".repeat(80),
  path: "/builtin/opencode.md",
  content: "BUILTIN BODY",
}

function makeCtx(dir: string, skillList?: any) {
  const skills: any[] = skillList === undefined ? [BUILTIN] : []
  const tools: any[] = []
  const hooks: any = {}
  const ctx: any = {
    location: { directory: dir },
    options: {},
    skill: {
      list: skillList ?? (async () => skills),
      transform: async (cb: any) => { await cb({ list: () => skills, add: (s: any) => skills.push(s) }) },
    },
    tool: {
      transform: async (cb: any) => { await cb({ add: (t: any) => tools.push(t) }) },
      hook: async (n: string, fn: any) => { hooks[`tool:${n}`] = fn },
    },
    session: { hook: async (n: string, fn: any) => { hooks[`session:${n}`] = fn } },
  }
  return { ctx, skills, tools, hooks }
}

function makeDb(dir: string, rows: string[][]) {
  mkdirSync(join(dir, ".opencode", "harness"), { recursive: true })
  const db = new Database(join(dir, ".opencode", "harness", "sessions.db"))
  db.run("CREATE TABLE facts(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, source TEXT, ts INTEGER)")
  rows.forEach(([text, source], i) => db.run("INSERT INTO facts(text,source,ts) VALUES(?,?,?)", text, source, i + 1))
  db.close()
}

const dir = mkdtempSync(join(tmpdir(), "harness-plugin-"))
makeDb(dir, [
  ["The plugin loader needs codemode false or the tool stays invisible", "chat"],
  ["unrelated fact about socks", "chat"],
])

const s = makeCtx(dir)
await plug.setup(s.ctx)
const tools: any = Object.fromEntries(s.tools.map((t: any) => [t.name, t]))
const call = async (name: string, input?: any, context?: any): Promise<string> => {
  try {
    const o = await tools[name].execute(input ?? {}, context ?? {})
    return String(o?.content ?? "")
  } catch (e: any) {
    return `THREW ${e?.message ?? e}`
  }
}

const out: any = {}
out.seedCount = s.skills.filter((x: any) => String(x.id).startsWith("harness-")).length
// activation is idempotent: a reload must not duplicate the seeds
const before = s.skills.length
await plug.setup(s.ctx)
out.seedIdempotent = s.skills.length === before && s.skills.length === out.seedCount + 1

out.listed = await call("skills_list")
out.firstLine = out.listed.split("\n")[0]
out.hasCount = /\(\d+ skills/.test(out.listed)

out.recallHit = await call("memory_recall", { query: "plugin loader" })
out.recallMiss = await call("memory_recall", { query: "zebra" })
out.recallNoTerms = await call("memory_recall", { query: "a b" })
out.recallEmpty = await call("memory_recall", {})
out.recallNull = await call("memory_recall", { query: null })

out.viewBuiltin = await call("skill_view", { id: "opencode" })
out.viewByname = await call("skill_view", { id: "OpenCode" })
out.viewNoId = await call("skill_view", {})
out.viewUnknown = await call("skill_view", { id: "nope" })
out.viewMissingRef = await call("skill_view", { id: "opencode", path: "references/x.md" })
out.viewTraversal = await call("skill_view", { id: "opencode", path: "../../../../etc/passwd" })

// skill.list() unusable -> both tools fall back to the bundled seeds on disk
const f = makeCtx(dir, async () => { throw new Error("boom") })
await plug.setup(f.ctx)
const ftools: any = Object.fromEntries(f.tools.map((t: any) => [t.name, t]))
out.seedList = await (async () => {
  try { return String((await ftools.skills_list.execute({}, {})).content) } catch (e: any) { return `THREW ${e?.message ?? e}` }
})()
out.seedView = await (async () => {
  try { return String((await ftools.skill_view.execute({ id: "harness-using-agent-skills" }, {})).content).slice(0, 40) } catch (e: any) { return `THREW ${e?.message ?? e}` }
})()

// condensing: oversized success shrinks, errors and small bodies pass through
const cond = s.hooks["tool:execute.after"]
const mk = (text: string, status = "completed") => ({ status, result: { content: [{ type: "text", text }] } })
const big = Array.from({ length: 300 }, (_, i) => `row ${i} ${"y".repeat(30)}`).join("\n")
const ok = mk(big)
await cond(ok)
out.condenseShrank = ok.result.content[0].text.length < big.length
out.condenseMarker = ok.result.content[0].text.slice(-130)
out.condenseKeepsHeadTail = ok.result.content[0].text.startsWith("row 0 ") && ok.result.content[0].text.includes("row 299 ")
const err = mk(`${big}\nTraceback (most recent call last):`)
await cond(err)
out.condenseKeepsError = err.result.content[0].text === `${big}\nTraceback (most recent call last):`
const small = mk("tiny")
await cond(small)
out.condenseKeepsSmall = small.result.content[0].text === "tiny"
const errored = mk(big, "error")
await cond(errored)
out.condenseKeepsErrorStatus = errored.result.content[0].text === big
let threw: string | null = null
for (const e of [undefined, null, {}, { status: "completed" }, { status: "completed", result: {} }, { status: "completed", result: { content: "x" } }]) {
  try { await cond(e) } catch (x: any) { threw = String(x?.message ?? x) }
}
out.condenseTolerant = threw

// compaction brief: newest durable facts ride into the compaction system prompt
const comp = s.hooks["session:compaction"]
const ev: any = { sessionID: "ses_abcdef123456", model: {}, system: [], messages: [] }
await comp(ev)
out.briefCount = ev.system.length
out.brief = String(ev.system[0]?.text ?? "")
await comp(ev)
out.briefIdempotent = ev.system.length
for (const bad of [undefined, null, {}, { sessionID: "ses_x", system: "nope" }]) {
  try { await comp(bad) } catch (e: any) { out.briefThrew = String(e?.message ?? e) }
}
out.briefThrew = out.briefThrew ?? null

// ---- the todo space: opencode 2.x ships no todo tool, so the plan lives in the
// HARNESS store (the table the sidebar panel reads) instead of a tool-private
// copy that nothing else can see. ----
const TODO_SID = "ses_todo_smoke"
const tctx = { sessionID: TODO_SID }
out.todoNoSession = await call("todowrite", { todos: [{ content: "x", status: "pending" }] })
out.todoWrite = await call("todowrite", { todos: [
  { content: "first thing", status: "completed" },
  { content: "second thing", status: "in_progress" },
  { content: "third thing", status: "pending" },
] }, tctx)
out.todoRead = await call("todoread", {}, tctx)
out.todoReadEmpty = await call("todoread", {}, { sessionID: "ses_no_todos_yet" })
out.todoGarbage = await call("todowrite", { todos: ["nope", null, { status: "pending" }] }, tctx)
// the write REPLACES the list, drops blank rows, and ignores a stranger's session
out.todoReplace = await (async () => {
  await tools.todowrite.execute({ todos: [
    { content: "only this", status: "pending" },
    { content: "   ", status: "pending" },
  ] }, tctx)
  await call("todowrite", { todos: [{ content: "other session row", status: "pending" }] }, { sessionID: "ses_other" })
  return call("todoread", {}, tctx)
})()
// rows really are in the harness store, keyed by the opencode session id
{
  const tdb = new Database(join(dir, ".opencode", "harness", "sessions.db"))
  out.todoRows = tdb.query("SELECT session, text, status FROM todos ORDER BY id").all()
  tdb.close()
}
// open todos ride into the compaction brief: a compaction destroys the
// transcript, and a plan that only lives in the transcript goes with it
{
  const evT: any = { sessionID: TODO_SID, system: [] }
  await s.hooks["session:compaction"](evT)
  out.briefWithTodos = String(evT.system[0]?.text ?? "")
}
// a todo tool with no session at all must answer, not write or throw
out.todoBare = await call("todoread", {})

// no facts yet -> no brief, no throw
const emptyDir = mkdtempSync(join(tmpdir(), "harness-plugin-empty-"))
const e2 = makeCtx(emptyDir)
await plug.setup(e2.ctx)
const ev2: any = { sessionID: "ses_none", system: [] }
await e2.hooks["session:compaction"](ev2)
out.briefWithoutFacts = ev2.system.length

// ---- future-host degradation: every ctx.* API may be renamed/removed by an
// opencode upgrade. setup must NEVER throw, and each loss must cost only its
// own feature. ----
const baseCtx = (over: any) => {
  const s = makeCtx(dir)
  const merged: any = { ...s.ctx, ...over }
  return { s, ctx: merged }
}
const setupOk = async (ctx: any): Promise<boolean> => {
  try { await plug.setup(ctx); return true } catch { return false }
}

// missing skill.transform: tools + hooks still register
{
  const { s, ctx } = baseCtx({ skill: { list: async () => [] } })
  out.degradeNoSkillTransform = await setupOk(ctx)
  out.degradeNoSkillTransformTools = s.tools.length
  out.degradeNoSkillTransformHooks = Object.keys(s.hooks).length
}
// missing tool.transform: seeding + hooks still register
{
  const { s, ctx } = baseCtx({ tool: { hook: async (n: string, fn: any) => { s.hooks[`tool:${n}`] = fn } } })
  out.degradeNoToolTransform = await setupOk(ctx)
  out.degradeNoToolTransformSeeds = s.skills.filter((x: any) => String(x.id).startsWith("harness-")).length
  out.degradeNoToolTransformHooks = Object.keys(s.hooks).length
}
// missing tool.hook: seeding + tools still register, no condense hook
{
  const { s, ctx } = baseCtx({})
  ctx.tool = { transform: async (cb: any) => { await cb({ add: (t: any) => s.tools.push(t) }) } }
  out.degradeNoToolHook = await setupOk(ctx)
  out.degradeNoToolHookTools = s.tools.length
  out.degradeNoToolHookCondense = "tool:execute.after" in s.hooks
}
// missing session.hook: seeding + tools still register, no brief hook
{
  const { s, ctx } = baseCtx({ session: {} })
  out.degradeNoSessionHook = await setupOk(ctx)
  out.degradeNoSessionHookTools = s.tools.length
  out.degradeNoSessionHookBrief = "session:compaction" in s.hooks
}
// one editor.add failing must not kill the other two tools
{
  const { s, ctx } = baseCtx({})
  ctx.tool = { transform: async (cb: any) => {
    await cb({ add: (t: any) => { if (t.name === "skill_view") throw new Error("host rejects skill_view"); s.tools.push(t) } })
  }, hook: async () => {} }
  out.degradePartialAdd = await setupOk(ctx)
  out.degradePartialAddTools = s.tools.map((t: any) => t.name).sort()
}
// editor.list unusable (missing / non-array): seeds still added, no dedupe, no throw
{
  const { s, ctx } = baseCtx({})
  ctx.skill = {
    list: async () => [],
    transform: async (cb: any) => { await cb({ add: (x: any) => s.skills.push(x) }) },
  }
  out.degradeNoList = await setupOk(ctx)
  out.degradeNoListSeeds = s.skills.filter((x: any) => String(x.id).startsWith("harness-")).length
}
// facts DB exists but with a drifted schema (no `facts` table): recall answers,
// nothing throws, no brief
{
  const drift = mkdtempSync(join(tmpdir(), "harness-plugin-drift-"))
  mkdirSync(join(drift, ".opencode", "harness"), { recursive: true })
  const db = new Database(join(drift, ".opencode", "harness", "sessions.db"))
  db.run("CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT)")
  db.close()
  const d3 = makeCtx(drift)
  await plug.setup(d3.ctx)
  const dt: any = Object.fromEntries(d3.tools.map((t: any) => [t.name, t]))
  out.degradeSchemaDrift = await (async () => {
    try { return String((await dt.memory_recall.execute({ query: "anything" }, {})).content) } catch (e: any) { return `THREW ${e?.message ?? e}` }
  })()
  const ev3: any = { sessionID: "ses_drift", system: [] }
  await d3.hooks["session:compaction"](ev3)
  out.degradeSchemaDriftBrief = ev3.system.length
}

console.log(JSON.stringify(out))
"""


def test_plugin_release_selection():
    """`latest` must mean newest `plugin-v*`, not whatever GitHub calls latest.

    The repo also publishes TUI binary releases (`tui-v*`) with no plugin
    assets; `releases/latest` follows publication order, so blindly trusting it
    makes `--from-release latest` resolve to a release that has no plugin files.
    """
    from harness.cli import (_pick_plugin_release, _plugin_asset_urls,
                             _plugin_release_tag, _tag_version)

    rels = [
        {"tag_name": "tui-v0.25", "published_at": "2026-09-21T00:00:00Z", "assets": []},
        {"tag_name": "plugin-v0.2", "assets": []},
        {"tag_name": "plugin-v0.9", "assets": []},
        {"tag_name": "plugin-v0.10", "assets": []},
        {"tag_name": "plugin-v0.11", "draft": True, "assets": []},
    ]
    assert _pick_plugin_release(rels)["tag_name"] == "plugin-v0.10"
    assert _pick_plugin_release([{"tag_name": "tui-v0.25", "assets": []}]) is None
    assert _tag_version("plugin-v0.10") > _tag_version("plugin-v0.9")

    # asset names: current release ships server.ts + tui.tsx; releases from
    # before the TUI entrypoint shipped harness.ts alone
    assert _plugin_asset_urls({"server.ts": "s", "tui.tsx": "t"}) == ("s", "t")
    assert _plugin_asset_urls({"harness.ts": "s", "tui.ts": "t"}) == ("s", "t")
    assert _plugin_asset_urls({}) == (None, None)

    # tag spellings users type by hand
    assert _plugin_release_tag("latest") is None and _plugin_release_tag("") is None
    for spelling in ("0.3", "v0.3", "plugin-v0.3"):
        assert _plugin_release_tag(spelling) == "plugin-v0.3"


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _fake_release_http(monkeypatch, rel: dict, bodies: dict) -> list:
    """Serve the release list / tag lookup + asset downloads from memory."""
    import urllib.request
    seen: list[str] = []

    def fake_urlopen(url, timeout=None):
        url = str(url)
        seen.append(url)
        if "/releases?" in url:
            return _FakeResp(json.dumps([rel]).encode())
        if "/releases/latest" in url or "/releases/tags/" in url:
            return _FakeResp(json.dumps(rel).encode())
        for name, body in bodies.items():
            if url.endswith(name):
                return _FakeResp(body)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def test_plugin_download_writes_both_entrypoints(tmp_path, monkeypatch):
    """The release install must land server.ts AND tui.tsx.

    tui.tsx is what sets features.tui; a server-only install is exactly the
    "harness only shows under Server in the Plugins panel" symptom.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    rel = {"tag_name": "plugin-v9.9", "assets": [
        {"name": "server.ts", "browser_download_url": "https://x/server.ts"},
        {"name": "tui.tsx", "browser_download_url": "https://x/tui.tsx"},
    ]}
    seen = _fake_release_http(monkeypatch, rel, {
        "server.ts": b"// server entrypoint",
        "tui.tsx": b"// tui entrypoint",
    })
    from harness.cli import download_plugin
    dest = download_plugin("latest")
    assert (dest / "server.ts").read_text() == "// server entrypoint"
    assert (dest / "tui.tsx").read_text() == "// tui entrypoint"
    assert dest.name == "harness" and dest.parent.name == "plugins"
    assert any("/releases?" in u for u in seen), "latest must list releases, not trust /latest"


def test_plugin_download_legacy_release_and_missing_tui(tmp_path, monkeypatch, capsys):
    """An old release (harness.ts only) installs the server entrypoint and says
    plainly that the TUI entrypoint is missing instead of failing silently."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    rel = {"tag_name": "plugin-v0.2", "assets": [
        {"name": "harness.ts", "browser_download_url": "https://x/harness.ts"},
    ]}
    _fake_release_http(monkeypatch, rel, {"harness.ts": b"// legacy server"})
    from harness.cli import download_plugin
    dest = download_plugin("0.2")
    assert (dest / "server.ts").read_text() == "// legacy server"
    assert not (dest / "tui.tsx").exists()
    assert "no tui.tsx" in capsys.readouterr().err


def test_plugin_download_rejects_assetless_release(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    rel = {"tag_name": "plugin-v9.9", "assets": [
        {"name": "Harness_TUI-x86_64.AppImage", "browser_download_url": "https://x/app"},
    ]}
    _fake_release_http(monkeypatch, rel, {})
    from harness.cli import download_plugin
    with pytest.raises(ValueError, match="no server.ts/harness.ts asset"):
        download_plugin("latest")


def test_plugin_files_exist():
    from harness.cli import _plugin_files
    files = _plugin_files()
    assert len(files) == 2 and all(Path(f).exists() for f in files)
    text = Path(files[0]).read_text()
    # Loader contract lock (opencode v2.0.8 PluginModule.load): the default
    # export must be an object with id + (effect | setup). `setup(ctx)` may
    # return a cleanup function or nothing — the host calls any other returned
    # value as a cleanup function, which kills plugin activation
    # ("TypeError: … is not a function"), leaves the plugin invisible under
    # /plugins and stalls request handling that waits on plugin activation.
    assert "export default HarnessPlugin" in text
    assert 'id: "harness"' in text
    assert "setup(ctx" in text and "effect:" not in text
    # v1 hook objects are NOT read in v2: tools, skills and hooks must go
    # through the ctx transforms/hooks.
    assert "ctx.tool.transform" in text and "ctx.skill.transform" in text
    assert "codemode: false" in text, "tools must be direct (codemode:false), not Code-Mode-only"
    assert 'ctx.tool.hook("execute.after"' in text
    assert 'ctx.session.hook("compaction"' in text
    assert '"tool.execute.after"' not in text
    assert '"experimental.session.compacting"' not in text
    # No static imports: the server resolves plugin specifiers before Bun
    # strips types, so `import type … from "@opencode-ai/plugin"` fails the
    # load with ResolveMessage (err_51f4c6d8). Dynamic node:/bun: imports and
    # Bun globals are fine.
    assert not re.search(r"^\s*import\s+(type\s+)?[{\*\w]", text, re.M), "plugin must not use static imports"
    # TUI entrypoint (tui.tsx): its presence is what makes the TUI plugin list
    # include harness (features.tui is set only for plugins with a tui
    # entrypoint; a bare server.ts shows only under the panel's Server section).
    tui = Path(files[1]).read_text()
    assert 'id: "harness"' in tui and "setup(ctx" in tui
    assert 'ui.slot' in tui, "TUI plugin must register a slot"
    assert not re.search(r"^\s*import\s+(type\s+)?[{\*\w]", tui, re.M), "TUI plugin must not use static imports"


def test_tui_plugin_is_a_sidebar_panel_not_a_layout_change():
    """The TUI entrypoint fills opencode's EXISTING sidebar with the stats panel.

    Contract notes these lock (all probed live against opencode v2.0.8):
      * `sidebar.content` renders only plugin contributions — an install that
        registers nothing there leaves the sidebar empty.
      * `keymap.layer` throws "Keymap.Provider is missing" unless it is called
        from inside a slot's render component, so commands register from the
        `app` slot.
      * Data stores are lazy: `sync()` must be called before `list()` yields
        anything, and session stats come from `data.session.get(sid)`.
      * state comes from `ctx.storage.memory` (a reactive store); importing
        solid-js would load a second instance and break reactivity.
      * `require` and `Bun` are NOT defined in the TUI plugin scope, so file and
        sqlite access must go through dynamic `import()`.
    """
    from harness.cli import _plugin_files
    tui = Path(_plugin_files()[1]).read_text()

    # the panel lives in the sidebar the TUI already has
    assert 'append: "sidebar.content"' in tui
    assert 'append: "sidebar.footer"' in tui

    # and nothing here rearranges opencode: no docked session panel (host-owned
    # overlay), no routes, no slot replacement
    assert "session.panel" not in tui, "the stats panel must not dock an overlay"
    assert "router.register" not in tui, "no custom routes"
    assert not re.search(r"\breplace:\s*\"", tui), "append only; never take over a slot"

    # footer chip: headline stats, click toggles the sidebar it lives in
    assert 'append: "home.footer.status"' in tui
    assert "onMouseDown" in tui
    assert 'dispatch?.("session.sidebar.toggle")' in tui

    # commands: palette + slash, registered from a slot (keymap needs the Provider)
    assert "keymap?.layer" in tui and 'bind: "ctrl+g"' in tui
    assert 'slash: { name: "harness", aliases: ["hp"] }' in tui
    assert 'slash: { name: "harness-refresh", aliases: ["hr"] }' in tui

    # the stat rows, all of them — and deliberately NO "Context" row: opencode's
    # own sidebar already renders one, and a second copy is what made the panel
    # look like a foreign overlay rather than part of the app
    for row in ("window", "tokens", "models", "todo", "workers", "skills", "agents", "memory"):
        assert f'id="{row}"' in tui, row
    assert 'label="Context"' not in tui, "opencode already renders Context"

    # several rows can be open at once (`open` is a list of row ids), and a
    # click toggles exactly the row it landed on
    assert "const isOpen = (name: string) => (Array.isArray(view.open) ? view.open : []).includes(name)" in tui
    assert "if (open.has(props.id)) open.delete(props.id)" in tui
    assert "d.open = [...open]" in tui
    assert 'view.open === name' not in tui, "expansion is no longer single-select"
    # every detail closure must read a tracked value (see the Row docstring)
    assert tui.count("void view.rev") >= 8, tui.count("void view.rev")

    # native row geometry, copied from opencode's own sidebar rows: the label
    # takes flexGrow and the value flexShrink 0, so the value pins to the right
    # edge at any panel width without a hard-coded width constant
    assert 'flexGrow={1} wrapMode="none" truncate' in tui
    assert 'flexShrink={0} wrapMode="none"' in tui
    # theme keys opencode actually exposes (text.base/muted/action/feedback);
    # text.default/text.subdued are from a different version and resolve to
    # undefined, which renders rows in a fallback colour — i.e. "foreign"
    assert "theme?.text?.base" in tui and "theme?.text?.muted" in tui
    assert "theme?.text?.default" not in tui and "theme?.text?.subdued" not in tui
    assert "theme?.text?.feedback?.warning" in tui

    # the context window comes from the provider's model limits
    assert "provEntry?.models?.[modelName]" in tui and "limit?.context" in tui

    # stat sources: session tokens/cost, messages for the model step rows, the
    # lazily-synced location stores, todos from the HARNESS space, facts from ours
    assert "data_?.session?.get?.(sessionID)" in tui
    assert "data_?.session?.message?.list?.(sessionID)" in tui
    for store in ("model", "provider", "agent", "skill"):
        assert f"store?.{store}?.sync?.(loc)" in tui and f"store?.{store}?.list?.(loc)" in tui, store
    assert "SELECT text, status FROM todos WHERE session = ?" in tui
    assert "FROM todo WHERE session_id" not in tui, "opencode 2.x writes no todos"
    assert "SELECT text FROM facts ORDER BY id DESC" in tui
    assert 'await import("node:fs")' in tui and 'await import("bun:sqlite")' in tui

    # reactive view state without a signal import, and a cleanup function
    assert "storage?.memory" in tui
    assert "return () => {" in tui
    assert not re.search(r"^\s*import\s+(type\s+)?[{\*\w]", tui, re.M), "TUI plugin must not use static imports"


def test_tui_panel_counts_and_repaints():
    """Three bugs found by reading the rendered screen:

    1. The header counted every skill in the store (13, including opencode's
       builtins) above the 11 harness rows actually listed — so the count is
       filtered to harness-* at load time.
    2. The stats rows live in a plain object, so a store update that changed no
       tracked value did not repaint the panel: it kept the snapshot painted
       before the first load ("no tokens reported yet") even after the data
       arrived. A revision counter the render reads fixes that.
    3. The in-flight placeholder read the plain `loading` guard from inside a
       tracked expression, so it reported whatever that variable held at the
       last repaint — leaving "scanning…" on screen next to loaded stats. The
       placeholder is now driven by a reactive `scanning` flag that is cleared
       in the same update that bumps `rev`.
    """
    from harness.cli import _plugin_files
    tui = Path(_plugin_files()[1]).read_text()
    assert "s.startsWith(HARNESS_PREFIX)" in tui, "count harness skills only"
    assert "d.rev = Number(d.rev ?? 0) + 1" in tui, "loads must notify the store"
    assert "view.rev === 0 || loading" not in tui, (
        "the placeholder must not read the non-reactive loading guard"
    )
    assert "{view.scanning ?" in tui
    assert "d.scanning = true" in tui and "d.scanning = false" in tui, "cleared per load"
    # the slow sources (location-store syncs, the message walk, the context
    # limit) must not re-run on the 8s poll: a blocking provider.sync() every
    # few seconds is what kept the placeholder on screen
    assert "const scanSlow = async (loc: any, data_: Rec)" in tui
    assert "if (full || !scanned) {" in tui and "await settle(scanSlow(loc, data_), SCAN_MS)" in tui
    assert "void load(true)" in tui and "await load(true)" in tui
    # the poll stands down when nothing has been drawn: the same entrypoint is
    # loaded in the long-lived server process, where no slot ever renders
    assert "lastRender === 0 || Date.now() - lastRender > POLL_MS * 4" in tui
    assert tui.count("rendered(props)") == 3, "every slot render marks the panel on-screen"
    # skill rows drop the shared namespace — the sidebar is 46 columns wide and
    # "harness-" repeats on every one of the 11 rows
    assert 'replace(/^harness-/, "")' in tui
    assert "▪ ${shortSkill(s)}" in tui
    # Solid re-runs tracked JSX expressions, not the render body: detail rows must
    # be built inside the JSX expression, or they freeze at the first paint
    # a truncated list always says how many more exist: "11 available" over
    # five rows was the count/list mismatch users called inaccurate
    assert "rows: string[] = all.length ? all.slice(0, DETAIL_LIMIT) : [props.empty]" in tui
    assert "if (all.length > DETAIL_LIMIT) rows.push(`… +${all.length - DETAIL_LIMIT} more`)" in tui
    # a failed load surfaces in the panel (cli-side console.error is not logged)
    assert 'd.note = notes[0] ?? ""' in tui
    assert "cut(view.note, 40)" in tui


def test_tui_panel_never_wedges_and_reads_like_opencode():
    """Driving the real TUI on 2.0.11 showed the panel stuck on
    `harness · no session` with every stat at zero and `scanning…` on screen at
    the end of a 26s run — while a live probe proved `sidebar.content` DOES
    receive the active `sessionID` and `data.session.get(sid)` returns real
    tokens. The wedge was the load mutex: the setup-time load (no session id
    yet) was still in flight inside a store `sync()`, so the load that the first
    slot render triggered returned early and the panel never got a second turn.

    Contracts that keep that from coming back — plus the ones that stop the
    panel from reading as a foreign overlay rather than part of opencode.
    """
    from harness.cli import _plugin_files
    tui = Path(_plugin_files()[1]).read_text()

    # 1. every scan is time-boxed, so the panel always settles
    assert "const SCAN_MS" in tui
    assert "function settle(p: any, ms: number)" in tui
    assert "await settle(scanSlow(loc, data_), SCAN_MS)" in tui
    for store in ("model", "provider", "agent", "skill"):
        assert f"settle(store?.{store}?.sync?.(loc), SCAN_MS)" in tui, store
    assert "settle(data_?.session?.sync?.(sessionID), SCAN_MS)" in tui

    # 2. a `full` load requested while one is in flight is queued, not dropped
    assert "if (loading) {" in tui and "pendingFull = pendingFull || full" in tui
    assert "if (pendingFull) {" in tui and "pendingFull = false" in tui

    # 3. the header never names a missing session: the id is shown or omitted
    assert 'view.sessionID || "no session"' not in tui
    assert 'view.sessionID || ""' in tui

    # 4. opencode's own numbers, so the panel agrees with the app's readout:
    #    total tokens = input+output+reasoning+cache read+write, cost from the
    #    session store's cost accessor rather than a re-derived sum
    assert "function totalTokens(tokens: Rec | null): number" in tui
    for field in ("input", "output", "reasoning"):
        assert f"Number(tokens.{field} ?? 0)" in tui, field
    assert "Number(cache.read ?? 0)" in tui and "Number(cache.write ?? 0)" in tui
    assert "data_?.session?.cost?.(sessionID)" in tui

    # 5. a usage bar must never read as "unused": 1% of 8 cells rounds to zero
    assert "if (clamped > 0 && filled < 1) filled = 1" in tui

    # 6. the in-flight marker is a glyph in the header, not a sentence that can
    #    be left behind by a load that never finishes
    assert "scanning skills · memory · todos" not in tui
    assert '{view.scanning ?' in tui

    # 7. a load must ASK for a repaint. The rows come from a plugin store and
    #    from plain reads, so nothing tells opencode to redraw the sidebar: on a
    #    real session the store updates landed (rev 0 -> 4, session id, 14
    #    skills) while the screen kept its all-zero first paint. Verified fixed
    #    by reading the painted text out of the pty stream, which then contained
    #    `harness ses_… · 11 available` and `Tokens 11.8k · $0.0000`.
    assert "const repaint = () =>" in tui
    assert "r?.requestRender" in tui and 'typeof fn === "function"' in tui
    assert "setTimeout(() => {" in tui, "the repaint request must be deferred"
    # every state write repaints (that is the only path a load's data takes out)
    assert "      repaint()\n    }" in tui, "patch() must request the repaint"


def test_tui_models_rows_are_computed_on_every_load():
    """The Models row was built only inside the once-per-session slow scan, from
    a message store that is EMPTY until its own sync lands — so it sat at
    "0 used" for a whole session unless the session changed or the user ran
    /harness-refresh, and the detail rows claimed "1 step" regardless.

    The message walk is a local read, so it runs on every load; the store syncs
    stay on the slow half; and one nudge per session picks the rows up with the
    session instead of up to POLL_MS later.
    """
    from harness.cli import _plugin_files
    tui = Path(_plugin_files()[1]).read_text()

    assert "const scanModels = (data_: Rec): void => {" in tui
    assert "scanSlow = async (loc: any, data_: Rec)" in tui
    assert "        scanModels(data_)\n" in tui, "the walk must run on every load"
    # ...and outside the slow gate, not behind its `scanned` flag
    gate = tui.index("if (full || !scanned) {")
    assert "scanModels" not in tui[gate:tui.index("\n        }\n", gate)]
    assert "warmedFor = sessionID" in tui and "setTimeout(() => void load(), 500)" in tui
    # real step counts, not a hard-coded "1 step", aligned in columns that fit
    # the 32-cell detail budget: model left, steps right
    assert 'e.steps === 1 ? "" : "s"' in tui
    assert "`${cut(name, 18).padEnd(18)}${e.steps} step" in tui
    # a session that has sent nothing still shows the selected model — as a
    # "fallback" row that is NOT counted as usage ("1 used" before the first
    # message was the old lie)
    assert "· selected`" in tui
    assert 'kind: "fallback"' in tui
    # provider lines are plain grouping headers: the catalog count ("kilo ·
    # 392 models") was inventory trivia and its source shape drifts (a
    # Provider.Info row has no models map on 2.0.14), so it is gone entirely
    assert "modelList" not in tui
    assert "`${cut(prov, 20)}:`" in tui
    # the value counts providers the session ROUTED through, not the ones
    # installed ("3 used · 6 providers" was the wrong data users saw)
    assert "data.provsUsed = byProv.size" in tui
    assert "d.providers = data.provsUsed" in tui
    assert "provEntries" not in tui


def test_tui_workers_row_shows_the_rlm_pool():
    """The RLM pool is project state, so it belongs in the panel next to the
    todos: the row reports how many workers are unfinished right now and lists
    the newest rows. The COUNT comes from SQL rather than the displayed window
    (a long worker can fall outside the newest rows while short ones finish),
    and the row deliberately does NOT re-derive `stale` — `harness doctor` owns
    that rule (`budgets.worker_timeout_s` × 2) and a second copy would drift, so
    the age is printed instead (`◐ build:panel · running 42m`).
    """
    from harness.cli import _plugin_files
    tui = Path(_plugin_files()[1]).read_text()

    # same DB as the todos/facts, one read-only open for all three
    assert "const readProjectState = async (directory: string, sid: string)" in tui
    assert tui.count("await import(\"bun:sqlite\")") == 1, "one connection for the state DB"
    assert "SELECT name, status, updated FROM workers ORDER BY rowid DESC LIMIT ?" in tui
    assert "SELECT count(*) AS n FROM workers WHERE status IN ('queued','running')" in tui

    # the pool's own vocabulary and the honest age, in epoch seconds
    assert 'done: "●", running: "◐", queued: "○", error: "✕", timeout: "!", stale: "~"' in tui
    assert 'const WORKER_LIVE = new Set(["queued", "running"])' in tui
    assert "function age(updated: unknown): string" in tui
    assert "/ 1000" in tui, "rlm writes epoch seconds, not milliseconds"
    assert "STALE_MS" not in tui and '= "stale"' not in tui, "the stale rule stays doctor's"

    # the value is the live occupancy of a pool that EXISTS; an empty pool
    # hides the row (a permanent "Workers none" was panel clutter, not state)
    assert 'view.workersActive ? `${view.workersActive} active` : "idle"' in tui
    assert "return !view.workers" in tui
    assert '(no workers in this project)' in tui


def test_tui_todo_row_reads_the_harness_space_and_auto_expands():
    """The Todo row is backed by the HARNESS todo space — the table the server
    half's `todowrite` writes — not by opencode's `todo` table, which nothing
    past 2.0.13 writes (the row was permanently empty). The row also opens
    itself when the list appears or changes, and a manual collapse sticks until
    the list changes again.
    """
    from harness.cli import _plugin_files
    tui = Path(_plugin_files()[1]).read_text()

    # this session's list, else the newest list in the project (the plan the
    # project is actually following) — both from the harness store
    assert "SELECT text, status FROM todos WHERE session = ?" in tui
    assert "SELECT text, status FROM todos ORDER BY id DESC LIMIT ?" in tui
    assert "stateDb(directory)" in tui
    assert "opencode.db" not in tui, "the panel must not read opencode's dead todo table"
    # the glyphs match what todowrite reports back
    assert 'completed: "●", in_progress: "◐", cancelled: "✕", pending: "○"' in tui

    # a fallback list is labelled as the project's, never passed off as this
    # session's
    assert "out.todosFallback = !mine.length && rows.length > 0" in tui
    assert 'view.todosProject ? " · project" : ""' in tui

    # auto-expand on a new/changed list, and only then (a manual collapse lasts)
    assert "if (todoSig !== (d.todoSig ?? \"\")) {" in tui
    assert "d.todoSig = todoSig" in tui
    assert 'open.add("todo")' in tui

    # several rows may be open, so the footer reports how many
    assert "`${openCount()} expanded`" in tui


# Parses each entrypoint with Bun's transpiler (JSX-aware, no module
# resolution). A broken tui.tsx is otherwise only visible as a WARN in
# opencode's log ("plugin operation failed … stage=read") with nothing in the
# UI — the plugin simply does not load and the sidebar/panel stay empty.
TRANSPILE = r"""
const results: any[] = []
for (const p of process.argv.slice(2)) {
  try {
    const code = await Bun.file(p).text()
    const js = new Bun.Transpiler({ loader: "tsx" }).transformSync(code)
    results.push({ file: p, ok: true, bytes: js.length, error: null })
  } catch (e: any) {
    results.push({ file: p, ok: false, bytes: 0, error: String(e?.message ?? e) })
  }
}
console.log(JSON.stringify(results))
"""


@pytest.mark.skipif(BUN is None, reason="bun is not installed")
def test_plugin_entrypoints_parse(tmp_path):
    """Both entrypoints must parse: the TUI compiles tui.tsx with its own
    pipeline, so a syntax error takes the whole TUI half of the plugin down."""
    from harness.cli import _plugin_files
    script = tmp_path / "transpile.ts"
    script.write_text(TRANSPILE)
    r = subprocess.run([BUN, "run", str(script), *_plugin_files()],
                       capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert len(out) == 2, out
    for entry in out:
        assert entry["ok"], f"{entry['file']} failed to parse: {entry['error']}"
        assert entry["bytes"] > 500, entry


@pytest.mark.skipif(BUN is None, reason="bun is not installed")
def test_plugin_features(tmp_path):
    from harness.cli import _plugin_files
    script = tmp_path / "features.ts"
    script.write_text(FEATURES)
    # HOME is pointed at tmp so nothing here can reach the developer's real
    # fact DB (facts come from <project>/.opencode/harness/sessions.db only)
    env = {**os.environ, "HOME": str(tmp_path)}
    r = subprocess.run([BUN, "run", str(script), _plugin_files()[0]],
                       capture_output=True, text=True, timeout=180, cwd=str(REPO_ROOT), env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])

    # seeding registers every bundled skill once, and reload does not duplicate
    assert out["seedCount"] >= 11, out["seedCount"]
    assert out["seedIdempotent"], (out["seedCount"], out["seedIdempotent"])

    # skills_list: word-boundary descriptions, count, builtin + seeds
    assert out["firstLine"].startswith("- opencode: Built in."), out["firstLine"]
    assert "…" in out["firstLine"] and len(out["firstLine"]) < 260, out["firstLine"]
    assert out["hasCount"], out["listed"][-80:]

    # memory_recall: matches keyword facts, never dumps unrelated rows
    assert "codemode false" in out["recallHit"], out["recallHit"]
    assert "socks" not in out["recallHit"], out["recallHit"]
    for key in ("recallMiss", "recallNoTerms", "recallEmpty", "recallNull"):
        assert out[key] == "(nothing recalled — verify from the transcript instead)", (key, out[key])

    # skill_view: virtual builtin bodies, both id and name, clear misses
    assert out["viewBuiltin"] == "BUILTIN BODY"
    assert out["viewByname"] == "BUILTIN BODY"
    assert "needs an id" in out["viewNoId"]
    assert out["viewUnknown"] == "skill not found: nope"
    assert out["viewMissingRef"].startswith("not found: references/x.md")
    assert "path escapes" in out["viewTraversal"], out["viewTraversal"]

    # seeding + reads still work when the server's skill list is unusable
    assert out["seedList"].count("harness-") >= 11 and out["hasCount"], out["seedList"][:120]
    assert out["seedView"].startswith("---\nname: using-agent-skills"), out["seedView"]

    # condensing
    assert out["condenseShrank"] and out["condenseKeepsHeadTail"]
    assert "harness: condensed" in out["condenseMarker"], out["condenseMarker"]
    assert out["condenseKeepsError"] and out["condenseKeepsSmall"] and out["condenseKeepsErrorStatus"]
    assert out["condenseTolerant"] is None, out["condenseTolerant"]

    # compaction brief: newest facts first, idempotent, tolerant, silent with no facts
    assert out["briefCount"] == 1, out["briefCount"]
    assert out["brief"].startswith("Harness memory brief"), out["brief"][:120]
    assert out["brief"].index("socks") < out["brief"].index("codemode"), out["brief"]
    assert out["briefIdempotent"] == 1
    assert out["briefThrew"] is None, out["briefThrew"]
    assert out["briefWithoutFacts"] == 0

    # the todo space opencode 2.x lacks: todowrite/todoread persist into the
    # harness store under the OPENCODE session id, and the write replaces the
    # list rather than appending to it
    assert out["todoNoSession"].startswith("todowrite needs an active session"), out["todoNoSession"]
    assert "3 todos in this session" in out["todoWrite"], out["todoWrite"]
    assert out["todoRead"] == "● first thing\n◐ second thing\n○ third thing", out["todoRead"]
    assert out["todoReadEmpty"].startswith("(no todos in this session"), out["todoReadEmpty"]
    assert out["todoBare"].startswith("(no todos in this session"), out["todoBare"]
    # junk input (strings, nulls, missing content) writes nothing and never throws
    assert "0 todos in this session" in out["todoGarbage"], out["todoGarbage"]
    assert out["todoReplace"] == "○ only this", out["todoReplace"]
    assert out["todoRows"] == [
        {"session": "ses_todo_smoke", "text": "only this", "status": "pending"},
        {"session": "ses_other", "text": "other session row", "status": "pending"},
    ], out["todoRows"]
    # open todos ride into the compaction brief, completed ones do not, and the
    # facts are still there alongside them
    assert "Harness memory brief" in out["briefWithTodos"], out["briefWithTodos"]
    assert "Open todos (harness todo space):" in out["briefWithTodos"], out["briefWithTodos"]
    assert "only this" in out["briefWithTodos"], out["briefWithTodos"]
    assert "first thing" not in out["briefWithTodos"], out["briefWithTodos"]
    assert "codemode false" in out["briefWithTodos"], out["briefWithTodos"]

    # future-host degradation: an opencode upgrade that renames/removes a ctx
    # API costs only its own feature — activation never throws, tools that do
    # not depend on the lost API still register, one bad editor.add does not
    # sink the other tools, and a drifted DB schema degrades to an answer.
    assert out["degradeNoSkillTransform"] is True
    assert out["degradeNoSkillTransformTools"] == 5 and out["degradeNoSkillTransformHooks"] == 2
    assert out["degradeNoToolTransform"] is True
    assert out["degradeNoToolTransformSeeds"] >= 11 and out["degradeNoToolTransformHooks"] == 2
    assert out["degradeNoToolHook"] is True
    assert out["degradeNoToolHookTools"] == 5 and out["degradeNoToolHookCondense"] is False
    assert out["degradeNoSessionHook"] is True
    assert out["degradeNoSessionHookTools"] == 5 and out["degradeNoSessionHookBrief"] is False
    assert out["degradePartialAdd"] is True
    assert out["degradePartialAddTools"] == ["memory_recall", "skills_list", "todoread", "todowrite"]
    assert out["degradeNoList"] is True and out["degradeNoListSeeds"] >= 11
    assert out["degradeSchemaDrift"] == "(nothing recalled — verify from the transcript instead)", out["degradeSchemaDrift"]
    assert out["degradeSchemaDriftBrief"] == 0


@pytest.mark.skipif(BUN is None, reason="bun is not installed")
def test_plugin_setup_registers_through_ctx(tmp_path):
    from harness.cli import _plugin_files
    smoke = tmp_path / "smoke.ts"
    smoke.write_text(SMOKE)
    r = subprocess.run([BUN, "run", str(smoke), _plugin_files()[0]],
                       capture_output=True, text=True, timeout=180, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])

    assert out["id"] == "harness"
    assert out["returned"] is None, "setup must not return a hooks object"
    assert out["hooks"] == ["tool:execute.after", "session:compaction"]
    assert [t["name"] for t in out["tools"]] == ["skills_list", "skill_view", "memory_recall", "todowrite", "todoread"]
    for t in out["tools"]:
        assert t["input"] == "object" and t["execute"] == "function"
        # codemode:false == exposed as a direct tool (the v2 default hides it
        # in the Code Mode catalog, where calling it by name fails)
        assert t["direct"] is True, f"{t['name']} is not a direct tool"
    # every executor answers with a content string (the v2 tool result shape)
    assert set(out["execs"]) == {"skills_list", "skill_view", "memory_recall", "todowrite", "todoread"}
    assert all(v == "string" for v in out["execs"].values()), out["execs"]
    # bundled seeds are registered in opencode's skill shape
    assert out["skills"], "no skills seeded"
    for s in out["skills"]:
        assert s["id"].startswith("harness-") and s["path"] == "string" and s["content"] == "string"
    # a virtual builtin skill (path on no real file) resolves from inline content
    assert out["views"]["builtin"] == {"ok": True, "text": "# OpenCode\n\nBuiltin body."}, out["views"]
    # a missing reference file (and an unknown id) answer with text, never throw
    assert out["views"]["builtinRef"]["ok"] is True, out["views"]
    assert "references/nope.md" in out["views"]["builtinRef"]["text"]
    assert out["views"]["unknown"] == {"ok": True, "text": "skill not found: does-not-exist"}, out["views"]
    assert out["views"]["noId"]["ok"] is True and "needs an id" in out["views"]["noId"]["text"]
    # condensing shrinks oversized success output and leaves errors untouched
    assert out["condensed"] == [True, True]


def test_plugin_install_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness.cli import _opencode_config_path
    r = subprocess.run([sys.executable, "-m", "harness", "plugin", "install"],
                       capture_output=True, text=True, timeout=60,
                       cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[:300]
    d = json.loads((_opencode_config_path()).read_text())
    assert not [p for p in d.get("plugin", []) if "harness/plugin" in str(p)], "legacy specs removed"
    plugdir = Path(os.environ["XDG_CONFIG_HOME"]) / "opencode" / "plugins"
    # directory layout: server entrypoint + TUI entrypoint (the tui one is what
    # makes the TUI plugin list include harness)
    assert (plugdir / "harness" / "server.ts").exists()
    assert (plugdir / "harness" / "tui.tsx").exists()
    r = subprocess.run([sys.executable, "-m", "harness", "plugin", "install"],
                       capture_output=True, text=True, timeout=60,
                       cwd=str(REPO_ROOT))
    assert r.returncode == 0
    # install merges agents (model-free, inherit user default) and no relay
    d = json.loads((_opencode_config_path()).read_text())
    assert "harness" not in d.get("provider", {})
    assert "orchestrator" in d.get("agent", {})
    assert "code-reviewer" in d.get("agent", {}), "specialized subagents merge too"
    # a legacy single-file install is superseded, not left behind to double-load
    legacy = plugdir / "harness.ts"
    legacy.write_text("// stale single-file install")
    r = subprocess.run([sys.executable, "-m", "harness", "plugin", "install"],
                       capture_output=True, text=True, timeout=60,
                       cwd=str(REPO_ROOT))
    assert r.returncode == 0 and not legacy.exists(), "legacy harness.ts must be removed"
    r = subprocess.run([sys.executable, "-m", "harness", "plugin", "uninstall"],
                       capture_output=True, text=True, timeout=60,
                       cwd=str(REPO_ROOT))
    assert r.returncode == 0 and "removed plugin dir" in r.stdout
    d = json.loads((_opencode_config_path()).read_text())
    assert "harness" not in d.get("provider", {})
    assert "code-reviewer" not in d.get("agent", {}), "uninstall removes the subagents it merged"
    assert not (plugdir / "harness").exists()


def test_tui_window_bar_is_the_last_message_not_the_session_total():
    """The Window row summed the session's lifetime tokens against the context
    window, so the bar pinned at 100% early in a real session: the aggregate
    only grows, but a window is filled by what is currently in it. opencode's
    own header computes the gauge from the LAST assistant message
    (`usage.Output > 0`; a compaction summary counts its output only).
    """
    from harness.cli import _plugin_files
    tui = Path(_plugin_files()[1]).read_text()
    # the last-message walk, exactly as opencode's header does it
    assert "Number(t.output ?? 0) <= 0" in tui
    assert "ctxUsed = m?.summary ? Number(t.output ?? 0) : totalTokens(t)" in tui
    assert "data.contextUsed = ctxUsed" in tui
    # the bar and its detail read the last message; the Tokens row keeps the total
    assert "return Number(data.contextUsed ?? 0) || 0" in tui
    assert "Math.round((used() / data.contextLimit) * 100)" in tui
    assert "`${numfmt(used())} / ${numfmt(data.contextLimit)} in context`" in tui
    # the value tints itself once the window is actually filling up
    assert "pct() >= 80 ? th.warn" in tui
    assert "return totalTokens(data.tokens)" in tui
    assert "Math.round((total() / data.contextLimit) * 100)" not in tui, (
        "the percentage must not divide the session total by the window"
    )


def test_tui_panel_shows_only_thoughtful_accurate_rows():
    """The panel's job is the information you want at a glance; the render
    carried noise and numbers that disagreed with their own lists:

    * "Agents 11 available" listed 5 rows — opencode's internal compaction and
      title agents were advertised as spawnable work roles (now filtered at
      load, active agent marked first);
    * empty Todo/Workers/Memory rows ("Workers none", "0 facts") narrated
      their own emptiness — they hide instead;
    * the facts count never reached the view state (and the raw query was
      LIMIT-capped), so header and Memory printed 0 with facts on disk — it
      now comes from `count(*)`;
    * the Todo value repeated the row count — it is progress now (`1/4 done`);
    * the footer chip led with the lifetime total, a number that only ever
      grows — window pressure comes first.
    """
    from harness.cli import _plugin_files
    tui = Path(_plugin_files()[1]).read_text()

    # internal plumbing agents are never advertised as available work roles
    assert 'INTERNAL_AGENTS = new Set(["compaction", "title"' in tui
    assert "!INTERNAL_AGENTS.has(a.toLowerCase())" in tui
    # the active agent leads the list and is marked
    assert "sort((a, b) => Number(b === cur) - Number(a === cur))" in tui
    assert '${a === cur ? "●" : "◦"} ${a}' in tui

    # empty sections disappear instead of narrating their emptiness
    for needle in ("return !(view.todos ?? 0)", "return !view.workers", "return !(view.facts ?? 0)"):
        assert needle in tui, needle

    # a REAL facts count reaches the view state, and todo progress with it
    assert "SELECT count(*) AS n FROM facts" in tui
    assert "d.facts = data.factsCount" in tui
    assert "out.todosDone = rows.filter" in tui
    assert "d.todosDone = data.todosDone" in tui

    # chip leads with context pressure; the lifetime total is demoted
    assert "`${p}% ctx · `" in tui
    # header drops zero counts instead of printing "0 facts"
    assert '(view.facts ?? 0) > 0 ? `${view.facts} facts` : ""' in tui
