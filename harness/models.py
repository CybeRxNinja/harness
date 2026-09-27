"""Model resolution: which model a live opencode session uses.

Every agent uses the model the user configured in opencode.json (`model` key)
unless an explicit `provider/model` is passed. The headless `opencode run`
shell-out, the `[[tool:...]]` text protocol and its prompt rendering were
retired with the headless CLI path — live sessions run inside opencode, which
owns the model call. What remains is the resolver doctor and setup read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _opencode_config_model() -> str:
    """The user's configured default model from opencode.json (any scope)."""
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    for cand in (Path(base) / "opencode" / "opencode.json",
                  Path.cwd() / "opencode.json"):
        try:
            d = json.loads(cand.read_text())
            m = str(d.get("model", "")).strip()
            if m:
                return m
        except Exception:
            continue
    return ""


def resolve_model(model: str = "", cfg: dict | None = None) -> str:
    """Explicit `provider/model` passes through; anything else (empty, bare
    profile names, legacy router ids like tag:*/auto-fastest) resolves to the
    user's configured opencode default. Raises with a fix-it message if none."""
    cfg = cfg or {}
    if model and "/" in model:
        return model
    prof = str((cfg.get("model_profile") or "")).strip()
    if "/" in prof:  # HARNESS_MODEL=provider/model flows through here
        return prof
    user_default = _opencode_config_model()
    if user_default:
        return user_default
    raise RuntimeError(
        "no model configured: set `model` in opencode.json (e.g. "
        "\"model\": \"anthropic/claude-sonnet-4-5\"), export "
        "HARNESS_MODEL=provider/model, or pass --model provider/model")
