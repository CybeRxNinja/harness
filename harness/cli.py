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


def cmd_serve(args) -> int:
    if getattr(args, "stop", False):
        from .serve import read_sentinel
        import os as _o
        cur = read_sentinel()
        if not cur:
            print("no live gateway")
            return 0
        _o.kill(int(cur["pid"]), 15)
        print(f"stopped gateway (was serving {cur.get('root')})")
        return 0
    from .serve import serve
    serve(_root(args), args.port, "0.0.0.0" if args.expose else "127.0.0.1")
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
    from .serve import ensure_token
    from .config import user_dir
    print("Keys are read from env (never stored by chat). Set e.g.:")
    print("  export OPENROUTER_API_KEY=... GROQ_API_KEY=... OLLAMA_BASE_URL=http://localhost:11434/v1")
    print(f"Gateway token: {ensure_token()}  (file {user_dir()/'token'}, chmod 600)")
    print("Router default works with zero keys in MOCK mode; add one key to go live.")
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
    return {"provider": {"harness": {"npm": "@ai-sdk/openai-compatible", "name": "Harness Relay",
            "options": {"baseURL": "http://127.0.0.1:8787/v1", "apiKey": "{env:HARNESS_TOKEN}"},
            "models": {"auto-fastest": {"name": "Auto Fastest", "reasoning": True}}}},
            "agent": {}, "model": "harness/auto-fastest"}


def ensure_opencode_config() -> str:
    """Merge provider.harness + harness agents into opencode.json. Never clobbers user keys."""
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
    prov = cur.setdefault("provider", {})
    if "harness" not in prov:
        prov["harness"] = want["provider"]["harness"]
    else:
        # backfill new keys (e.g. reasoning capability) without clobbering user edits
        existing_models = prov["harness"].setdefault("models", {})
        for mid, spec in want["provider"]["harness"].get("models", {}).items():
            node = existing_models.setdefault(mid, {})
            for k, v in spec.items():
                node.setdefault(k, v)
    agents = cur.setdefault("agent", {})
    for name, spec in want["agent"].items():
        node = agents.setdefault(name, {})
        for k, v in spec.items():
            node.setdefault(k, v)
    cur.setdefault("model", "harness/auto-fastest")
    dest.write_text(_j.dumps(cur, indent=2) + "\n")
    return str(dest)


def _serve_healthy(port: int) -> bool:
    import urllib.request as _u
    try:
        with _u.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _ensure_serve(root: Path, port: int) -> None:
    import subprocess as _sp
    import time as _t
    from .serve import read_sentinel
    root = root.resolve()
    if _serve_healthy(port):
        cur = read_sentinel()
        if not cur or cur.get("root") == str(root):
            return  # same project (or legacy): reuse
        print(f"harness: gateway serves {cur.get('root')} — restarting for {root} "
              f"(sessions persist in each .opencode/harness/)")
        try:
            import os as _o
            _o.kill(int(cur["pid"]), 15)
            for _ in range(20):
                _t.sleep(0.25)
                if not _serve_healthy(port):
                    break
        except Exception as e:
            raise RuntimeError(f"cannot stop gateway for {cur.get('root')} (pid {cur.get('pid')}): {e}. "
                               f"Stop it manually or use --port")
    log = state_dir(root) / "serve.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    _sp.Popen([sys.executable, "-m", "harness", "--root", str(root),
               "serve", "--port", str(port)],
              stdout=open(log, "a"), stderr=_sp.STDOUT, start_new_session=True)
    for _ in range(25):
        if _serve_healthy(port):
            return
        _t.sleep(0.4)
    raise RuntimeError(f"serve did not come up on :{port} (see {log})")


def _find_tui() -> str | None:
    import glob as _g
    import shutil as _sh
    cands = [_sh.which("harness-tui"), str(Path.home() / ".local" / "bin" / "harness-tui")]
    # AppImages: single-file, no install (case-insensitive; release uses Harness_TUI-*)
    for pat in ("harness-tui*.AppImage", "Harness_TUI*.AppImage", "*arness*.AppImage"):
        cands += sorted(_g.glob(str(Path.home() / "Applications" / pat)))
        cands += sorted(_g.glob(pat))
    for cand in cands:
        if cand and Path(cand).exists():
            return cand
    return None


def _ensure_tmp() -> None:
    """Bun-compiled TUI materializes native libs under TMPDIR. If it is not
    writable (full /tmp is the classic failure), fall back to a cache dir."""
    import tempfile as _t
    tmp = os.environ.get("TMPDIR", "/tmp")
    try:
        with _t.TemporaryFile(dir=tmp):
            return
    except Exception:
        fb = Path.home() / ".cache" / "harness-tmp"
        fb.mkdir(parents=True, exist_ok=True)
        os.environ["TMPDIR"] = str(fb)


def cmd_tui(args) -> int:
    from .serve import ensure_token
    root = _root(args)
    if args.dry_run:
        print(f"would: ensure serve :{args.port}, merge {_opencode_config_path()}, exec harness-tui")
        return 0
    _ensure_serve(root, args.port)
    os.environ["HARNESS_TOKEN"] = ensure_token()
    os.environ.setdefault("HARNESS_URL", f"http://127.0.0.1:{args.port}")
    _ensure_tmp()
    try:
        cfg_path = ensure_opencode_config()
    except Exception as e:
        print(f"harness: config merge failed ({e}) — continuing", file=sys.stderr)
        cfg_path = _opencode_config_path()
    if args.setup_only:
        print(f"export HARNESS_TOKEN={os.environ['HARNESS_TOKEN']}")
        print(f"export HARNESS_URL={os.environ['HARNESS_URL']}")
        return 0
    binary = _find_tui()
    if not binary:
        print("harness-tui binary not found. Get it via:")
        print("  curl -fsSL https://raw.githubusercontent.com/CybeRxNinja/harness/main/install.sh | bash")
        print(f"(opencode.json already wired at {cfg_path}; CLI fallback: harness chat)")
        return 1
    print(f"launching {binary} (serve :{args.port}, config {cfg_path})")
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
    s = sub.add_parser("serve", help="HTTP gateway (TUI-B talks here)")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--expose", action="store_true", help="bind 0.0.0.0 (warning: token auth still required)")
    s.add_argument("--stop", action="store_true", help="stop the running gateway")
    s.set_defaults(fn=cmd_serve)
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
    t = sub.add_parser("tui", help="one command: serve + config + launch harness-tui")
    t.add_argument("--port", type=int, default=8787)
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--setup-only", action="store_true",
                   help="ensure serve+config, print exports, do not launch")
    t.set_defaults(fn=cmd_tui)
    return p


def main(argv=None) -> int:
    # `harness "prompt"` shorthand -> chat
    if argv is None:
        argv = sys.argv[1:]
    if argv and not argv[0].startswith("-") and argv[0] not in (
            "chat", "serve", "doctor", "config", "skills", "memory", "checkpoint", "plan", "setup", "tui"):
        argv = ["chat", argv[0]] + argv[1:]
    args = build_parser().parse_args(argv)
    if not getattr(args, "cmd", None) and not hasattr(args, "fn"):
        build_parser().print_help()
        return 2
    os.environ.setdefault("HARNESS_MOCK", os.environ.get("HARNESS_MOCK", ""))
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
