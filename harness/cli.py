"""harness CLI (fallback UI — guaranteed runner, zero TUI deps)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path


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
                    ok = approve(con, pid, root / ".harness" / "MEMORY.md")
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
    return p


def main(argv=None) -> int:
    # `harness "prompt"` shorthand -> chat
    if argv is None:
        argv = sys.argv[1:]
    if argv and not argv[0].startswith("-") and argv[0] not in (
            "chat", "serve", "doctor", "config", "skills", "memory", "checkpoint", "plan", "setup"):
        argv = ["chat", argv[0]] + argv[1:]
    args = build_parser().parse_args(argv)
    if not getattr(args, "cmd", None) and not hasattr(args, "fn"):
        build_parser().print_help()
        return 2
    os.environ.setdefault("HARNESS_MOCK", os.environ.get("HARNESS_MOCK", ""))
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
