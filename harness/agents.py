"""Render the 12 bundled Harness agents as native Markdown config files.

Harness used to merge agents into ~/.config/opencode/opencode.json. That made
the user's default config a Harness-generated surface. These files are a
lower-churn alternative:

- every file starts with `harness-`, so ownership is visible;
- permissions are generated from risk.py at install time;
- uninstall deletes exactly the harness-* files;
- the user's opencode.json is never read for writing or modified.
"""
from __future__ import annotations

import json
from pathlib import Path

from .paths import opencode_config_dir

PREFIX = "harness-"


def bundled_agents() -> dict:
    """Read the packaged agent definitions without importing the CLI."""
    try:
        from importlib.resources import files as _rf
        text = _rf("harness").joinpath("harness-opencode.json").read_text()
        data = json.loads(text)
        if isinstance(data.get("agent"), dict):
            return data["agent"]
    except Exception:
        pass
    cands = [
        Path(__file__).resolve().parent / "harness-opencode.json",
        Path(__file__).resolve().parent.parent / "harness" / "harness-opencode.json",
    ]
    for cand in cands:
        try:
            return json.loads(cand.read_text())["agent"]
        except Exception:
            continue
    return {}


def agents_dir() -> Path:
    return opencode_config_dir() / "agents"


def agent_id(name: str) -> str:
    return f"{PREFIX}{name}"


def _render_perms(perm: dict | None) -> list[str]:
    if not isinstance(perm, dict):
        perm = {}
    if "bash" not in perm:
        from . import risk as _risk
        perm = dict(perm)
        perm["bash"] = _risk.bash_permission_map()
    out = ["permissions:"]
    for action, effect in perm.items():
        if action == "bash" and isinstance(effect, dict):
            for resource, e in effect.items():
                out.append(f"  - {{action: \"shell\", resource: \"{resource}\", effect: \"{e}\"}}")
            continue
        mapped = "subagent" if action == "task" else action
        if isinstance(effect, str) and mapped in {
            "edit", "read", "glob", "grep", "webfetch", "websearch",
            "external_directory", "subagent", "shell", "skill",
        }:
            out.append(f"  - {{action: \"{mapped}\", resource: \"*\", effect: \"{effect}\"}}")
    return out or []


def render_agent(name: str, spec: dict, model: str = "", badge: str = "") -> str:
    """One agent as opencode markdown.

    `model` is the routed `provider/model#variant` (empty = inherit the session
    model). `badge` says the tier in the description — that text is what the
    orchestrator sees when it chooses a worker, so it is where the cost/speed
    difference has to be visible.
    """
    desc = str(spec.get("description", ""))
    if badge:
        desc = f"{desc} [{badge}]"
    # ensure_ascii=False keeps the tier badge readable in the file (a `·`
    # escaped to \u00b7 is noise in frontmatter the user opens by hand).
    lines = ["---", f"description: {json.dumps(desc, ensure_ascii=False)}"]
    mode = spec.get("mode")
    if mode:
        lines.append(f"mode: {json.dumps(str(mode))}")
    if model:
        lines.append(f"model: {json.dumps(model)}")
    # opencode v2 agent option: a six-digit hex the UI uses to colour the
    # agent. Validated here so a bad palette entry is dropped, not installed
    # as an unusable frontmatter value.
    color = str(spec.get("color", "") or "")
    if len(color) == 7 and color.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in color[1:]):
        lines.append(f"color: {json.dumps(color)}")
    lines.extend(_render_perms(spec.get("permission")))
    lines.extend(["---", ""])
    prompt = str(spec.get("prompt", "") or "").rstrip()
    if prompt:
        lines.append(prompt)
        lines.append("")
    return "\n".join(lines)


def routed_agents(root: str | Path = ".") -> dict[str, dict]:
    """role -> route row, for subagents whose model could be resolved.

    Reads the cached model list only (`harness models sync` writes it); a
    machine that never synced gets no pins, so agents keep inheriting the
    session model instead of failing install on a network call.
    """
    try:
        from .config import load_config
        from .models import route
        cfg, _ = load_config(root)
        if not bool(((cfg.get("models") or {}).get("pin_agents", True))):
            return {}
        return {r["role"]: r for r in route(cfg) if r.get("pin")}
    except Exception:
        return {}


def expected_agent_files(root: str | Path = ".") -> dict[Path, str]:
    out: dict[Path, str] = {}
    routes = routed_agents(root)
    for name, spec in bundled_agents().items():
        # Primary agents inherit the session's model: a pin there would look
        # like routing but never apply to the session the user selected.
        row = routes.get(name) if spec.get("mode") == "subagent" else None
        badge = ""
        if row:
            badge = str(row.get("tier") or "") + (" · free" if row.get("free") else "")
        out[agents_dir() / f"{agent_id(name)}.md"] = render_agent(
            name, spec, model=str((row or {}).get("pin") or ""), badge=badge)
    return out


def install_managed_agents(root: str | Path = ".") -> list[Path]:
    installed: list[Path] = []
    target = agents_dir()
    target.mkdir(parents=True, exist_ok=True)
    for path, content in expected_agent_files(root).items():
        existed = path.exists()
        if not existed or path.read_text(errors="replace") != content:
            path.write_text(content)
        installed.append(path)
    # Prune only Harness-managed files from an older package layout.
    for path in target.glob(f"{PREFIX}*.md"):
        if path not in installed:
            path.unlink(missing_ok=True)
    return installed


def uninstall_managed_agents() -> list[Path]:
    removed: list[Path] = []
    for path in agents_dir().glob(f"{PREFIX}*.md"):
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def status(root: str | Path = ".") -> tuple[str, bool]:
    expected = expected_agent_files(root)
    if not expected:
        return "harness agents unreadable from the package", False
    missing = [p.name for p in expected if not p.exists()]
    stale = [p.name for p in expected if p.exists() and p.read_text(errors="replace") != expected[p]]
    if missing:
        return f"{agents_dir()} missing {len(missing)} harness agent file(s): {', '.join(sorted(missing))} — re-run: harness plugin install", False
    if stale:
        return f"{agents_dir()} — STALE: {', '.join(sorted(stale))}; re-run: harness plugin install", False
    return f"{agents_dir()} ({len(expected)} harness agents)", True
