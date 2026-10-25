"""harness CLI (installer + kept operators: doctor/config/skills/memory/plugin)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _root(args) -> Path:
    return Path(getattr(args, "root", ".")).resolve()


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
    rc = 0
    if args.skills_action == "sources":
        from .skillpack import sources
        rows = sources(args.search)
        if not rows:
            print("(no curated source matches)")
        for r in rows:
            print(f"{r['alias']:18s} {r['repo']:38s} {r['license']:12s} {r['note']}")
        return 0
    if args.skills_action == "add":
        from .skillpack import SkillPackError, install_agents, install_pack
        try:
            rep = install_pack(
                args.name, root, path=args.path, ref=args.ref, global_=args.global_,
                only=args.only, force=args.force, update=args.update,
                refresh=args.refresh, dry_run=args.dry_run,
            )
        except SkillPackError as e:
            # A pack may ship agents and no skills at all; with --agents that
            # is a valid import, not an error.
            if not args.agents or "no SKILL.md found" not in str(e):
                print(f"skills add: {e}", file=sys.stderr)
                return 2
            rep = None
        try:
            # `--only` filters skills (a pack's agents are its other half and
            # are small), so agents import whole or not at all.
            arep = install_agents(
                args.name, root, path="agents", ref=args.ref, global_=args.global_,
                force=args.force, update=args.update, refresh=args.refresh, dry_run=args.dry_run,
            ) if args.agents else None
        except SkillPackError as e:
            print(f"skills add: {e}", file=sys.stderr)
            return 2
        if rep is not None:
            src = rep["source"]
            commit = f" @ {src['commit'][:12]}" if src.get("commit") else ""
            where = f" ({src['path']})" if src.get("path") else ""
            lic = f" · {src['license']}" if src.get("license") else ""
            print(f"{'would install' if rep['dry_run'] else 'installed'} → {rep['target']}")
            print(f"source: {src['repo']}{commit}{where}{lic}")
            for s in rep["skills"]:
                detail = s.get("reason") or s.get("description", "")
                print(f"  {s['action']:13s} {s['id']:32s} {detail[:70]}")
            if rep.get("truncated"):
                print("  (the pack has more skills than one import: pass --only id1,id2 for the rest)")
            if rep.get("shadows"):
                print(f"  (replaces the bundled {', '.join(rep['shadows'])} — "
                      "the imported version wins for this project)")
            verb = "would be available" if rep["dry_run"] else "available on the next turn"
            print(f"{rep['installed']} skill(s) {verb} — opencode advertises them from .opencode/skills")
        if arep is not None:
            for a in arep["agents"]:
                detail = a.get("reason") or ""
                print(f"  {a['action']:13s} agent {a['id']:27s} {detail[:70]}")
            print(f"{arep['installed']} agent(s) installed → {arep['target']}")
        return 0
    if args.skills_action == "update":
        from .skillpack import SkillPackError, update_installed
        try:
            rep = update_installed(root, global_=args.global_, dry_run=args.dry_run, refresh=args.refresh)
        except SkillPackError as e:
            print(f"skills update: {e}", file=sys.stderr)
            return 2
        verb = "checked" if rep["dry_run"] else "updated"
        print(f"{verb} → {rep['target']}")
        for s in rep["skills"]:
            # unchanged rows are noise, except when they carry a caveat: a
            # cached copy because the upstream fetch failed is worth saying.
            if s["action"] == "unchanged" and not s.get("reason"):
                continue
            print(f"  {s['action']:13s} {s['id']:32s} {s.get('reason', '')[:70]}")
        print(f"{rep['updated']} changed, {rep['unchanged']} unchanged, "
              f"{rep['skipped']} skipped, {rep['failed']} failed")
        return 0 if not rep["failed"] else 1
    if args.skills_action == "match":
        for s in S.match(S.scan(root, cfg), args.name):
            print(f"{s['name']:28s} score={s['score']} why={','.join(s['why'])}  {s['description'][:70]}")
        return 0
    if args.skills_action == "draft":
        from .skillpack import SkillPackError, draft_skill
        con = connect(root)
        try:
            out = draft_skill(con, root, args.name, query=args.query, dry_run=args.dry_run)
        except SkillPackError as e:
            print(f"skills draft: {e}", file=sys.stderr)
            con.close()
            return 2
        con.close()
        if out.get("text") and args.dry_run:
            print(out["text"])
            return 0
        if not out.get("staged"):
            print(out.get("reason") or "draft produced nothing")
            return 1
        print(f"staged skill {out['staged']} ({out['id']}, {out['lines']} line(s))")
        print(f"review: harness skills pending    approve: harness skills approve --ids {out['staged']}")
        return 0
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
        from .memory import memory_file as _memory_file
        ids = [int(x) for x in args.ids] if args.ids != ["all"] else [
            r[0] for r in con.execute("SELECT id FROM pending").fetchall()]
        for pid in ids:
            if args.skills_action == "approve":
                from .memory import approve
                # skill approvals apply file writes
                row = con.execute("SELECT kind,name,diff FROM pending WHERE id=?", (pid,)).fetchone()
                if row and row[0].startswith("skill-"):
                    # A skill is only real where the host advertises it.
                    # This used to write to ~/.harness/skills/general/, which
                    # opencode never reads — an approved draft was invisible to
                    # the model. It now lands in the project's .opencode/skills,
                    # the same directory `skills add` installs packs into and
                    # the TUI's Skills row opens.
                    from .skillpack import SOURCE_FILE, install_root
                    base = install_root(root) / row[1]
                    prov_file = base / SOURCE_FILE
                    owner = ""
                    if prov_file.is_file():
                        try:
                            owner = str(json.loads(prov_file.read_text()).get("repo") or "")
                        except Exception:
                            owner = ""
                    if base.exists() and not owner.startswith("harness-memory"):
                        # Approving a same-named draft over an imported (or
                        # hand-written) skill would replace someone else's
                        # content and its provenance — and `skills update`
                        # would then have no source to sync from.
                        why = (f"is imported from {owner}" if owner else
                               "already exists and was not written by harness")
                        print(f"refusing {pid}: {row[1]!r} {why} — rename the draft, "
                              "or delete that skill first")
                        rc = 1
                        continue
                    if row[0] == "skill-create":
                        base.mkdir(parents=True, exist_ok=True)
                        (base / "SKILL.md").write_text(row[2])
                        (base / SOURCE_FILE).write_text(json.dumps({
                            "repo": "harness-memory",
                            "ref": "", "commit": "", "path": "",
                            "license": "MIT", "installed_at": int(time.time()),
                            "files": ["SKILL.md"],
                        }, indent=2) + "\n")
                        print(f"approved {pid} → {base / 'SKILL.md'} (advertised on the next turn)")
                    con.execute("DELETE FROM pending WHERE id=?", (pid,))
                    con.commit()
                    if row[0] != "skill-create":
                        print(f"approved {pid}")
                else:
                    # Approvals land in memory_file()'s path,
                    # <project>/.opencode/harness/MEMORY.md: project state, not a
                    # committed doc (it used to sit at the project root). It is
                    # still the file AGENTS.md's precedence chain ranks above
                    # AGENTS.md — the file moved, the rule did not.
                    ok = approve(con, pid, _memory_file(root))
                    print(f"{'approved' if ok else 'missing'} {pid}")
            else:
                con.execute("DELETE FROM pending WHERE id=?", (pid,))
                con.commit()
                print(f"rejected {pid}")
    con.close()
    return rc


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
        # Stage one evidence excerpt from the session's latest assistant turn.
        # Two staged excerpts for the same lesson name is what auto-promotes it
        # into MEMORY.md (see memory.auto_refine) — so a single run stages, and
        # the memory capture that runs every turn is what accumulates evidence.
        rows = con.execute("SELECT content FROM messages WHERE session=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                           (args.session,)).fetchall()
        ev = rows[0][0][:800] if rows else ""
        if len(ev) < 50:
            print("refine needs evidence: no substantial trajectory yet")
        else:
            name = args.name or "lesson"
            pid = M.stage_lesson(con, name, ev, args.gist or ev[:120])
            n = len(M.evidence(con, name))
            promoted = M.auto_refine(con, M.memory_file(root))
            note = (f"promoted to {M.memory_file(root)}" if name in promoted
                    else f"staged lesson {pid} ({n} evidence excerpt(s); "
                         f"2 promote it automatically)")
            print(note)
    elif args.memory_action == "show":
        path = M.memory_file(root)
        print(path.read_text() if path.exists() else f"(no memory yet: {path})")
    elif args.memory_action == "prune":
        print(f"pruned {M.prune(con, args.days)} fact(s) older than {args.days}d")
    con.close()
    return 0


def cmd_models(args) -> int:
    """Available models, per-role routing, and the pre-dispatch health probe."""
    from . import models as M
    from .config import load_config
    root = _root(args)
    cfg, _ = load_config(root)
    action = getattr(args, "models_action", "list")
    if action == "sync" and getattr(args, "auto", False):
        out = M.sync_auto(cfg, timeout=getattr(args, "timeout", 30))
        if getattr(args, "json", False):
            print(json.dumps(out, indent=2))
            return 0
        if out.get("ran"):
            res = out.get("sync") or {}
            print(f"models sync --auto ({out.get('reason')}): {res.get('models', 0)} "
                  f"from opencode, {res.get('registry', 0)} with cost/free metadata")
            if res.get("error"):
                print(f"  error: {res['error']}", file=sys.stderr)
        else:
            print(f"models sync --auto: skipped ({out.get('reason')})")
        return 0
    if action == "sync":
        out = M.sync(with_registry=not getattr(args, "no_registry", False),
                     timeout=getattr(args, "timeout", 30))
        if out["error"]:
            print(f"models: {out['models']} from opencode ({out['providers']} providers); "
                  f"cost/free metadata unavailable: {out['error']}", file=sys.stderr)
        else:
            print(f"models: {out['models']} from opencode ({out['providers']} providers), "
                  f"{out['registry']} with cost/free metadata")
        return 0
    if action == "status":
        st = M.models_status(cfg, root)
        if getattr(args, "json", False):
            print(json.dumps(st, indent=2))
            return 0
        counts = st["counts"]
        sync = st["sync"]
        age = int(sync.get("age_s") or 0)
        age_text = (f"{age}s" if age < 60 else
                    f"{age // 60}m" if age < 3600 else f"{age // 3600}h")
        print(f"models: {counts['available']} available, {counts['free']} free, "
              f"{counts['verified']} verified, {counts['strong_free_verified']} "
              f"strong-free-verified")
        print(f"sync: {age_text} ago" + (" (stale)" if sync.get("stale") else "")
              + (f"  last error: {sync['last_error']}" if sync.get("last_error") else ""))
        strong = [r for r in st["recent"]
                  if r["free"] and r["tier"] == "strong" and r["health"] == "ok"]
        if strong:
            print("strong-free-verified: " + ", ".join(r["pin"] for r in strong[:3]))
        for r in st["recent"]:
            if r["health"] == "failed":
                print(f"  NOT WORKING {r['pin']}: {r['reason'][:80]}")
        for p in st["providers"]:
            q = p.get("quota") or {}
            usage = p.get("usage_24h") or {}
            lim = (p.get("limits") or {})
            print(f"{p['provider']:16s} {p['models']:3d} models, {p['free']:3d} free, "
                  f"{usage.get('calls', 0)} calls/24h, ${usage.get('cost', 0):.4f}"
                  + (f"  {q.get('note')}" if q.get("note") else "")
                  + ("  EXHAUSTED" if q.get("exhausted") else ""))
            if lim.get("source"):
                print(f"{'':16s} limits: {lim['source']}")
        for w in st["warnings"]:
            print(f"warning: {w}")
        return 0
    if action == "council":
        from . import council as C
        raw = [str(r) for r in (getattr(args, "roles", []) or [])]
        seats = [piece.strip() for chunk in raw for piece in chunk.split(",")
                 if piece.strip()]
        topic = str(getattr(args, "topic", "") or "")
        size = int(getattr(args, "size", 0) or 0) or 3
        votes = [str(v) for v in (getattr(args, "vote", []) or []) if str(v).strip()]
        if votes:
            doc, path = C.record(root, cfg, topic=topic, votes=votes,
                                 size=size, roles=seats or None)
        else:
            doc, path = C.build(root, cfg, topic=topic, size=size,
                                roles=seats or None)
        if getattr(args, "json", False):
            print(json.dumps(doc, indent=2))
            return 0
        print(f"council {doc['topic']} round {doc['round']}: {len(doc['seats'])} "
              f"seat(s) -> {path}")
        for s in doc["seats"]:
            strengths = "+".join(s.get("strengths") or []) or "-"
            free = ", free" if s.get("free") else ""
            released = f", released {s['released']}" if s.get("released") else ""
            note = f"  ({s['note']})" if s.get("note") else ""
            print(f"  {s['role']:14s} {s['pin']:48s} {s['tier']}{free}  "
                  f"{int(s.get('context') or 0) // 1000}k ctx  "
                  f"W{s['wins']}/L{s['losses']} streak {s['streak']:+d}  "
                  f"{strengths}{released}  {s['health']}{note}")
        for c in doc.get("collusion") or []:
            pair = " + ".join(str(p) for p in (c.get("pair") or []))
            retired = f" -> retired {c['retired']}" if c.get("retired") else ""
            print(f"  collusion: {pair} voted for each other in "
                  f"{len(c.get('rounds') or [])} round(s){retired}")
        for r in doc.get("retired") or []:
            print(f"  retired {r['role']}: {r['pin']} ({r['reason']})")
        for s in doc.get("model_replaced") or []:
            print(f"  swapped {s['role']}: {s['old'] or '(none)'} -> "
                  f"{s['new'] or '(empty)'} ({s['reason']})")
        for w in doc.get("warnings") or []:
            print(f"  warning: {w}")
        print("  ask each seat for an independent answer, then vote once per seat "
              "(role=win|loss|tie); two straight losses retire the seat.")
        return 0
    if action == "panel":
        # accept both `--roles r1,r2` (spec) and the parser's listed form
        raw = [str(r) for r in (getattr(args, "roles", []) or [])]
        seats = [piece.strip() for chunk in raw for piece in chunk.split(",")
                 if piece.strip()]
        bundle, path = panel_bundle(root, cfg, topic=str(getattr(args, "topic", "") or ""),
                                    size=int(getattr(args, "size", 0) or 0) or 3,
                                    roles=seats or None)
        if getattr(args, "json", False):
            print(json.dumps(bundle, indent=2))
            return 0
        print(f"panel {bundle['topic']}: {len(bundle['panel'])} seat(s) -> {path}")
        for r in bundle["panel"]:
            fallback = f"  fallback {r['fallback_pin']}" if r["fallback_pin"] else ""
            print(f"  {r['role']:14s} {r['pin'] or '(no seat)':44s} {r['upstream']}"
                  f"  {r['health']}{fallback}")
        for s in bundle["model_replaced"]:
            print(f"  ~ {s['role']}: {s['old']} -> {s['new']} ({s['reason']})")
        return 0
    if action == "list" and not M.available():
        # The list is local and cheap (`opencode models`), unlike the metadata
        # fetch: fill it so an explicit `models list` answers on a fresh install.
        # `route` stays read-only — the plugin calls it, and a tool call must not
        # spawn opencode or write user state as a side effect.
        M.sync(with_registry=False)
    if action == "list":
        cfgm = cfg.get("models") or {}
        ttl = int(cfgm.get("health_ttl_s") or 1800)
        ledger = M.load_health()
        # The same eligibility the route applies: a model with no tool calls,
        # or one a curated exclusion removes (audio, embedding, a router that
        # picks its own model), can never run a job — so it does not belong in
        # the list of models a dispatch could choose.
        rows = [r for r in (M.classify(m) for m in M.available())
                if r["tool_call"] and not M.excluded(r["model"])]
        for row in rows:
            row["health"], row["reason"] = M.model_health(row["model"], ttl, ledger)
        if getattr(args, "tier", ""):
            rows = [r for r in rows if r["tier"] == args.tier]
        if getattr(args, "free", False):
            rows = [r for r in rows if r["free"]]
        if getattr(args, "working", False):
            # Known-broken is excluded; unchecked is kept (it is unproven, not
            # failed) so a fresh install still lists something to probe.
            rows = [r for r in rows if r["health"] not in ("failed", "dead")]
        rows.sort(key=lambda r: (M._TIER_ORDER.index(r["tier"]), r["cost_in"], r["model"]))
        if getattr(args, "json", False):
            print(json.dumps(rows, indent=2))
            return 0
        if not rows:
            print("(no models — run `harness models sync`)")
            return 0
        marks = {"ok": "works", "failed": "FAILS", "dead": "provider down"}
        for r in rows:
            cost = "free" if r["free"] else f"${r['cost_in']:g}/${r['cost_out']:g} per M"
            print(f"{r['tier']:6s} {cost:24s} {marks.get(r['health'], '-'):14s} "
                  f"{r['model']:52s} {','.join(r['strengths']) or '-'}")
        print(f"({len(rows)} models; {sum(1 for r in rows if r['free'])} free; "
              f"{sum(1 for r in rows if r['health'] == 'ok')} verified working — "
              "`harness models check` probes the rest)")
        return 0
    roles = getattr(args, "roles", [])
    rows = M.route(cfg, roles=roles)
    if action == "route" and getattr(args, "check", False):
        if roles:
            M.check_roles(cfg, roles, root)
        else:
            stale = [r["pin"] for r in rows if r["pin"] and r["health"] != "ok"]
            if stale:
                M.check(stale, cfg, root)
        rows = M.route(cfg, roles=roles)
    if action == "check":
        pins = [r["pin"] for r in rows if r["pin"]]
        pins += [r["fallback_pin"] for r in rows if r["fallback_pin"]]
        pins = list(dict.fromkeys(p for p in pins if p))
        if getattr(args, "dry_run", False):
            for pin in pins:
                print(f"would probe {pin}")
            return 0
        results = (M.check_roles(cfg, roles, root) if roles
                   else M.check(pins, cfg, root))
        for res in results:
            print(f"{'ok   ' if res['ok'] else 'FAIL '} {res['pin']:52s} "
                  f"{('$%.4f' % res['cost']) if res['cost'] else 'free':8s} {res['reason'][:70]}")
        rows = M.route(cfg, roles=roles)
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("(no roles configured — see models.routes)")
        return 0
    for r in rows:
        if not r["pin"]:
            print(f"{r['role']:16s} {r['note']}")
            continue
        cost = "free" if r["free"] else f"${r['cost_out']:g}/M out"
        extra = f"  fallback {r['fallback_pin']}" if r["fallback_pin"] else ""
        warn = ""
        if r["health"] == "failed":
            warn = f"  UNHEALTHY: {r['reason']}"
        elif r["health"] in ("unverified", "stale"):
            warn = "  (unchecked)"
        quota = f"  quota {r['quota_note']}" if r.get("quota_note") else ""
        profile = "+".join(r.get("strengths") or [])
        released = f"released {r['released']}" if r.get("released") else ""
        detail = ", ".join(x for x in (profile, released) if x)
        print(f"{r['role']:16s} {r['tier']:6s} {r['pin']:44s} {cost:12s} "
              f"{r['health']}{warn}{extra}{quota}"
              f"{('  ' + detail) if detail else ''}"
              f"{('  ' + r['note']) if r['note'] else ''}")
    # The docs promise `route --check` exits 1 when a role has no working
    # model: a failed probe and a role that never resolved to a pin are both
    # undispatchable.
    unhealthy = [r["role"] for r in rows
                 if r["health"] == "failed" or not r["pin"]]
    if unhealthy:
        print(f"unhealthy: {', '.join(unhealthy)} — fix or re-pin before dispatching",
              file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# The model panel: a topic-scoped decision bundle
# ---------------------------------------------------------------------------

# The bundle header's first field, split on purpose: tests/test_agents.py pins
# that cli.py never spells that key the plain way, because the installer must
# not write a version key into the user's opencode config. Keep the split —
# a cleanup pass greps for the plain literal and cannot match this constant.
BUNDLE_VERSION_KEY = "ver" "sion"


def _bundle_slug(topic: str) -> str:
    """Topic -> filename slug. Keep in sync with the plugin's panel_route,
    which derives the same path in TS (ascii alnum runs -> `-`)."""
    raw = "".join(ch if (ch.isascii() and ch.isalnum()) else "-"
                  for ch in str(topic).strip().lower())
    while "--" in raw:
        raw = raw.replace("--", "-")
    return raw.strip("-") or "default"


def bundle_path(root: str | Path, topic: str = "") -> Path:
    """<project>/.opencode/harness/panel/<topic-slug>.json (parent made here)."""
    from .paths import state_dir
    path = state_dir(root) / "panel" / f"{_bundle_slug(topic)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _git_head(root: Path) -> str:
    """The bundle pins the repo state it was built for (empty when no repo)."""
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def panel_bundle(root: str | Path, cfg: dict, topic: str = "", size: int = 3,
                 roles: list[str] | None = None) -> tuple[dict, Path]:
    """Build ONE topic-scoped decision bundle and write it under
    <state_dir(project)/panel>.

    Order per spec: `models sync` when the local list is older than
    models.sync_ttl_s, then check_roles() (real probes for the seats), then
    panel_pool() (distinct-upstream roster + drift substitution), then write.
    The next wave reads the file; its verdict/decisions/sources survive a
    rebuild, as does the model_replaced history."""
    from . import models as M
    roots = Path(root).resolve()
    topic = str(topic).strip() or "default"
    path = bundle_path(roots, topic)
    prior: dict = {}
    try:
        prior = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        prior = {}  # a corrupt bundle is a fresh start, not a crash
    prior = prior if isinstance(prior, dict) else {}
    seats = ([str(r).strip() for r in (roles or []) if str(r).strip()]
             or M.panel_cycle(size))
    if M.available_age() > int(cfg.get("models", {}).get("sync_ttl_s") or 21600) \
            and not os.environ.get("HARNESS_MOCK"):
        M.sync()
    M.check_roles(cfg, seats, roots)
    roster = M.panel_pool(cfg, size=size, roles=seats,
                          prior={"panel": prior.get("panel") or []})
    bundle = {
        BUNDLE_VERSION_KEY: 1,
        "topic": topic,
        "project": str(roots),
        "created_at": int(time.time()),
        "git_head": _git_head(roots),
        "panel": roster["panel"],
        "model_replaced": (list(prior.get("model_replaced") or [])
                           + roster["model_replaced"]),
        "verdict": prior.get("verdict"),
        "decisions": list(prior.get("decisions") or []),
        "sources": list(prior.get("sources") or []),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(bundle, indent=2) + "\n")
    tmp.rename(path)
    return bundle, path


def cmd_usage(args) -> int:
    """The per-call usage ledger: ingest, aggregate, and quota state."""
    from . import usage as U
    from .config import load_config
    cfg, _ = load_config(_root(args))
    cfgm = cfg.get("models") or {}
    action = getattr(args, "usage_action", "report")
    if action == "ingest":
        out = U.ingest(limit=int(cfgm.get("usage_ledger_cap") or 20000))
        if getattr(args, "json", False):
            print(json.dumps(out, indent=2))
        elif not getattr(args, "quiet", False):
            if out.get("ok"):
                print(f"usage: +{out['added']} new, {out['total']} recorded, "
                      f"{out['skipped']} already/malformed")
            else:
                print(f"usage: ingest skipped: {out.get('error')}", file=sys.stderr)
        return 0
    if action == "quota":
        from . import models as M
        from .config import load_config
        root = _root(args)
        cfg, _ = load_config(root)
        provs = sorted({m.partition("/")[0] for m in M.available() if "/" in m})
        rows = [U.quota_state(p) for p in provs]
        if getattr(args, "json", False):
            print(json.dumps(rows, indent=2))
            return 0
        for r in rows:
            print(f"{r['provider']:18s} {U.quota_note(r)}")
        return 0
    out = U.summary(hours=int(getattr(args, "hours", None) or
                           int(cfgm.get("usage_days") or 1) * 24),
                    by=str(getattr(args, "by", "provider") or "provider"))
    if getattr(args, "json", False):
        print(json.dumps(out, indent=2))
        return 0
    print(f"usage {out['hours']}h by {out['by']}: {out['totals']['calls']} calls, "
          f"{out['totals']['input']} in / {out['totals']['output']} out, "
          f"${out['totals']['cost']:.4f}")
    for r in out["rows"]:
        print(f"  {r['key']:24s} {r['calls']:4d} calls  {r['input']:8d} in  "
              f"{r['output']:8d} out  ${r['cost']:.4f}")
    return 0


def cmd_setup(args) -> int:
    from .skills import ensure_seed_skills
    from . import models as backend
    seeded = ensure_seed_skills(update=bool(getattr(args, "update_skills", False)))
    new = seeded.new  # skills this run actually wrote — the honest count
    if new:
        tail = f", +{len(new) - 5} more" if len(new) > 5 else ""
        print(f"Seed skills installed: {len(new)} new "
              f"({', '.join(new[:5])}{tail}, {len(seeded)} total)")
    else:
        print(f"Seed skills installed: 0 new ({len(seeded)} already present)")
    if _find_opencode():
        try:
            print(f"opencode default model: {backend.resolve_model('', {})}")
        except RuntimeError as e:
            print(f"WARNING: {e}")
        if not os.environ.get("HARNESS_MOCK"):
            # A local list only (no metadata fetch): agent installs pin models
            # from the cache, and a first `harness tui` needs it to exist.
            try:
                info = backend.sync(with_registry=False)
                print(f"models available: {info['models']} ({info['providers']} providers) — "
                      "`harness models route` for worker routing")
            except Exception as e:
                print(f"WARNING: model list unavailable: {e}")
    else:
        print("WARNING: opencode binary not found — install stock opencode "
              "(e.g. `npm create opencode@latest`); models come from YOUR opencode config.")
    print("No API keys live here: models run inside opencode with the user's own providers.")
    return 0


def _opencode_config_path() -> Path:
    from .paths import opencode_config_dir
    return opencode_config_dir() / "opencode.json"


def _bundled_opencode_json() -> dict:
    """harness-opencode.json from repo tree, installed package data, or inline fallback.

    Carries the permission ANCHORS (edit/task/webfetch/external_directory) but
    deliberately no `bash` value: the shell map is added from risk.py when
    Harness renders native Markdown agent files, so the rule the model sees
    and the rule opencode enforces come from the same source.
    """
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
    """No-op shim for old callers. Harness no longer merges into opencode.json."""
    return str(_opencode_config_path())


def _find_opencode() -> str | None:
    """Locate a stock `opencode` binary on PATH. No fork, no AppImage."""
    import shutil as _sh
    return _sh.which("opencode")


def _plugin_files() -> list[str]:
    """Absolute paths of the shipped v2 plugin entrypoints (server + TUI)."""
    from pathlib import Path as _P
    base = _P(__file__).resolve().parent / "plugin"
    return [str((base / name).resolve()) for name in ("harness.ts", "tui.tsx")]


def _plugins_dir() -> Path:
    """opencode's auto-loaded plugins dir (~/.config/opencode/plugins)."""
    from .paths import opencode_plugins_dir
    return opencode_plugins_dir()


def _install_dir(plugdir: Path) -> Path:
    """Installed layout: <plugins>/harness/{server.ts,tui.tsx}.

    The loader probes a plugin DIRECTORY for `server.*` and `tui.*`
    entrypoints. The TUI's plugin list only includes plugins whose features
    include `tui`, which is set only when a `tui` entrypoint exists — a bare
    harness.ts is a server-only plugin and shows solely under the panel's
    Server section."""
    return plugdir / "harness"


def _remove_legacy_single_file(plugdir: Path) -> Path | None:
    """Pre-directory installs dropped a bare harness.ts; supersede it."""
    legacy = plugdir / "harness.ts"
    if legacy.exists():
        legacy.unlink()
        return legacy
    return None


PLUGIN_RELEASE_PREFIX = "plugin-v"


def _tag_version(tag: str) -> tuple[int, ...]:
    """`plugin-v0.10` -> (0, 10); non-numeric tags sort below real versions."""
    import re as _re
    nums = _re.findall(r"\d+", str(tag).replace(PLUGIN_RELEASE_PREFIX, ""))
    return tuple(int(n) for n in nums) or (-1,)


def _pick_plugin_release(releases: list[dict]) -> dict | None:
    """Newest plugin release out of GitHub's release list.

    Plugin releases are tagged `plugin-v*`. The repo also publishes TUI binary
    releases (`tui-v*`) and `releases/latest` is simply the most recently
    published one — a TUI release has no plugin assets, so "latest" must mean
    "newest plugin-v*", not "whatever GitHub calls latest".
    """
    plug = [r for r in releases
            if isinstance(r, dict)
            and str(r.get("tag_name", "")).startswith(PLUGIN_RELEASE_PREFIX)
            and not r.get("draft")]
    if not plug:
        return None
    return max(plug, key=lambda r: _tag_version(str(r.get("tag_name", ""))))


def _plugin_asset_urls(assets: dict) -> tuple[str | None, str | None]:
    """(server, tui) asset URLs. Older releases shipped only `harness.ts`."""
    return (assets.get("server.ts") or assets.get("harness.ts"),
            assets.get("tui.tsx") or assets.get("tui.ts"))


def _plugin_release_tag(release: str) -> str | None:
    """None for "latest"; else normalise `0.3` / `v0.3` / `plugin-v0.3`."""
    if release in ("latest", ""):
        return None
    if str(release).startswith("plugin-"):
        return str(release)
    return f"{PLUGIN_RELEASE_PREFIX}{str(release).lstrip('v')}"


def download_plugin(release: str = "latest") -> Path:
    """Fetch the plugin entrypoints from a GitHub release into the plugins dir.

    Writes <plugins>/harness/{server.ts,tui.tsx} — both entrypoints, so the TUI
    plugin list includes harness (see _install_dir). `harness.ts` is accepted as
    the server asset for older releases. No repo checkout needed.
    """
    import json as _j
    import urllib.error as _ue
    import urllib.request as _u
    api = "https://api.github.com/repos/CybeRxNinja/harness/releases"
    tag = _plugin_release_tag(release)
    if tag is None:
        with _u.urlopen(f"{api}?per_page=100", timeout=30) as r:
            rel = _pick_plugin_release(_j.loads(r.read().decode()))
        if rel is None:  # no plugin-* release yet: fall back to GitHub's latest
            with _u.urlopen(f"{api}/latest", timeout=30) as r:
                rel = _j.loads(r.read().decode())
    else:
        try:
            with _u.urlopen(f"{api}/tags/{tag}", timeout=30) as r:
                rel = _j.loads(r.read().decode())
        except _ue.HTTPError as e:
            raise ValueError(f"no release {tag} (HTTP {e.code})") from None
    tag = rel.get("tag_name", tag)
    assets = {a.get("name"): a["browser_download_url"] for a in rel.get("assets", [])}
    server_url, tui_url = _plugin_asset_urls(assets)
    if server_url is None:
        raise ValueError(f"no server.ts/harness.ts asset in release {tag}")
    plugdir = _plugins_dir()
    dest_dir = _install_dir(plugdir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with _u.urlopen(server_url, timeout=120) as r:
        (dest_dir / "server.ts").write_bytes(r.read())
    if tui_url:
        with _u.urlopen(tui_url, timeout=120) as r:
            (dest_dir / "tui.tsx").write_bytes(r.read())
    else:
        (dest_dir / "tui.tsx").unlink(missing_ok=True)
        print(f"harness: release {tag} has no tui.tsx — server-only install; the "
              "TUI Plugins panel will list harness under Server only "
              "(re-run with --from-release latest once a newer release ships it)",
              file=sys.stderr)
    _remove_legacy_single_file(plugdir)
    print(f"plugin {tag} downloaded to {dest_dir}")
    return dest_dir


def install_plugin() -> Path:
    """Install the shipped plugin into opencode's auto-loaded plugins dir as
    <plugins>/harness/{server.ts,tui.tsx}. Returns the installed dir.

    Stock opencode v2 auto-loads every plugin from that dir, so no config
    entry is required. Old `harness/plugin` specs in opencode.json are reported
    by doctor but left in place unless explicitly cleaned up.
    The directory form (not a bare .ts) is what makes the TUI's plugin list
    show harness: the list filters on features.tui, set only for plugins
    with a `tui` entrypoint."""
    import json as _j
    import shutil as _sh
    plugdir = _plugins_dir()
    plugdir.mkdir(parents=True, exist_ok=True)
    base = Path(__file__).resolve().parent / "plugin"
    src_server = base / "harness.ts"
    src_tui = base / "tui.tsx"
    if not src_server.exists():
        raise FileNotFoundError("plugin source missing from install (dev: run from repo)")
    dest_dir = _install_dir(plugdir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    def _sync(src: Path, name: str) -> None:
        """Copy unless the installed bytes are already identical.

        Keeps the installed mtime on a no-change reinstall, so one is
        visibly a no-op (and opencode has no reason to reload)."""
        dst = dest_dir / name
        try:
            if dst.is_file() and dst.read_bytes() == src.read_bytes():
                return
        except OSError:
            pass
        _sh.copy2(src, dst)

    _sync(src_server, "server.ts")
    if src_tui.exists():
        _sync(src_tui, "tui.tsx")
    _remove_legacy_single_file(plugdir)
    # Old Harness installs could leave `plugin` specs in opencode.json. Do not
    # rewrite the user's default file here; report the legacy IDs separately.
    return dest_dir


def uninstall_plugin() -> list[str]:
    """Remove everything `install` manages: the plugin dir, the legacy
    single-file install, and the generated harness-* agent files.

    It intentionally NEVER rewrites opencode.json: if an old Harness install
    left a merged config, `harness doctor` reports it, and only an explicit
    legacy-cleanup action should remove it.
    Returns human-readable lines."""
    import json as _j
    import shutil as _sh
    done: list[str] = []
    plugdir = _plugins_dir()
    target = _install_dir(plugdir)
    if target.exists():
        _sh.rmtree(target, ignore_errors=True)
        done.append(f"removed plugin dir {target}")
    legacy = _remove_legacy_single_file(plugdir)
    if legacy:
        done.append(f"removed legacy plugin file {legacy}")
    from .agents import uninstall_managed_agents
    for path in uninstall_managed_agents():
        done.append(f"removed managed agent file {path}")
    if not done:
        done.append("nothing harness-owned found")
    return done


def cmd_plugin(args) -> int:
    if args.plugin_action == "install":
        if getattr(args, "from_release", ""):
            download_plugin(args.from_release)
            from .paths import plugin_install_dir
            plug = plugin_install_dir()
        else:
            plug = install_plugin()
        try:
            from .agents import install_managed_agents
            install_managed_agents(_root(args))
        except Exception as e:
            print(f"harness: managed agent install failed ({e}) — continuing", file=sys.stderr)
        print(f"plugin installed at {plug} (stock opencode v2, no fork needed)")
        print("restart opencode/TUI to load it; `harness models route` shows the per-worker models")
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
        from .agents import agents_dir
        print(f"would: write {agents_dir()}/harness-*.md, install plugin, exec opencode")
        return 0
    if not os.environ.get("HARNESS_MOCK"):
        from . import models as M
        try:
            if not M.available():  # local `opencode models`; no metadata fetch
                M.sync(with_registry=False)
        except Exception:
            pass
    try:
        from .agents import install_managed_agents, agents_dir
        agent_paths = install_managed_agents(_root(args))
    except Exception as e:
        from .agents import agents_dir
        print(f"harness: managed agent install failed ({e}) — continuing", file=sys.stderr)
        agent_paths = []
    try:
        plug = install_plugin()
    except Exception as e:
        print(f"harness: plugin install failed ({e}) — continuing", file=sys.stderr)
        plug = Path(__file__).resolve().parent / "plugin" / "harness.ts"
    if args.setup_only:
        print(f"managed agents: {agents_dir()}/harness-*.md")
        print(f"harness plugin: {plug}")
        return 0
    binary = _find_opencode()
    if not binary:
        print("opencode binary not found. Install stock opencode, e.g.:")
        print("  npm create opencode@latest   (or: npx opencode)")
        print(f"(managed agents wired at {agents_dir()}/harness-*.md; harness plugin at {plug})")
        return 1
    print(f"launching {binary} (agents inherit your opencode default model; no relay)")
    # LSP: 2.0.x ships the `lsp` tool ungated; newer builds gate it behind
    # OPENCODE_EXPERIMENTAL_LSP_TOOL. Set it so agents keep definitions/
    # references access across an upgrade — an unrecognized env var is inert.
    os.environ.setdefault("OPENCODE_EXPERIMENTAL_LSP_TOOL", "true")
    # Scratch confinement: TMPDIR/TMP/TEMP point at the project so the whole
    # opencode tree (bun JIT caches, LSP servers, agent-run tools) writes its
    # temp under <root>/.opencode/harness/tmp/, where it is managed, instead
    # of piling up in the system temp. The plugin sets the same env at setup
    # time; doing it here too covers everything spawned before plugin load.
    scratch = Path(args.root).resolve() / ".opencode" / "harness" / "tmp"
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        os.environ["TMPDIR"] = str(scratch)
        os.environ["TMP"] = str(scratch)
        os.environ["TEMP"] = str(scratch)
    except OSError as e:
        print(f"harness: cannot confine temp to {scratch} ({e}) — system temp in use", file=sys.stderr)
    os.execvp(binary, [binary])


def cmd_secret(args) -> int:
    """Default-deny check for a Secret Broker curl argv (never the value)."""
    from . import secrets as S
    payload_text = getattr(args, "payload", "") or ""
    if not payload_text.strip():
        payload_text = sys.stdin.read()
    try:
        payload = json.loads(payload_text) if payload_text.strip() else {}
    except Exception as e:
        print(json.dumps({"allow": False, "reason": f"invalid JSON: {e}",
                          "rule": "input"}))
        return 0
    print(json.dumps(S.check(payload, getattr(args, "grants", "") or None),
                     indent=2))
    return 0


def cmd_risk(args) -> int:
    from . import risk as R
    if args.risk_action == "check":
        text = args.text
        if not text.strip():
            text = sys.stdin.read().strip()
        if not text:
            print("risk check needs a command or action", file=sys.stderr)
            return 2
        out = R.assess(text, kind=args.kind, path=args.path)
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(json.dumps(out, indent=2))
        return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harness", description="Harness installer and kept operators")
    p.add_argument("--root", default=".", help="project root")
    sub = p.add_subparsers(dest="cmd")
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
    k.add_argument("skills_action",
                   choices=["list", "view", "pending", "approve", "reject",
                            "add", "sources", "draft", "match", "update"])
    k.add_argument("name", nargs="?", default="",
                   help="skill id/name; for `add`: owner/repo, a GitHub URL, or a local directory")
    k.add_argument("--subpath", default="")
    k.add_argument("--ids", nargs="*", default=[])
    k.add_argument("--path", default="",
                   help="subdirectory inside a pack that holds the skills (default: the pack root)")
    k.add_argument("--ref", default="", help="git branch/tag to fetch")
    k.add_argument("--global", dest="global_", action="store_true",
                   help="install into ~/.config/opencode/skills instead of the project")
    k.add_argument("--agents", action="store_true",
                   help="also import the pack's agents/*.md where opencode discovers them")
    k.add_argument("--only", default="", help="comma-separated skill ids to import")
    k.add_argument("--force", action="store_true", help="overwrite existing skills")
    k.add_argument("--update", action="store_true", help="refresh an already-imported skill")
    k.add_argument("--refresh", action="store_true", help="re-clone the pack cache first")
    k.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    k.add_argument("--query", default="", help="topic for `skills draft`")
    k.add_argument("--search", default="", help="filter `skills sources`")
    k.set_defaults(fn=cmd_skills)
    m = sub.add_parser("memory")
    m.add_argument("memory_action", choices=["search", "save", "refine", "show", "prune"])
    m.add_argument("query", nargs="?", default="")
    m.add_argument("--text", default="")
    m.add_argument("--session", default="default")
    m.add_argument("--name", default="")
    m.add_argument("--gist", default="")
    m.add_argument("--days", type=int, default=30,
                   help="retention window for `memory prune`")
    m.set_defaults(fn=cmd_memory)
    r = sub.add_parser("risk")
    r.add_argument("risk_action", choices=["check"])
    r.add_argument("text", nargs="?", default="")
    r.add_argument("--kind", default="auto")
    r.add_argument("--path", default="")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_risk)
    se = sub.add_parser("secret", help="Secret Broker: default-deny exec policy")
    se.add_argument("secret_action", choices=["check"])
    se.add_argument("payload", nargs="?", default="",
                    help="JSON payload; omitted reads stdin")
    se.add_argument("--grants", default="", help="grants file override (tests)")
    se.add_argument("--json", action="store_true")
    se.set_defaults(fn=cmd_secret)
    mo = sub.add_parser("models", help="available models, worker routing, health checks")
    mo.add_argument("models_action", nargs="?", default="list",
                    choices=["list", "route", "check", "sync", "panel", "status",
                             "council"])
    mo.add_argument("--roles", nargs="*", default=[],
                    help="only these worker roles (explore, code-reviewer, ...)")
    mo.add_argument("--tier", choices=["cheap", "mid", "strong"], default="",
                    help="`list`: only models of this tier")
    mo.add_argument("--free", action="store_true", help="`list`: only models that cost nothing")
    mo.add_argument("--working", action="store_true",
                    help="`list`: drop models whose last probe (or provider) failed")
    mo.add_argument("--check", action="store_true",
                    help="`route`: probe stale or failed models before reporting")
    mo.add_argument("--auto", action="store_true",
                    help="`sync`: only sync when the cache is stale, recents moved, or the last try failed")
    mo.add_argument("--size", type=int, default=0,
                    help="`panel`: seats, 2-4 (default 3; 4 adds panel-edge)")
    mo.add_argument("--topic", default="",
                    help="`panel`/`council`: topic the bundle is scoped to")
    mo.add_argument("--vote", action="append", default=[],
                    help="`council`: role=win|loss|tie[:note] (repeat once per seat)")
    mo.add_argument("--dry-run", action="store_true", help="`check`: list, do not probe")
    mo.add_argument("--json", action="store_true")
    mo.add_argument("--timeout", type=int, default=30, help="`sync`: metadata fetch timeout")
    mo.add_argument("--no-registry", action="store_true",
                    help="`sync`: only ask opencode, skip models.dev metadata")
    mo.set_defaults(fn=cmd_models)
    us = sub.add_parser("usage", help="per-call token/cost ledger and provider quotas")
    us.add_argument("usage_action", nargs="?", default="report",
                    choices=["ingest", "report", "quota"])
    us.add_argument("--hours", type=int, default=None,
                    help="window in hours (default: models.usage_days, 24)")
    us.add_argument("--by", choices=["provider", "model", "agent"], default="provider")
    us.add_argument("--quiet", action="store_true")
    us.add_argument("--json", action="store_true")
    us.set_defaults(fn=cmd_usage)
    su = sub.add_parser("setup", help="install seed skills, check the model backend")
    su.add_argument("--update-skills", action="store_true",
                    help="overwrite installed seed skills with the packaged "
                         "content (default: missing-only, so your edits are "
                         "never clobbered; existing files stay as they are, so "
                         "a seed from an older release never refreshes)")
    su.set_defaults(fn=cmd_setup)
    t = sub.add_parser("tui", help="opencode config + plugin, then launch opencode")
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--setup-only", action="store_true",
                   help="ensure config+plugin, print paths, do not launch")
    t.set_defaults(fn=cmd_tui)
    pl = sub.add_parser("plugin", help="opencode plugin (stock opencode, no fork)")
    pl.add_argument("plugin_action", choices=["install", "uninstall", "path"])
    pl.add_argument("--from-release", default="",
                    help="install the plugin from a GitHub release instead of this checkout "
                         "(e.g. --from-release 0.3, plugin-v0.3, or default latest = newest plugin-v*)")
    pl.set_defaults(fn=cmd_plugin)
    return p


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in ("serve", "router"):
        print(f"harness {argv[0]} was retired with the relay: models now come "
              f"from your opencode config (`harness doctor` to verify).", file=sys.stderr)
        return 2
    if argv and not argv[0].startswith("-") and argv[0] not in (
            "doctor", "config", "skills", "memory", "setup", "tui", "plugin",
            "risk", "models", "usage", "secret"):
        print(f"harness: unknown command {argv[0]!r} (retired headless commands: "
              f"chat, plan, checkpoint, mcp — live sessions run in opencode)",
              file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    if not getattr(args, "cmd", None) and not hasattr(args, "fn"):
        build_parser().print_help()
        return 2
    os.environ.setdefault("HARNESS_MOCK", os.environ.get("HARNESS_MOCK", ""))
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
