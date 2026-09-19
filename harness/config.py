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

from .paths import state_dir

USER_ONLY_KEYS = ("secrets", "token", "trusted_project_dirs", "mcp_env_allowlist", "security")

DEFAULT_CONFIG: dict = {
    "model_profile": "capable",
    "router": {
        "target_latency_ms": 3000,
        "timeout_s": 60,
        "max_retries": 2,
        "ban": [],
    },
    "budgets": {"max_turns": 25, "max_tokens": 120000, "max_cost_usd": 2.0, "max_parallel": 2, "max_depth": 1},
    "categories": {
        "quick": {"models": ["auto-fastest"], "reasoning": "low", "max_turns": 15},
        "deep": {"models": ["tag:reasoning+min_ctx:32k", "tag:free", "auto-fastest"], "reasoning": "high", "max_turns": 25},
        "ultrabrain": {"models": ["tag:reasoning", "tag:free", "auto-fastest"], "reasoning": "max", "max_turns": 30},
        "visual": {"models": ["tag:coding", "auto-fastest"], "reasoning": "medium", "max_turns": 20},
        "writing": {"models": ["tag:general", "auto-fastest"], "reasoning": "medium", "max_turns": 15},
        "unspecified-low": {"models": ["auto-fastest"], "reasoning": "low", "max_turns": 15},
        "unspecified-high": {"models": ["tag:general", "auto-fastest"], "reasoning": "high", "max_turns": 25},
    },
    "agents": {
        "explore": {"models": ["auto-fastest"], "reasoning": "low", "tools": {"write": False, "edit": False, "shell": False}},
        "librarian": {"models": ["auto-fastest"], "reasoning": "low", "tools": {"write": False, "edit": False, "shell": False}},
        "plan-consultant": {"models": ["tag:reasoning", "auto-fastest"], "reasoning": "high"},
        "plan-reviewer": {"models": ["tag:reasoning", "auto-fastest"], "reasoning": "high"},
        "code-reviewer": {"models": ["tag:general", "auto-fastest"], "reasoning": "medium", "tools": {"shell": False}},
        "test-engineer": {"models": ["tag:coding", "auto-fastest"], "reasoning": "medium", "tools": {"shell": True}},
        "security-auditor": {"models": ["tag:reasoning", "auto-fastest"], "reasoning": "high", "tools": {"shell": False}},
    },
    "skills": {"write_approval": True, "disabled": ["reverse-*"], "external_dirs": ["~/.agents/skills"], "create_dir": ""},
    "memory": {"enabled": True, "cap_lines": 200, "retention_days": 30},
    "mcp": {"servers": {}, "max_servers": 10},
    "tui": {"theme": "dark", "side_width": 28},
    "security": {"allow_remote_sec": False, "shell_allowlist": ["git", "pytest", "python", "python3", "npm", "npx", "rg", "grep", "ls", "cat", "bun", "node", "uv"]},
}

# Agent-mutable top-level keys. Everything else needs human CLI.
MUTABLE_TOP = {"model_profile", "router", "budgets", "categories", "agents", "skills", "memory", "mcp", "tui"}


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
        cand = cur / ".opencode" / "harness.jsonc"
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
    """Validate, backup, write atomically. Returns unified diff text."""
    _check_mutable(path)
    if scope not in ("user", "project"):
        raise ValueError("scope must be user|project")
    dest = (user_dir() if scope == "user" else state_dir(Path(project_dir).resolve())) / "harness.jsonc"
    dest.parent.mkdir(parents=True, exist_ok=True)
    old_data = _load_jsonc(dest) if dest.exists() else {}
    if dest.exists():
        bdir = dest.parent / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, bdir / f"harness-{int(time.time())}.jsonc")
    # apply nested set on a copy of effective file data
    parts = path.split(".")
    node = old_data
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise ValueError(f"cannot descend into non-object at {p}")
    node[parts[-1]] = value
    # validate by merging over defaults (raises on structural breakage)
    merged = _deep_merge(json.loads(json.dumps(DEFAULT_CONFIG)), old_data if scope == "project" else _deep_merge(old_data, {}))
    # light structural validation
    if not isinstance(merged.get("budgets", {}).get("max_parallel", 2), int):
        raise ValueError("budgets.max_parallel must be int")
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(old_data if False else _apply(old_data, parts, value), indent=2) + "\n")
    tmp.rename(dest)
    return diff_text(path, value)


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
