"""doctor: env, disk, db, opencode binary, user model, plugin, memory, workers."""
from __future__ import annotations

import shutil
import sqlite3
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
            return f"{d} (server + tui)", True
        found = [n for n in ENTRYPOINTS if (d / n).exists()]
        return (f"{d} ({', '.join(found)}) — missing {', '.join(missing)}; "
                "re-run: harness plugin install"), False
    legacy = (opencode_plugins_dir() if plugins_dir is None else plugins_dir) / "harness.ts"
    if legacy.exists():
        return f"{legacy} — legacy server-only layout; re-run: harness plugin install", False
    return "missing (run: harness plugin install)", False


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
