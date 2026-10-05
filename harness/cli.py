"""harness CLI (installer + kept operators: doctor/config/skills/memory/plugin)."""
from __future__ import annotations

import argparse
import json
import os
import sys
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
                    # MEMORY.md at the project root is the file AGENTS.md's
                    # precedence chain reads; writing <state>/MEMORY.md (as this
                    # used to) put approved lessons somewhere nothing read.
                    ok = approve(con, pid, _memory_file(root))
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
            install_managed_agents()
        except Exception as e:
            print(f"harness: managed agent install failed ({e}) — continuing", file=sys.stderr)
        print(f"plugin installed at {plug} (stock opencode v2, no fork needed)")
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
        from .agents import agents_dir
        print(f"would: write {agents_dir()}/harness-*.md, install plugin, exec opencode")
        return 0
    try:
        from .agents import install_managed_agents, agents_dir
        agent_paths = install_managed_agents()
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
    k.add_argument("skills_action", choices=["list", "view", "pending", "approve", "reject"])
    k.add_argument("name", nargs="?", default="")
    k.add_argument("--subpath", default="")
    k.add_argument("--ids", nargs="*", default=[])
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
            "doctor", "config", "skills", "memory", "setup", "tui", "plugin", "risk"):
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
