"""Config layers: defaults < ~/.harness/harness.jsonc < .opencode/harness.jsonc < env/flags.

JSONC supported (// and /* */ comments, trailing commas).
Project layer can never touch USER_ONLY_KEYS.
All writes are validated, backed up, atomic.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from .paths import project_config_file

USER_ONLY_KEYS = ("secrets", "token", "trusted_project_dirs", "mcp_env_allowlist", "security")

# Every key here is read by code. Keys that nothing read (a per-agent `tools`
# matrix, `tui.theme`/`side_width`, `mcp.max_servers`, `security.allow_remote_sec`
# and a per-category `reasoning`/`max_turns` payload) are gone: config a user can
# set that changes nothing is worse than no config, because it reads as a knob.
DEFAULT_CONFIG: dict = {
    # only a `provider/model` id here does anything (HARNESS_MODEL); every agent
    # otherwise inherits the user's opencode default model
    "model_profile": "",
    "budgets": {"max_turns": 25, "max_tokens": 120000, "max_cost_usd": 2.0,
                "max_parallel": 2, "max_depth": 1, "worker_timeout_s": 600},
    "compress": {"enabled": True, "threshold": 4000, "intensity": "standard"},
    # the valid spawn intents; rlm.spawn validates against this list, so a typo
    # is a refused spawn instead of a worker with an unknown kind
    "categories": ["quick", "deep", "ultrabrain", "visual", "writing",
                   "unspecified-low", "unspecified-high"],
    "skills": {"write_approval": True, "disabled": ["reverse-*"],
               "external_dirs": ["~/.agents/skills"], "create_dir": ""},
    "memory": {"enabled": True, "cap_lines": 200, "retention_days": 30,
               "max_facts": 2000},
    "mcp": {"servers": {}},
    "security": {"shell_allowlist": ["git", "pytest", "python", "python3", "npm",
                                      "npx", "rg", "grep", "ls", "cat", "bun",
                                      "node", "uv"]},
}

# Agent-mutable top-level keys. Everything else needs human CLI.
# NOTE: there is intentionally no "router" section (retired) and no model
# chains: every agent inherits the user's configured opencode default model.
MUTABLE_TOP = {"model_profile", "budgets", "categories", "skills", "memory",
               "mcp", "compress"}

# The intent list, with the legacy dict shape (categories.<name>.{reasoning,
# max_turns}) still accepted so an existing harness.jsonc keeps loading.
def intents(cfg: dict) -> list[str]:
    cats = cfg.get("categories")
    if isinstance(cats, dict):
        return sorted(cats)
    if isinstance(cats, (list, tuple)):
        return [str(c) for c in cats]
    return list(DEFAULT_CONFIG["categories"])


def _strip_jsonc(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|\s)//.*$", "", text, flags=re.M)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def _load_jsonc(path: Path) -> dict:
    try:
        return json.loads(_strip_jsonc(path.read_text()))
    except FileNotFoundError:
        return {}
    except Exception as e:
        raise ValueError(f"bad config {path}: {e}")


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def user_dir() -> Path:
    return Path(os.environ.get("HARNESS_HOME", str(Path.home() / ".harness")))


def load_config(project_dir: str | Path = ".") -> tuple[dict, dict]:
    """Returns (effective, layers_info). Never raises on missing files."""
    proj = Path(project_dir).resolve()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    info: dict = {"user": None, "project": None}
    ufile = user_dir() / "harness.jsonc"
    udata = _load_jsonc(ufile)
    if udata:
        # strip user-only violations silently (project can't set them anyway)
        cfg = _deep_merge(cfg, udata)
        info["user"] = str(ufile)
    # walk from proj up to $HOME for .opencode/harness.jsonc (nearest wins)
    home = Path.home()
    chain: list[Path] = []
    cur = proj
    while True:
        cand = project_config_file(cur)
        if cand.exists():
            chain.append(cand)
        if cur == home or cur == cur.parent or cur == Path("/"):
            break
        cur = cur.parent
    for cand in reversed(chain):  # farthest first so nearest wins
        pdata = _load_jsonc(cand)
        # enforce user-only invariant
        for k in USER_ONLY_KEYS:
            pdata.pop(k, None)
        cfg = _deep_merge(cfg, pdata)
        info["project"] = str(cand)
    # env overrides (non-secret routing prefs only)
    if os.environ.get("HARNESS_MODEL"):
        cfg["model_profile"] = os.environ["HARNESS_MODEL"]
    return cfg, info


def _check_mutable(path: str) -> None:
    top = path.split(".")[0]
    if top not in MUTABLE_TOP:
        raise PermissionError(
            f"{top!r} is not agent-mutable (needs human `harness config set`). Mutable: {sorted(MUTABLE_TOP)}"
        )
    if "api_key" in path or "secret" in path or "token" in path:
        raise PermissionError("secrets must be set via `harness setup` or env vars, never via chat/config_set")


def get(cfg: dict, path: str):
    cur: object = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(f"unknown config path: {path}")
        cur = cur[part]
    return cur


def set_value(project_dir: str | Path, path: str, value, scope: str = "user") -> str:
    """Validate, backup, write atomically. Returns a one-line diff summary.

    Both scopes write exactly where load_config() reads: the user file
    (~/.harness/harness.jsonc) and the project file
    (<root>/.opencode/harness.jsonc).
    """
    _check_mutable(path)
    if scope not in ("user", "project"):
        raise ValueError("scope must be user|project")
    top = path.split(".")[0]
    if scope == "project" and top in USER_ONLY_KEYS:
        # load_config() strips these from the project layer, so accepting the
        # write here would look like it worked and change nothing
        raise PermissionError(f"{top!r} is user-only; use --scope user")
    dest = project_config_file(project_dir) if scope == "project" else user_dir() / "harness.jsonc"
    dest.parent.mkdir(parents=True, exist_ok=True)
    old_data = _load_jsonc(dest) if dest.exists() else {}
    if dest.exists():
        bdir = dest.parent / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, bdir / f"harness-{int(time.time())}.jsonc")

    parts = path.split(".")
    node = old_data
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise ValueError(f"cannot descend into non-object at {p}")
    data = _apply(old_data, parts, value)

    # validate the result as it will be read (defaults + this file)
    _validate(_deep_merge(json.loads(json.dumps(DEFAULT_CONFIG)), data))
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(dest)
    return diff_text(path, value)


def _validate(cfg: dict) -> None:
    """Structural checks on the merged config — the last gate before a write."""
    budgets = cfg.get("budgets") or {}
    for key in ("max_turns", "max_tokens", "max_parallel", "max_depth"):
        if key in budgets and not isinstance(budgets[key], int):
            raise ValueError(f"budgets.{key} must be an int")
    if "max_cost_usd" in budgets and not isinstance(budgets["max_cost_usd"], (int, float)):
        raise ValueError("budgets.max_cost_usd must be a number")
    cats = cfg.get("categories")
    if cats is not None and not isinstance(cats, (list, dict)):
        raise ValueError("categories must be a list of intent names")
    if "worker_timeout_s" in budgets and not isinstance(budgets["worker_timeout_s"], int):
        raise ValueError("budgets.worker_timeout_s must be an int")
    if not isinstance(cfg.get("mcp", {}).get("servers", {}) or {}, dict):
        raise ValueError("mcp.servers must be an object")


def _apply(data: dict, parts: list[str], value) -> dict:
    import copy
    out = copy.deepcopy(data)
    node = out
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value
    return out


def diff_text(path: str, value) -> str:
    return f"~ {path} = {json.dumps(value)[:500]}"


def redact(cfg: dict) -> dict:
    import copy, re as _re
    out = copy.deepcopy(cfg)
    blob = json.dumps(out)
    blob = _re.sub(r"(sk-[A-Za-z0-9-_]{4})[A-Za-z0-9-_]+", r"\1***", blob)
    blob = _re.sub(r"(xoxb-[A-Za-z0-9-]{4})[A-Za-z0-9-]+", r"\1***", blob)
    return json.loads(blob)
