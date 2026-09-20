"""doctor: env, disk, db, opencode binary, user model, plugin checks."""
from __future__ import annotations

import shutil
from pathlib import Path


def run(root: Path, verbose: bool = False) -> dict:
    from .config import load_config
    out: dict = {"ok": True, "checks": {}}
    c = out["checks"]
    import sys
    c["python"] = sys.version.split()[0]
    try:
        total, used, free = shutil.disk_usage(root)
        c["disk_free_gb"] = round(free / 1e9, 1)
    except Exception:
        c["disk_free_gb"] = -1
    db = root / ".harness" / "sessions.db"
    c["db_mb"] = round(db.stat().st_size / 1e6, 2) if db.exists() else 0.0
    cfg, info = load_config(root)
    c["config_layers"] = info
    oc_bin = shutil.which("opencode")
    c["opencode_binary"] = oc_bin or "missing (install stock opencode, e.g. `npm create opencode@latest`)"
    try:
        from . import models as backend
        c["user_model"] = backend.resolve_model("", cfg)
    except RuntimeError as e:
        c["user_model"] = f"missing: {e}"
        out["ok"] = False
    plug = Path.home() / ".config" / "opencode" / "plugins" / "harness.ts"
    import os as _o
    if _o.environ.get("XDG_CONFIG_HOME"):
        plug = Path(_o.environ["XDG_CONFIG_HOME"]) / "opencode" / "plugins" / "harness.ts"
    c["plugin"] = str(plug) if plug.exists() else "missing (run: harness plugin install)"
    if verbose:
        c["budgets"] = cfg.get("budgets")
        c["categories"] = list((cfg.get("categories", {}) or {}).keys())
    return out
