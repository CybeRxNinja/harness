"""doctor: env, disk, db, opencode binary, user model, plugin, memory, workers."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ENTRYPOINTS = ("server.ts", "tui.tsx")


def plugin_status(plugins_dir: Path | None = None) -> tuple[str, bool]:
    """What opencode will actually load, and whether that is healthy.

    The installed layout is <plugins>/harness/{server.ts,tui.tsx}: the loader
    probes a plugin DIRECTORY for those entrypoints, and only a `tui` entrypoint
    gets the plugin into the TUI's list — a bare harness.ts is server-only.
    Reporting the old single-file path made `harness doctor` claim the plugin
    was missing on a correct install.
    """
    from .paths import plugin_install_dir, opencode_plugins_dir
    d = plugin_install_dir() if plugins_dir is None else plugins_dir / "harness"
    if d.is_dir():
        missing = [n for n in ENTRYPOINTS if not (d / n).exists()]
        if not missing:
            stale = _stale_entrypoints(d)
            if stale:
                return (f"{d} (server + tui) — STALE: {', '.join(stale)} differ "
                        f"from this build (old window/agent code in effect); "
                        f"re-run: harness plugin install"), False
            return f"{d} (server + tui)", True
        found = [n for n in ENTRYPOINTS if (d / n).exists()]
        return (f"{d} ({', '.join(found)}) — missing {', '.join(missing)}; "
                "re-run: harness plugin install"), False
    legacy = (opencode_plugins_dir() if plugins_dir is None else plugins_dir) / "harness.ts"
    if legacy.exists():
        return f"{legacy} — legacy server-only layout; re-run: harness plugin install", False
    return "missing (run: harness plugin install)", False


# installed name -> shipped source name (server.ts is harness.ts on disk)
SHIPPED = {"server.ts": "harness.ts", "tui.tsx": "tui.tsx"}


def _stale_entrypoints(installed: Path) -> list[str]:
    """Installed bytes vs the entrypoints THIS build ships.

    A directory that merely exists can be months old — the failure that hid
    the Window fix and the specialized subagents from an already-'installed'
    system — so existence alone is not health.
    """
    base = Path(__file__).resolve().parent / "plugin"
    stale = []
    for name, src in SHIPPED.items():
        try:
            f = base / src
            if f.exists() and f.read_bytes() != (installed / name).read_bytes():
                stale.append(name)
        except OSError:
            continue
    return stale


def agents_status() -> tuple[str, bool]:
    """opencode.json must carry every bundled agent.

    The task tool resolves subagent_type against opencode's agent registry, so
    a config merged by an older harness (primaries only, no `mode: subagent`
    entries) leaves the orchestrator spawning `general` for every task.
    """
    import json as _j
    from .paths import opencode_config_dir
    try:
        from .cli import _bundled_opencode_json
        want = set(_bundled_opencode_json().get("agent", {}))
    except Exception as e:
        return f"bundled agents unreadable: {e}", False
    dest = opencode_config_dir() / "opencode.json"
    try:
        cur = _j.loads(dest.read_text()) if dest.exists() else None
    except Exception:
        return f"{dest} unreadable — re-run: harness plugin install", False
    if cur is None:
        return f"{dest} not found (run: harness plugin install)", False
    missing = sorted(want - set(cur.get("agent") or {}))
    if missing:
        return (f"{dest} missing {len(missing)} harness agent(s): "
                f"{', '.join(missing)} — re-run: harness plugin install"), False
    return f"{dest} ({len(want)} harness agents)", True


def _project_db(root: Path) -> Path | None:
    """sessions.db under the current layout, else the pre-migration one."""
    from .paths import state_file
    new = state_file(root, "sessions.db")
    if new.exists():
        return new
    old = root / ".harness" / "sessions.db"
    return old if old.exists() else None


def _readonly(db: Path, sql: str, args: tuple = ()) -> list:
    """Query without touching the state dir or running migrations.

    `store.connect()` mkdirs and migrates; a reporting command must not write.
    """
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    except Exception:
        return []


def memory_status(root: Path) -> dict:
    """The lessons file people actually read, plus the fact count behind recall."""
    from .memory import memory_file
    path = memory_file(root)
    try:
        lines = len(path.read_text().splitlines()) if path.exists() else 0
    except Exception:
        lines = -1
    facts = 0
    db = _project_db(root)
    if db:
        rows = _readonly(db, "SELECT COUNT(*) FROM facts")
        facts = int(rows[0][0]) if rows else 0
    return {"file": str(path), "lines": lines, "facts": facts}


def worker_status(root: Path, cfg: dict | None = None) -> dict:
    """Worker rows by lifecycle state. `stale` is the interesting one: a row
    that still claims queued/running long after its last update is a thread that
    died, and it must be visible rather than silent."""
    db = _project_db(root)
    if not db:
        return {"total": 0, "unfinished": 0, "stale": 0}
    from . import rlm
    limit = rlm.worker_timeout(cfg or {}) * 2
    rows = _readonly(db, "SELECT status, updated FROM workers")
    now = int(time.time())
    unfinished = [r for r in rows if not rlm.is_terminal(str(r[0]))]
    stale = [r for r in unfinished if now - int(r[1] or 0) > limit]
    return {"total": len(rows), "unfinished": len(unfinished), "stale": len(stale)}


def run(root: Path, verbose: bool = False) -> dict:
    from .config import load_config
    out: dict = {"ok": True, "checks": {}}
    c = out["checks"]
    c["python"] = sys.version.split()[0]
    try:
        _, _, free = shutil.disk_usage(root)
        c["disk_free_gb"] = round(free / 1e9, 1)
    except Exception:
        c["disk_free_gb"] = -1
    db = _project_db(root)
    c["db_mb"] = round(db.stat().st_size / 1e6, 2) if db else 0.0
    cfg, info = load_config(root)
    c["config_layers"] = info
    c["opencode_binary"] = shutil.which("opencode") or (
        "missing (install stock opencode, e.g. `npm create opencode@latest`)")
    # opencode's OWN version, read from the binary: the TUI/plugins panel
    # owns displaying it — harness only reports it so any mismatch the owner
    # sees is attributable (the plugin never writes a version anywhere).
    binary = shutil.which("opencode")
    if binary:
        try:
            v = subprocess.run([binary, "--version"], capture_output=True,
                               text=True, timeout=20)
            first = ((v.stdout or "") + (v.stderr or "")).strip().splitlines()
            c["opencode_version"] = first[0] if first else "unknown"
        except Exception:
            c["opencode_version"] = "unknown"
    else:
        c["opencode_version"] = "missing"
    # LSP: what opencode's OWN config will do — read from opencode.json (the
    # file the merge writes), NOT harness's config layers, which know nothing
    # about it. harness merges `lsp: true` only when the key is absent; an
    # explicit false/object of the owner's is always respected.
    try:
        from .paths import opencode_config_dir
        import json as _j
        _oc = opencode_config_dir() / "opencode.json"
        lsp = _j.loads(_oc.read_text()).get("lsp") if _oc.exists() else None
    except Exception:
        lsp = None
    c["lsp"] = (
        "disabled (your config)" if lsp is False
        else f"overridden ({len(lsp)} entries, your config)" if isinstance(lsp, dict)
        else "enabled (opencode built-in servers)" if lsp is True
        else "unset (opencode default)"
    )
    try:
        from . import models as backend
        c["user_model"] = backend.resolve_model("", cfg)
    except RuntimeError as e:
        c["user_model"] = f"missing: {e}"
        out["ok"] = False
    status, healthy = plugin_status()
    c["plugin"] = status
    if not healthy:
        out["ok"] = False
    agents_msg, agents_ok = agents_status()
    c["agents"] = agents_msg
    if not agents_ok:
        out["ok"] = False
    c["memory"] = memory_status(root)
    c["workers"] = worker_status(root, cfg)
    if verbose:
        from . import risk
        from .config import intents
        c["budgets"] = cfg.get("budgets")
        c["categories"] = intents(cfg)
        c["permissions"] = {
            "shell_default": "allow",
            "ask_when_destructive": len([v for v in risk.bash_permission_map().values()
                                         if v == "ask"]),
            "destructive_rules": len(risk.DESTRUCTIVE_RULES),
            "sensitive_paths": len(risk.SENSITIVE_PATH_RULES),
        }
    return out
