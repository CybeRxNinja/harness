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
  const input = t.name === "skill_view" ? { id: seeds[0]?.id ?? "missing" } : t.name === "memory_recall" ? { query: "smoke" } : {}
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
const call = async (name: string, input?: any): Promise<string> => {
  try {
    const o = await tools[name].execute(input ?? {}, {})
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


@pytest.mark.skipif(BUN is None, reason="bun is not installed")
def test_plugin_features(tmp_path):
    from harness.cli import _plugin_files
    script = tmp_path / "features.ts"
    script.write_text(FEATURES)
    # HOME is pointed at tmp so the drift case cannot read the developer's real
    # fact DB (factDbs falls back to $HOME/.opencode/harness/sessions.db)
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

    # future-host degradation: an opencode upgrade that renames/removes a ctx
    # API costs only its own feature — activation never throws, tools that do
    # not depend on the lost API still register, one bad editor.add does not
    # sink the other tools, and a drifted DB schema degrades to an answer.
    assert out["degradeNoSkillTransform"] is True
    assert out["degradeNoSkillTransformTools"] == 3 and out["degradeNoSkillTransformHooks"] == 2
    assert out["degradeNoToolTransform"] is True
    assert out["degradeNoToolTransformSeeds"] >= 11 and out["degradeNoToolTransformHooks"] == 2
    assert out["degradeNoToolHook"] is True
    assert out["degradeNoToolHookTools"] == 3 and out["degradeNoToolHookCondense"] is False
    assert out["degradeNoSessionHook"] is True
    assert out["degradeNoSessionHookTools"] == 3 and out["degradeNoSessionHookBrief"] is False
    assert out["degradePartialAdd"] is True
    assert out["degradePartialAddTools"] == ["memory_recall", "skills_list"]
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
    assert [t["name"] for t in out["tools"]] == ["skills_list", "skill_view", "memory_recall"]
    for t in out["tools"]:
        assert t["input"] == "object" and t["execute"] == "function"
        # codemode:false == exposed as a direct tool (the v2 default hides it
        # in the Code Mode catalog, where calling it by name fails)
        assert t["direct"] is True, f"{t['name']} is not a direct tool"
    # every executor answers with a content string (the v2 tool result shape)
    assert set(out["execs"]) == {"skills_list", "skill_view", "memory_recall"}
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
    assert not (plugdir / "harness").exists()
