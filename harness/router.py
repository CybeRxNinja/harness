"""Builtin ModelRelay-style router. Stdlib only. No background probing.

Model strings:
  auto-fastest | <group-id> | tag:<name> | tag:<name>+min_ctx:<32k|32000|1m>
  provider/model pin (e.g. openrouter/anthropic/claude, ollama/qwen2.5-coder)
Env keys: OPENROUTER_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY,
  GOOGLE_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY, NVIDIA_API_KEY,
  OLLAMA_BASE_URL (default http://localhost:11434/v1).
Stats: ~/.harness/router_stats.json {ewma_ms, errors} per provider|model.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

TARGET_MS_DEFAULT = 3000

# curated static quality 0..1 + tags + ctx. Small, hand-tuned. Extend via config later.
GROUPS: dict[str, dict] = {
    "kimi-k2.5": {"quality": 0.82, "ctx": 200000, "tags": ["coding", "general", "agentic"]},
    "minimax-m2.5": {"quality": 0.80, "ctx": 200000, "tags": ["coding", "general", "agentic"]},
    "glm-4.7": {"quality": 0.78, "ctx": 128000, "tags": ["coding", "general", "agentic"]},
    "deepseek-v3.2": {"quality": 0.84, "ctx": 128000, "tags": ["coding", "reasoning", "general"]},
    "qwen-coder": {"quality": 0.75, "ctx": 128000, "tags": ["coding", "fast"]},
    "llama-fast": {"quality": 0.62, "ctx": 128000, "tags": ["fast", "general"]},
}

PROVIDERS: dict[str, dict] = {
    "openrouter": {"base": "https://openrouter.ai/api/v1", "env": "OPENROUTER_API_KEY"},
    "openai": {"base": "https://api.openai.com/v1", "env": "OPENAI_API_KEY"},
    "anthropic": {"base": "https://api.anthropic.com/v1", "env": "ANTHROPIC_API_KEY", "native": True},
    "google": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "env": "GOOGLE_API_KEY"},
    "groq": {"base": "https://api.groq.com/openai/v1", "env": "GROQ_API_KEY"},
    "cerebras": {"base": "https://api.cerebras.ai/v1", "env": "CEREBRAS_API_KEY"},
    "nvidia": {"base": "https://integrate.api.nvidia.com/v1", "env": "NVIDIA_API_KEY"},
    "ollama": {"base": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), "env": ""},
}


def _stats_path() -> Path:
    from .config import user_dir
    return user_dir() / "router_stats.json"


def _load_stats() -> dict:
    try:
        return json.loads(_stats_path().read_text())
    except Exception:
        return {}


def _save_stats(s: dict) -> None:
    try:
        p = _stats_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(s, indent=2))
    except Exception:
        pass


def _parse_size(s: str) -> int:
    s = s.strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([km]?)", s)
    if not m:
        raise ValueError(f"bad size: {s}")
    n, suf = float(m.group(1)), m.group(2)
    return int(n * (1024 * 1024 if suf == "m" else 1024 if suf == "k" else 1))


def parse_model(s: str) -> dict:
    """Parse user model string into {kind, ...}."""
    base, _, mod = s.partition("+")
    min_ctx = None
    if mod.startswith("min_ctx:"):
        try:
            min_ctx = _parse_size(mod[len("min_ctx:"):])
        except ValueError:
            min_ctx = None
    if base == "auto-fastest":
        return {"kind": "auto", "min_ctx": min_ctx, "raw": s}
    if base.startswith("tag:"):
        return {"kind": "tag", "tag": base[4:], "min_ctx": min_ctx, "raw": s}
    if "/" in base and not base.startswith("tag:"):
        prov, _, mid = base.partition("/")
        return {"kind": "pin", "provider": prov, "model": mid, "raw": s}
    return {"kind": "group", "group": base, "min_ctx": None, "raw": s}


def _provider_has_key(name: str) -> bool:
    meta = PROVIDERS.get(name, {})
    env = meta.get("env", "")
    if not env:
        return name == "ollama"  # local, try anyway
    return bool(os.environ.get(env))


def candidates(parsed: dict, cfg: dict) -> list[dict]:
    """Return ordered provider|model candidates before QoS ranking."""
    cands: list[dict] = []
    banned = set((cfg.get("router", {}) or {}).get("ban", []))
    if parsed["kind"] == "pin":
        cands.append({"provider": parsed["provider"], "model": parsed["model"], "quality": 0.8, "ctx": 128000})
        return cands
    if parsed["kind"] == "group":
        g = GROUPS.get(parsed["group"])
        if not g:
            # unknown group -> treat as direct model id on first keyed provider
            for pname in PROVIDERS:
                if _provider_has_key(pname):
                    cands.append({"provider": pname, "model": parsed["group"], "quality": 0.7, "ctx": 64000})
                    break
            return cands
        for pname in PROVIDERS:
            if _provider_has_key(pname):
                cands.append({"provider": pname, "model": parsed["group"], "quality": g["quality"], "ctx": g["ctx"]})
        return [c for c in cands if f"{c['provider']}/{c['model']}" not in banned]
    # auto + tag
    want_tag = parsed.get("tag") if parsed["kind"] == "tag" else None
    min_ctx = parsed.get("min_ctx")
    for gid, g in GROUPS.items():
        if want_tag and want_tag not in g["tags"]:
            continue
        if min_ctx and g["ctx"] < min_ctx:
            continue
        for pname in PROVIDERS:
            if _provider_has_key(pname):
                cands.append({"provider": pname, "model": gid, "quality": g["quality"], "ctx": g["ctx"]})
    if not cands and want_tag:
        # relax min_ctx rather than hard-fail
        for gid, g in GROUPS.items():
            if want_tag in g["tags"]:
                for pname in PROVIDERS:
                    if _provider_has_key(pname):
                        cands.append({"provider": pname, "model": gid, "quality": g["quality"], "ctx": g["ctx"]})
    # last resort: any keyed provider with raw model passthrough
    if not cands:
        for pname in PROVIDERS:
            if _provider_has_key(pname):
                cands.append({"provider": pname, "model": "auto", "quality": 0.5, "ctx": 32000})
                break
    return [c for c in cands if f"{c['provider']}/{c['model']}" not in banned]


def rank(cands: list[dict], cfg: dict) -> list[dict]:
    stats = _load_stats()
    target = (cfg.get("router", {}) or {}).get("target_latency_ms", TARGET_MS_DEFAULT)
    out = []
    now = time.time()
    for c in cands:
        key = f"{c['provider']}|{c['model']}"
        st = stats.get(key, {})
        if st.get("cooldown_until", 0) > now:
            continue
        ewma = st.get("ewma_ms", target)
        err = st.get("errors", 0)
        latency_score = target / (target + max(0, ewma))
        penalty = 0.5 ** min(err, 4)
        c = dict(c)
        c["score"] = round(c["quality"] * latency_score * penalty, 4)
        c["ewma_ms"] = ewma
        out.append(c)
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


def _record(provider: str, model: str, ms: float, ok: bool) -> None:
    s = _load_stats()
    key = f"{provider}|{model}"
    st = s.get(key, {"ewma_ms": 3000, "errors": 0})
    st["ewma_ms"] = int(0.8 * st.get("ewma_ms", 3000) + 0.2 * ms)
    st["errors"] = 0 if ok else st.get("errors", 0) + 1
    if not ok and st["errors"] >= 2:
        st["cooldown_until"] = time.time() + 60
    s[key] = st
    _save_stats(s)


def _post_openai(base: str, key: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(base + "/chat/completions", data=data, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://localhost/harness", "X-Title": "harness",
    })
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()), (time.time() - t0) * 1000


def _post_openai_stream(base: str, key: str, payload: dict, timeout: int):
    """Yield RAW byte chunks verbatim (preserves SSE blank-line framing).

    Never re-split/re-join lines: strict clients (AI SDK) require \\n\\n
    event separators. Raises before first byte on failure (failover point).
    """
    import urllib.request as _u
    payload = dict(payload)
    payload["stream"] = True
    data = json.dumps(payload).encode()
    req = _u.Request(base + "/chat/completions", data=data, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://localhost/harness", "X-Title": "harness", "Accept": "text/event-stream",
    })
    resp = _u.urlopen(req, timeout=timeout)
    try:
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            resp.close()
        except Exception:
            pass


def chat_stream(messages: list[dict], model: str = "auto-fastest", cfg: dict | None = None,
                extra: dict | None = None, tools: list | None = None):
    """Yield SSE lines, failing over to the next candidate if upstream dies before first byte."""
    from .reasoning import provider_params
    cfg = cfg or {}
    timeout = (cfg.get("router", {}) or {}).get("timeout_s", 60)
    retries = (cfg.get("router", {}) or {}).get("max_retries", 2)
    parsed = parse_model(model)
    ordered = rank(candidates(parsed, cfg), cfg)
    if os.environ.get("HARNESS_MOCK") == "1" or not ordered:
        last = messages[-1].get("content", "") if messages else ""
        yield f'data: {json.dumps({"choices": [{"delta": {"content": f"[mock:{model}] echo: {str(last)[:200]}"}}]})}\n\n'.encode()
        yield b"data: [DONE]\n\n"
        return
    last_err: Exception | None = None
    for cand in ordered[: max(1, retries + 1)]:
        pname, mid = cand["provider"], cand["model"]
        meta = PROVIDERS.get(pname)
        if not meta or meta.get("native"):
            continue
        key = os.environ.get(meta.get("env", ""), "") if meta.get("env") else "ollama"
        if meta.get("env") and not key:
            continue
        payload = {"model": mid, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        if extra:
            payload.update(provider_params(pname, (extra.get("reasoning") or "medium"), mid))
        t0 = time.time()
        try:
            gen = _post_openai_stream(meta["base"], key, payload, timeout)
            first = next(gen)  # failover point: nothing sent to client yet
            _record(pname, mid, (time.time() - t0) * 1000, True)
            yield first
            for chunk in gen:
                yield chunk if isinstance(chunk, bytes) else str(chunk).encode()
            return
        except StopIteration:
            _record(pname, mid, (time.time() - t0) * 1000, True)
            yield b"data: [DONE]\n\n"
            return
        except Exception as e:  # noqa: BLE001 - failover path
            _record(pname, mid, 5000, False)
            last_err = e
            continue
    yield f'data: {json.dumps({"error": f"all providers failed for {model!r}: {last_err}"})}\n\n'.encode()
    yield b"data: [DONE]\n\n"


def chat(messages: list[dict], model: str = "auto-fastest", cfg: dict | None = None,
         extra: dict | None = None, tools: list | None = None) -> dict:
    """Synchronous chat with failover. Returns OpenAI-style message dict + _route meta.

    If no provider keys are set or HARNESS_MOCK=1, returns deterministic mock (for tests/offline).
    """
    from .reasoning import provider_params
    cfg = cfg or {}
    timeout = (cfg.get("router", {}) or {}).get("timeout_s", 60)
    retries = (cfg.get("router", {}) or {}).get("max_retries", 2)
    parsed = parse_model(model)
    ordered = rank(candidates(parsed, cfg), cfg)
    if os.environ.get("HARNESS_MOCK") == "1" or not ordered:
        last = messages[-1].get("content", "") if messages else ""
        return {"role": "assistant", "content": f"[mock:{model}] echo: {str(last)[:500]}",
                "_route": {"provider": "mock", "model": model, "mock": True}}
    last_err: Exception | None = None
    for cand in ordered[: max(1, retries + 1)]:
        pname, mid = cand["provider"], cand["model"]
        meta = PROVIDERS.get(pname)
        if not meta or meta.get("native"):
            continue  # v0: OpenAI-compat path only; native Anthropic mapped via openrouter
        key = os.environ.get(meta.get("env", ""), "") if meta.get("env") else "ollama"
        if meta.get("env") and not key:
            continue
        payload = {"model": mid, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        if extra:
            payload.update(provider_params(pname, (extra.get("reasoning") or "medium"), mid))
        try:
            body, ms = _post_openai(meta["base"], key, payload, timeout)
            _record(pname, mid, ms, True)
            msg = body["choices"][0]["message"]
            msg["_route"] = {"provider": pname, "model": mid, "ms": int(ms)}
            return msg
        except Exception as e:  # noqa: BLE001 - failover path
            _record(pname, mid, 5000, False)
            last_err = e
            continue
    raise RuntimeError(f"all providers failed for {model!r}: {last_err}")


def list_models(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or {}
    seen: dict[str, dict] = {"auto-fastest": {"id": "auto-fastest", "owned_by": "router"}}
    for gid, g in GROUPS.items():
        seen[gid] = {"id": gid, "owned_by": "relay", "tags": g["tags"], "ctx": g["ctx"]}
    for pname in PROVIDERS:
        if _provider_has_key(pname):
            seen[f"{pname}/*"] = {"id": f"{pname}/*", "owned_by": pname, "available": True}
    return list(seen.values())


def reasoning_from_body(body: dict) -> dict:
    """Map upstream reasoning controls to harness canonical levels. {} if none."""
    lvl = body.get("reasoning_effort") or body.get("reasoningEffort")
    if isinstance(lvl, dict):
        lvl = lvl.get("effort")
    if isinstance(lvl, str) and lvl.lower() in ("off", "low", "medium", "high", "max", "xhigh"):
        return {"reasoning": lvl.lower()}
    return {}


def did_you_mean(s: str) -> list[str]:
    keys = list(GROUPS) + ["auto-fastest", "tag:coding", "tag:reasoning", "tag:general", "tag:fast"]
    return [k for k in keys if s[:3].lower() in k.lower()][:3]
