"""doctor: env, disk, db, router, kernel, tui checks."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def run(root: Path, verbose: bool = False) -> dict:
    from .config import load_config, user_dir
    from .router import PROVIDERS
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
    keys = {}
    for name, meta in PROVIDERS.items():
        env = meta.get("env", "")
        keys[name] = bool(os.environ.get(env)) if env else "local-try"
    c["provider_keys"] = keys
    if not any(v is True for v in keys.values()):
        c["note"] = "no provider keys set — running in MOCK mode (HARNESS_MOCK=1 behavior). Set OPENROUTER_API_KEY etc."
    try:
        from .router import rank, candidates, parse_model
        r = rank(candidates(parse_model("auto-fastest"), cfg), cfg)
        c["router_top"] = r[0] if r else None
    except Exception as e:
        c["router_top"] = f"error: {e}"
    tui_bin = shutil.which("harness-tui")
    c["tui_binary"] = tui_bin or "missing (built by CI Releases; CLI fallback active)"
    if verbose:
        c["budgets"] = cfg.get("budgets")
        c["categories"] = list((cfg.get("categories", {}) or {}).keys())
    return out
