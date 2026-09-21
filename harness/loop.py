"""Single agent loop. Mode-aware tool allowlist. Budget-enforced. No infinite loops."""
from __future__ import annotations

import time
from pathlib import Path

from . import models as backend
from .kernel import Kernel

# Restricted modes are read-only. `skills_list` belongs in every set that
# allows `skill_view`: without it the model has to guess skill names.
READ_ONLY_TOOLS = {"read", "glob", "grep", "skill_view", "skills_list", "memory"}
MODE_TOOLS = {
    "code": None,  # all
    "orchestrator": None,
    "plan": READ_ONLY_TOOLS | {"config_get"},
    "ask": READ_ONLY_TOOLS,
    "debug": None,
    "review": READ_ONLY_TOOLS,
}

def _fresh_history(hist: list[dict]) -> list[dict]:
    """Drop stale system messages (old modes, old compaction notes, old hints).

    Only the most recent system entry (current state) survives; user/assistant
    turns pass through untouched. Prevents contradictory directives piling up
    across mode switches and compactions.
    """
    seen_system = False
    out: list[dict] = []
    for m in reversed(hist):
        if m.get("role") == "system":
            if seen_system:
                continue
            seen_system = True
        out.append(m)
    return list(reversed(out))


SYSTEM = {
    "code": "You are Harness, a senior coding agent. Be concrete. Use tools. Verify with tests. Return summary+files changed.",
    "orchestrator": ("You are the ORCHESTRATOR. Never write product code yourself. Decompose, spawn(category=...) "
                     "parallel workers, merge diffs, verify with an independent reviewer before done."),
    "plan": "You are in PLAN mode. Read-only. Produce a decision-complete plan, no code writes.",
    "ask": "You are in ASK mode. Answer from the codebase. No writes.",
    "debug": "You debug methodically: reproduce, localize, reduce, fix, guard.",
    "review": "You review like a staff engineer: five axes, severity Nit/Blocker, ~100-line hunks.",
}


def _allowed(mode: str, tool: str) -> bool:
    allow = MODE_TOOLS.get(mode)
    return True if allow is None else tool in allow


def run_turn(project_root: Path, session: str, user_msg: str, mode: str = "code",
             model: str = "", cfg: dict | None = None, auto_approve: bool = False,
             budget_override: dict | None = None) -> dict:
    from .config import load_config
    from .store import connect, ensure_session, add_message, history
    from . import memory as M  # recall hint
    from . import rlm as R  # inbox flush
    from .compact import should_compact, compact as do_compact

    cfg = cfg or load_config(project_root)[0]
    con = connect(project_root)
    ensure_session(con, session, mode)
    kernel = Kernel(project_root, session)
    add_message(con, session, "user", user_msg)

    # recall (kibitzer-lite): top-1 hint, verified-not-trusted
    hint = ""
    try:
        hits = M.recall(con, user_msg[:80], 1)
        if hits:
            hint = f"\nrecalled memory: {hits[0]['text'][:200]} [{hits[0].get('source','')}] (verify before relying)"
    except Exception:
        pass

    # Model: explicit provider/model passes through, otherwise the user's
    # configured opencode default (agents inherit; no relay, no router ids).
    try:
        model = backend.resolve_model(model, cfg)
    except RuntimeError as e:
        return {"content": f"no model: {e}", "touched": [], "route_model": ""}

    budgets = dict((cfg.get("budgets", {}) or {}))
    budgets.update(budget_override or {})
    max_turns = int(budgets.get("max_turns", 25))
    msgs = [{"role": "system", "content": SYSTEM.get(mode, SYSTEM["code"]) + hint}]
    msgs += _fresh_history(history(con, session, 30))
    if should_compact(msgs):
        try:
            do_compact(con, cfg, project_root, session, kernel)
            msgs = [{"role": "system", "content": SYSTEM.get(mode, SYSTEM["code"])}] + _fresh_history(history(con, session, 30))
        except Exception:
            pass

    allowlist = (cfg.get("security", {}) or {}).get("shell_allowlist", [])
    touched: list[str] = []
    total_cost = 0.0
    t0 = time.time()
    final = ""
    for _ in range(max_turns):
        try:
            msg = backend.chat(msgs, model=model, cfg=cfg, tools=TOOLS_SCHEMA,
                               workdir=project_root)
        except Exception as e:
            final = f"model backend failed: {e}"
            break
        content = str(msg.get("content", ""))
        tcalls = msg.get("tool_calls") or []
        # Text protocol: [[tool:name {json}]] blocks parsed by models.parse_tool_calls.
        # Empty tool list + empty text = done; otherwise the tool loop below runs.
        if not tcalls:
            final = content
            add_message(con, session, "assistant", final)
            break
        msgs.append({"role": "assistant", "content": content, "tool_calls": tcalls})
        for tc in tcalls[:4]:  # cap fan-out per turn (potato)
            name = (tc.get("function", {}) or {}).get("name", "")
            try:
                import json as _j
                args = _j.loads((tc.get("function", {}) or {}).get("arguments", "{}"))
            except Exception:
                args = {}
            if not _allowed(mode, name):
                msgs.append({"role": "tool", "content": f"denied in {mode} mode: {name}"})
                continue
            try:
                res = _exec_tool(name, args, project_root, session, cfg, con, kernel, allowlist, auto_approve, touched)
            except Exception as e:
                res = f"tool error: {e}"
            text = str(res)
            ccfg = (cfg.get("compress", {}) or {})
            if ccfg.get("enabled", True) and len(text) >= int(ccfg.get("threshold", 4000)):
                from .compress import stacked as _stacked
                label = args.get("cmd", args.get("code", name)) if isinstance(args, dict) else name
                ctext, cstats = _stacked(text, command=str(label),
                                         intensity=str(ccfg.get("intensity", "standard")))
                try:
                    spill = kernel.runs / f"tool-{name}-{int(time.time()*1000)}.log"
                    spill.write_text(text[:200000])
                    trail = f"\n[compressed {cstats['saved_pct']}% by {','.join(cstats['engines'])} ({cstats['filter']}); raw: {spill.name}]"
                except Exception:
                    trail = f"\n[compressed {cstats['saved_pct']}% by {','.join(cstats['engines'])} ({cstats['filter']})]"
                text = ctext + trail
            msgs.append({"role": "tool", "content": text[:4000]})
        if time.time() - t0 > 600:
            final = "stopped: 10m turn budget"
            break
    else:
        final = "stopped: max_turns"
    # inbox flush: surface child completions
    try:
        for m in R.inbox(con)[:2]:
            final += f"\n[child {m['from']}] {m['content'][:500]}"
    except Exception:
        pass
    con.close()
    return {"content": final, "touched": touched, "route_model": model}


def _exec_tool(name, args, root, session, cfg, con, kernel, allowlist, auto_approve, touched) -> str:
    from . import tools as T
    from . import skills as S
    from . import memory as M
    from . import rlm as R
    from . import mcp as MCP
    from .config import get as cget, set_value
    if name == "read":
        return T.read(root, args.get("path", ""), int(args.get("limit", 200)))
    if name == "write":
        r = T.write(root, args["path"], args.get("content", ""))
        touched.append(args["path"])
        return r
    if name == "edit":
        return T.edit(root, args["path"], args.get("old", ""), args.get("new", ""), args.get("hash_id", ""))
    if name == "glob":
        return "\n".join(T.glob(root, args.get("pattern", "*")))
    if name == "grep":
        return "\n".join(T.grep(root, args.get("pattern", ""), args.get("include", "*")))
    if name == "shell":
        out = kernel.bash(args.get("cmd", ""), int(args.get("timeout", 10)), allowlist)
        if isinstance(out, dict):
            return (out.get("output", "") + "\n" + out.get("error", ""))[:4000]
        return f"background pid={out.pid} (poll later)"
    if name == "py":
        r = kernel.execute(args.get("code", ""))
        return (r.get("output", "") + "\n" + r.get("error", ""))[:4000]
    if name == "spawn":
        h = R.spawn(con, cfg, root, args.get("prompt", ""), args.get("name", "worker"),
                    category=args.get("category"), subagent_type=args.get("subagent_type"),
                    load_skills=args.get("load_skills", []))
        return f"spawned {h['rlm_child_id']} ({h['model']}) status={h['status']}"
    if name == "memory":
        op = args.get("op", "recall")
        if op == "recall":
            return str(M.recall(con, args.get("q", ""))[:3])[:2000]
        if op == "save":
            M.save_fact(con, args.get("text", ""), "agent")
            return "saved"
        return "unknown memory op"
    if name == "skill_view":
        return S.view(root, cfg, args.get("name", ""), args.get("path", ""))[:4000]
    if name == "skills_list":
        return str([(s["name"], s["description"][:60]) for s in S.scan(root, cfg)])[:4000]
    if name == "config_get":
        from .config import redact
        return str(cget(redact(cfg), args.get("path", "budgets")))[:2000]
    if name == "config_set":
        return set_value(root, args["path"], args.get("value"), args.get("scope", "user"))
    if name.startswith("mcp_"):
        _, server, tool = name.split("_", 2)
        return str(MCP.call(cfg, server, tool, args, auto_approve))[:3000]
    return f"unknown tool {name}"


TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "read", "description": "read file/dir with LINE# gutter", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "write", "description": "write file (jailed)", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "edit", "description": "exact-match edit with optional LINE#ID guard", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}, "hash_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "glob", "description": "find files by glob", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "grep", "description": "regex search", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "include": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "shell", "description": "run allowlisted command (10s fg then background)", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "py", "description": "persistent python kernel exec", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "spawn", "description": "spawn subagent: exactly one of category|subagent_type", "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}, "name": {"type": "string"}, "category": {"type": "string"}, "subagent_type": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "memory", "description": "recall/save facts", "parameters": {"type": "object", "properties": {"op": {"type": "string"}, "q": {"type": "string"}, "text": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "skill_view", "description": "progressive skill load L1/L2", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "skills_list", "description": "L0 skill index", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "config_get", "description": "read config (redacted)", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "config_set", "description": "set allowlisted config with backup", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "value": {}, "scope": {"type": "string"}}}}},
]
