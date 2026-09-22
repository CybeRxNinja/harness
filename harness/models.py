"""Model backend: shell out to stock `opencode run` (no relay, no API keys here).

Every agent uses the model the user configured in opencode.json (`model` key)
unless an explicit `provider/model` is passed. Plain-text protocol: the model
requests harness tools with [[tool:name {json-args}]] blocks, parsed into
OpenAI-style tool_calls so loop.py's tool loop works unchanged.

HARNESS_MOCK=1 returns a deterministic echo (hermetic tests, offline).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

TOOL_RE = re.compile(r"\[\[tool:([A-Za-z0-9_]+)\s+(\{.*?\})\]\]", re.S)

MAX_PROMPT_CHARS = 60000
RUN_TIMEOUT_S = 300


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


def _render_transcript(messages: list[dict]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = str(m.get("content", ""))
        if role == "system":
            parts.append(f"SYSTEM: {content}")
        elif role == "assistant":
            parts.append(f"ASSISTANT: {content}")
        elif role == "tool":
            parts.append(f"TOOL RESULT: {content}")
        else:
            parts.append(f"USER: {content}")
    return "\n\n".join(parts)


def _render_tools(tools: list[dict] | None) -> str:
    if not tools:
        return ""
    lines = ["\nAVAILABLE TOOLS (call with [[tool:name {json-args}]] on its own line):"]
    for t in tools:
        fn = (t.get("function", {}) or {})
        name = fn.get("name", "?")
        desc = fn.get("description", "")
        params = json.dumps(fn.get("parameters", {}))[:800]
        lines.append(f"- {name}: {desc} args={params}")
    lines.append("To call tools, emit one or more [[tool:name {...}]] blocks and nothing else. "
                 "Otherwise reply with your final answer text and no tool blocks.")
    return "\n".join(lines)


def render_prompt(messages: list[dict], tools: list[dict] | None = None) -> str:
    prompt = _render_transcript(messages) + _render_tools(tools)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS] + "\n[truncated: older context dropped]"
    return prompt


def parse_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Split [[tool:name {json}]] blocks out of model text.

    Returns (clean_text, openai-style tool_calls). Malformed JSON keeps the
    block in the text (model sees its own mistake next turn)."""
    calls: list[dict] = []

    def _take(m: "re.Match[str]") -> str:
        name, raw = m.group(1), m.group(2)
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                return m.group(0)
        except Exception:
            return m.group(0)
        calls.append({"id": f"tc_{len(calls)}",
                      "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args)}})
        return ""

    clean = TOOL_RE.sub(_take, text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, calls


def chat(messages: list[dict], model: str = "", cfg: dict | None = None,
         tools: list[dict] | None = None, extra: dict | None = None,
         workdir: str | Path | None = None) -> dict:
    """One model turn via `opencode run`. Returns OpenAI-style message dict
    with content + tool_calls (+ _route meta). `extra` (reasoning levels) is
    accepted for call-site compatibility and ignored: the user's model runs
    with the user's opencode settings."""
    cfg = cfg or {}
    if os.environ.get("HARNESS_MOCK") == "1":
        # Mock mode is documented as offline/hermetic, so it must not require a
        # configured model: a missing default made every mock run (including
        # the whole worker lifecycle) fail at resolve_model instead.
        try:
            resolved = resolve_model(model, cfg)
        except RuntimeError:
            resolved = model or "mock/model"
        last = messages[-1].get("content", "") if messages else ""
        return {"role": "assistant",
                "content": f"[mock:{resolved}] echo: {str(last)[:500]}",
                "_route": {"provider": "opencode", "model": resolved, "mock": True}}
    resolved = resolve_model(model, cfg)
    binary = shutil.which("opencode")
    if not binary:
        raise RuntimeError("opencode binary not found on PATH — install stock "
                           "opencode (e.g. `npm create opencode@latest`)")
    prompt = render_prompt(messages, tools)
    try:
        proc = subprocess.run(
            [binary, "run", "--model", resolved, prompt],
            capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
            cwd=str(workdir) if workdir else None)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"opencode run timed out after {RUN_TIMEOUT_S}s (model {resolved})")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"opencode run failed (model {resolved}): {err}")
    content, tcalls = parse_tool_calls(proc.stdout or "")
    msg: dict = {"role": "assistant", "content": content,
                 "_route": {"provider": "opencode", "model": resolved}}
    if tcalls:
        msg["tool_calls"] = tcalls
    return msg
