"""harness CLI (fallback UI — guaranteed runner, zero TUI deps)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from .paths import state_dir


def _root(args) -> Path:
    return Path(getattr(args, "root", ".")).resolve()


def cmd_chat(args) -> int:
    from .config import load_config
    from .loop import run_turn
    from .store import connect, ensure_session
    root = _root(args)
    cfg, _ = load_config(root)
    sid = args.resume or f"s_{uuid.uuid4().hex[:8]}"
    con = connect(root)
    ensure_session(con, sid, args.mode)
    con.close()
    prompt = args.prompt or sys.stdin.read()
    out = run_turn(root, sid, prompt, args.mode, args.model, cfg, auto_approve=args.auto)
    print(out["content"])
    print(f"\n[session {sid} mode={args.mode} model={out.get('route_model')}]", file=sys.stderr)
    return 0


def cmd_doctor(args) -> int:
    from .doctor import run
    print(json.dumps(run(_root(args), args.verbose), indent=2))
    return 0


def cmd_config(args) -> int:
    from .config import load_config, redact, get, set_value
    root = _root(args)
    cfg, _ = load_config(root)
    if args.config_action == "get":
        print(json.dumps(get(redact(cfg), args.path), indent=2))
    elif args.config_action == "set":
        print(set_value(root, args.path, json.loads(args.value), args.scope))
    elif args.config_action == "show":
        print(json.dumps(redact(cfg), indent=2))
    return 0


def cmd_skills(args) -> int:
    from .config import load_config
    from .store import connect
    from . import skills as S
    root = _root(args)
    cfg, _ = load_config(root)
    con = connect(root)
    if args.skills_action == "list":
        for s in S.scan(root, cfg):
            print(f"{s['name']:28s} [{s['tier']}] {s['description'][:80]}")
    elif args.skills_action == "view":
        print(S.view(root, cfg, args.name, args.subpath or ""))
    elif args.skills_action == "pending":
        from .memory import list_pending
        for p in list_pending(con):
            print(f"{p['id']} {p['kind']} {p['name']} — {p['gist']}")
    elif args.skills_action in ("approve", "reject"):
        ids = [int(x) for x in args.ids] if args.ids != ["all"] else [
            r[0] for r in con.execute("SELECT id FROM pending").fetchall()]
        for pid in ids:
            if args.skills_action == "approve":
                from .memory import approve
                from .config import user_dir
                # skill approvals apply file writes
                row = con.execute("SELECT kind,name,diff FROM pending WHERE id=?", (pid,)).fetchone()
                if row and row[0].startswith("skill-"):
                    base = user_dir() / "skills" / "general" / row[1]
                    base.mkdir(parents=True, exist_ok=True)
                    if row[0] == "skill-create":
                        (base / "SKILL.md").write_text(row[2])
                    con.execute("DELETE FROM pending WHERE id=?", (pid,))
                    con.commit()
                    print(f"approved {pid}")
                else:
                    ok = approve(con, pid, state_dir(root) / "MEMORY.md")
                    print(f"{'approved' if ok else 'missing'} {pid}")
            else:
                con.execute("DELETE FROM pending WHERE id=?", (pid,))
                con.commit()
                print(f"rejected {pid}")
    con.close()
    return 0


def cmd_memory(args) -> int:
    from .store import connect
    from . import memory as M
    root = _root(args)
    con = connect(root)
    if args.memory_action == "search":
        for h in M.recall(con, args.query):
            print(f"- [{h.get('source')}] {h['text'][:300]}")
    elif args.memory_action == "save":
        print("saved", M.save_fact(con, args.text, "cli"))
    elif args.memory_action == "refine":
        # stage lesson from last assistant message (evidence-backed: needs session text)
        rows = con.execute("SELECT content FROM messages WHERE session=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                           (args.session,)).fetchall()
        ev = rows[0][0][:800] if rows else ""
        if len(ev) < 50:
            print("refine needs evidence: no substantial trajectory yet")
        else:
            pid = M.stage_lesson(con, args.name or "lesson", ev, args.gist or ev[:120])
            print(f"staged lesson {pid} (approve via harness skills approve {pid})")
    con.close()
    return 0


def cmd_checkpoint(args) -> int:
    from . import checkpoints as C
    root = _root(args)
    if args.ck_action == "save":
        print(C.checkpoint(root))
    else:
        print(C.restore(root, args.snap))
    return 0


def cmd_plan(args) -> int:
    from . import orchestrator as O
    root = _root(args)
    if args.plan_action == "start":
        items = [x.strip() for x in args.items.split(";") if x.strip()]
        print(O.start_plan(root, args.title, items))
    elif args.plan_action == "next":
        b = O.load_boulder(root)
        wid = b.get("active_work_id")
        if not wid:
            print("no active work")
            return 1
        plan = b["works"][wid]["plan"]
        print(O.next_box(Path(plan).read_text()) or "complete")
    elif args.plan_action == "check":
        from pathlib import Path as _P
        b = O.load_boulder(root)
        wid = b.get("active_work_id")
        print("checked" if O.check_box(root, b["works"][wid]["plan"], args.item) else "nothing to check")
    return 0


def cmd_setup(args) -> int:
    from .skills import ensure_seed_skills
    from . import models as backend
    seeded = ensure_seed_skills()
    print(f"Seed skills installed: {len(seeded)} new" + (f" ({', '.join(seeded[:5])})" if seeded else ""))
    if _find_opencode():
        try:
            print(f"opencode default model: {backend.resolve_model('', {})}")
        except RuntimeError as e:
            print(f"WARNING: {e}")
    else:
        print("WARNING: opencode binary not found — install stock opencode "
              "(e.g. `npm create opencode@latest`); models come from YOUR opencode config.")
    print("No API keys live here: models run inside opencode with the user's own providers.")
    return 0


def _opencode_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "opencode" / "opencode.json"


def _bundled_opencode_json() -> dict:
    """harness-opencode.json from repo tree, installed package data, or inline fallback."""
    import json as _j
    from pathlib import Path as _P
    cands = [
        _P(__file__).resolve().parent.parent / "tui" / "harness-opencode.json",  # repo checkout
        _P(__file__).resolve().parent / "harness-opencode.json",  # pip install (package-data)
    ]
    for c in cands:
        try:
            if c.exists():
                return _j.loads(c.read_text())
        except Exception:
            continue
    try:
        from importlib.resources import files as _rf
        return _j.loads(_rf("harness").joinpath("harness-opencode.json").read_text())
    except Exception:
        pass
    return {"agent": {}}


def ensure_opencode_config() -> str:
    """Merge harness agents into opencode.json. Never clobbers user keys.

    No provider, no model pinning: agents inherit the user's configured
    opencode default model. The relay is retired (see git history)."""
    import json as _j
    import shutil as _sh
    import time as _t
    dest = _opencode_config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        cur = _j.loads(dest.read_text()) if dest.exists() else {}
    except Exception:
        cur = {}
    if dest.exists():
        _sh.copy2(dest, dest.with_suffix(f".bak-{int(_t.time())}.json"))
    want = _bundled_opencode_json()
    agents = cur.setdefault("agent", {})
    for name, spec in want.get("agent", {}).items():
        node = agents.setdefault(name, {})
        for k, v in spec.items():
            node.setdefault(k, v)
    # Legacy relay cleanup for upgraders: provider.harness and harness/* model
    # pins are removed (agents inherit the user default now). User-owned keys
    # are never touched.
    try:
        if isinstance(cur.get("provider"), dict) and "harness" in cur["provider"]:
            del cur["provider"]["harness"]
        if isinstance(cur.get("agent"), dict):
            for _name, _spec in cur["agent"].items():
                if isinstance(_spec, dict) and str(_spec.get("model", "")).startswith("harness/"):
                    del _spec["model"]
        if str(cur.get("model", "")).startswith("harness/"):
            del cur["model"]
    except Exception:
        pass
    # LSP on unless the user already decided (true enables built-ins;
    # explicit false/object is always respected).
    cur.setdefault("lsp", True)
    # NOTE: no mcp.harness-skills block here on purpose — the plugin exposes
    # skills_list/skill_view/memory_recall as NATIVE tools (no extra process,
    # no stdio framing to break). The stdio server (`harness mcp`) remains
    # for non-opencode MCP clients only.
    try:
        if isinstance(cur.get("mcp"), dict) and "harness-skills" in cur["mcp"]:
            del cur["mcp"]["harness-skills"]
    except Exception:
        pass
    dest.write_text(_j.dumps(cur, indent=2) + "\n")
    return str(dest)


def _find_opencode() -> str | None:
    """Locate a stock `opencode` binary on PATH. No fork, no AppImage."""
    import shutil as _sh
    return _sh.which("opencode")


def cmd_mcp(args) -> int:
    from .mcp_server import serve_stdio
    return serve_stdio(_root(args))


def _plugin_files() -> list[str]:
    """Absolute path of the shipped v2 plugin module."""
    from pathlib import Path as _P
    return [str((_P(__file__).resolve().parent / "plugin" / "harness.ts").resolve())]


def download_plugin(release: str = "latest") -> Path:
    """Fetch harness.ts from a GitHub release asset into the plugins dir.
    No repo checkout needed — pairs with the pip/AppImage CLI install."""
    import json as _j
    import urllib.request as _u
    api = ("https://api.github.com/repos/CybeRxNinja/harness/releases/latest"
           if release in ("latest", "") else
           f"https://api.github.com/repos/CybeRxNinja/harness/releases/tags/{release}")
    with _u.urlopen(api, timeout=30) as r:
        rel = _j.loads(r.read().decode())
    tag = rel.get("tag_name", release)
    asset = next((a for a in rel.get("assets", []) if a.get("name") == "harness.ts"), None)
    if asset is None:
        raise ValueError(f"no harness.ts asset in release {tag}")
    plugdir = Path.home() / ".config" / "opencode" / "plugins"
    if os.environ.get("XDG_CONFIG_HOME"):
        plugdir = Path(os.environ["XDG_CONFIG_HOME"]) / "opencode" / "plugins"
    plugdir.mkdir(parents=True, exist_ok=True)
    dest_file = plugdir / "harness.ts"
    with _u.urlopen(asset["browser_download_url"], timeout=120) as r:
        dest_file.write_bytes(r.read())
    print(f"plugin {tag} downloaded to {dest_file}")
    return dest_file


def install_plugin() -> Path:
    """Copy the shipped harness.ts server plugin into opencod's auto-loaded
    plugins dir (~/.config/opencode/plugins/) and drop any legacy v1
    'harness/plugin' specs from opencod.json. Returns the installed path.

    Stock opencod v2 auto-loads every plugin from that dir, so no config entry
    is required (the `plugin` array only stores legacy specs we prune here)."""
    import json as _j
    import shutil as _sh
    plugdir = Path.home() / ".config" / "opencode" / "plugins"
    if os.environ.get("XDG_CONFIG_HOME"):
        plugdir = Path(os.environ["XDG_CONFIG_HOME"]) / "opencode" / "plugins"
    plugdir.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent / "plugin" / "harness.ts"
    if not src.exists():
        raise FileNotFoundError("plugin source missing from install (dev: run from repo)")
    dest_file = plugdir / "harness.ts"
    _sh.copy2(src, dest_file)
    dest = _opencode_config_path()
    try:
        cur = _j.loads(dest.read_text()) if dest.exists() else {}
    except Exception:
        cur = {}
    plugs = cur.setdefault("plugin", [])
    before = len(plugs)
    plugs[:] = [p for p in plugs
                if not (isinstance(p, str) and "harness/plugin" in p)]
    if len(plugs) != before or not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_j.dumps(cur, indent=2) + "\n")
    return dest_file


def uninstall_plugin() -> list[str]:
    """Remove everything `install` manages: the plugin file plus harness-merged
    agent blocks. User-owned keys are never touched: agents with a user-set
    (non-harness) model pin are kept. Returns human-readable lines."""
    import json as _j
    done: list[str] = []
    plugdir = Path.home() / ".config" / "opencode" / "plugins"
    if os.environ.get("XDG_CONFIG_HOME"):
        plugdir = Path(os.environ["XDG_CONFIG_HOME"]) / "opencode" / "plugins"
    target = plugdir / "harness.ts"
    if target.exists():
        target.unlink()
        done.append(f"removed plugin file {target}")
    dest = _opencode_config_path()
    try:
        cur = _j.loads(dest.read_text()) if dest.exists() else {}
    except Exception:
        cur = {}
    changed = False
    if isinstance(cur.get("provider"), dict) and "harness" in cur["provider"]:
        del cur["provider"]["harness"]
        done.append("removed provider.harness")
        changed = True
    if isinstance(cur.get("agent"), dict):
        for name in ("orchestrator", "ask", "debug", "review", "plan"):
            if name in cur["agent"] and isinstance(cur["agent"][name], dict):
                model = str(cur["agent"][name].get("model", ""))
                if not model or model.startswith("harness/"):
                    del cur["agent"][name]
                    done.append(f"removed agent.{name}")
                    changed = True
    if str(cur.get("model", "")).startswith("harness/"):
        cur.pop("model", None)
        done.append("removed default model (was harness/*)")
        changed = True
    if isinstance(cur.get("mcp"), dict) and "harness-skills" in cur["mcp"]:
        del cur["mcp"]["harness-skills"]
        done.append("removed mcp.harness-skills")
        changed = True
    if changed:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_j.dumps(cur, indent=2) + "\n")
    if not done:
        done.append("nothing harness-owned found")
    return done


def cmd_plugin(args) -> int:
    if args.plugin_action == "install":
        if getattr(args, "from_release", ""):
            download_plugin(args.from_release)
            plug = _opencode_config_path().parent / "plugins" / "harness.ts"
        else:
            plug = install_plugin()
        try:
            ensure_opencode_config()
        except Exception as e:
            print(f"harness: provider merge failed ({e}) — continuing", file=sys.stderr)
        print(f"plugin installed at {plug} (stock opencod v2, no fork needed)")
        print("restart opencode/TUI to load it; agents use your opencode default model")
    elif args.plugin_action == "uninstall":
        for line in uninstall_plugin():
            print(line)
        print("optional: remove CLI (`pip uninstall harness`)")
    elif args.plugin_action == "path":
        for spec in _plugin_files():
            print(spec)
    return 0


def cmd_tui(args) -> int:
    from .skills import ensure_seed_skills
    ensure_seed_skills()
    if args.dry_run:
        print(f"would: merge {_opencode_config_path()}, install plugin, exec opencode")
        return 0
    try:
        cfg_path = ensure_opencode_config()
    except Exception as e:
        print(f"harness: config merge failed ({e}) — continuing", file=sys.stderr)
        cfg_path = _opencode_config_path()
    try:
        plug = install_plugin()
    except Exception as e:
        print(f"harness: plugin install failed ({e}) — continuing", file=sys.stderr)
        plug = Path(__file__).resolve().parent / "plugin" / "harness.ts"
    if args.setup_only:
        print(f"opencode config: {cfg_path}")
        print(f"harness plugin: {plug}")
        return 0
    binary = _find_opencode()
    if not binary:
        print("opencode binary not found. Install stock opencode, e.g.:")
        print("  npm create opencode@latest   (or: npx opencode)")
        print(f"(opencod.json wired at {cfg_path}; harness plugin at {plug}; CLI fallback: harness chat)")
        return 1
    print(f"launching {binary} (agents inherit your opencode default model; no relay)")
    os.execvp(binary, [binary])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harness", description="All-in-one coding harness (CLI fallback)")
    p.add_argument("--root", default=".", help="project root")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("chat", help="run one turn (default command)")
    c.add_argument("prompt", nargs="?", default="")
    c.add_argument("--resume", default="")
    c.add_argument("--mode", default="code", choices=["code", "orchestrator", "plan", "ask", "debug", "review"])
    c.add_argument("--model", default="")
    c.add_argument("--auto", action="store_true")
    c.set_defaults(fn=cmd_chat)
    d = sub.add_parser("doctor")
    d.add_argument("--verbose", action="store_true")
    d.set_defaults(fn=cmd_doctor)
    g = sub.add_parser("config")
    g.add_argument("config_action", choices=["get", "set", "show"])
    g.add_argument("path", nargs="?", default="budgets")
    g.add_argument("value", nargs="?", default="null")
    g.add_argument("--scope", default="user", choices=["user", "project"])
    g.set_defaults(fn=cmd_config)
    k = sub.add_parser("skills")
    k.add_argument("skills_action", choices=["list", "view", "pending", "approve", "reject"])
    k.add_argument("name", nargs="?", default="")
    k.add_argument("--subpath", default="")
    k.add_argument("--ids", nargs="*", default=[])
    k.set_defaults(fn=cmd_skills)
    m = sub.add_parser("memory")
    m.add_argument("memory_action", choices=["search", "save", "refine"])
    m.add_argument("query", nargs="?", default="")
    m.add_argument("--text", default="")
    m.add_argument("--session", default="default")
    m.add_argument("--name", default="")
    m.add_argument("--gist", default="")
    m.set_defaults(fn=cmd_memory)
    t = sub.add_parser("checkpoint")
    t.add_argument("ck_action", choices=["save", "restore"])
    t.add_argument("snap", nargs="?", default="")
    t.set_defaults(fn=cmd_checkpoint)
    pl = sub.add_parser("plan")
    pl.add_argument("plan_action", choices=["start", "next", "check"])
    pl.add_argument("--title", default="work")
    pl.add_argument("--items", default="")
    pl.add_argument("--item", default="")
    pl.set_defaults(fn=cmd_plan)
    su = sub.add_parser("setup")
    su.set_defaults(fn=cmd_setup)
    t = sub.add_parser("tui", help="opencode config + plugin, then launch opencode")
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--setup-only", action="store_true",
                   help="ensure config+plugin, print paths, do not launch")
    t.set_defaults(fn=cmd_tui)
    mc = sub.add_parser("mcp", help="run harness as an MCP stdio server (skills+memory tools)")
    mc.set_defaults(fn=cmd_mcp)
    pl = sub.add_parser("plugin", help="opencode plugin (stock opencode, no fork)")
    pl.add_argument("plugin_action", choices=["install", "uninstall", "path"])
    pl.add_argument("--from-release", default="",
                    help="install harness.ts from a GitHub release instead (e.g. --from-release plugin-v0.1, default latest)")
    pl.set_defaults(fn=cmd_plugin)
    return p


def main(argv=None) -> int:
    # `harness "prompt"` shorthand -> chat
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in ("serve", "router"):
        print(f"harness {argv[0]} was retired with the relay: models now come "
              f"from your opencode config (`harness doctor` to verify).", file=sys.stderr)
        return 2
    if argv and not argv[0].startswith("-") and argv[0] not in (
            "chat", "doctor", "config", "skills", "memory", "checkpoint", "plan", "setup", "tui", "mcp", "plugin"):
        argv = ["chat", argv[0]] + argv[1:]
    args = build_parser().parse_args(argv)
    if not getattr(args, "cmd", None) and not hasattr(args, "fn"):
        build_parser().print_help()
        return 2
    os.environ.setdefault("HARNESS_MOCK", os.environ.get("HARNESS_MOCK", ""))
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
