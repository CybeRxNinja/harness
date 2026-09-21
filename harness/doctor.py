"""doctor: env, disk, db, opencode binary, user model, plugin checks."""
from __future__ import annotations

import shutil
import sys
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
    if verbose:
        c["budgets"] = cfg.get("budgets")
        c["categories"] = list((cfg.get("categories", {}) or {}).keys())
    return out
