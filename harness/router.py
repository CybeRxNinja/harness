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
    "kimi-k2.5": {"quality": 0.82, "ctx": 200000, "tags": ["coding", "general", "agentic"],
                 "ids": {"openrouter": "moonshotai/kimi-k2.5", "groq": "moonshotai/kimi-k2-instruct"}},
    "minimax-m2.5": {"quality": 0.80, "ctx": 200000, "tags": ["coding", "general", "agentic"],
                    "ids": {"openrouter": "minimax/minimax-m2.5"}},
    "glm-4.7": {"quality": 0.78, "ctx": 128000, "tags": ["coding", "general", "agentic"],
               "ids": {"openrouter": "z-ai/glm-5.3"}},
    "deepseek-v3.2": {"quality": 0.84, "ctx": 128000, "tags": ["coding", "reasoning", "general", "fast"]},
    "qwen-coder": {"quality": 0.75, "ctx": 128000, "tags": ["coding", "fast"],
                  "ids": {"openrouter": "qwen/qwen3-coder", "groq": "qwen-qwq-32b"}},
    "llama-fast": {"quality": 0.62, "ctx": 128000, "tags": ["fast", "general"],
                 "ids": {"openrouter": "meta-llama/llama-3.3-70b-instruct", "groq": "llama-3.3-70b-versatile"}},
}

# Curated free-tier groups. ids map overrides the group slug per provider
# (OpenRouter :free endpoints cost $0 under the same key; Groq/Cerebras have
# free tiers; Ollama is always free/local). IDs churn — failover covers misses.
FREE_GROUPS: dict[str, dict] = {
    # Verified live against the OpenRouter catalog; breadth across vendors so
    # 429/404 on one still leaves options. Groq/Cerebras/Ollama ids activate
    # automatically when those keys exist (verified on arrival).
    "free-giant": {"quality": 0.85, "ctx": 128000, "tags": ["reasoning", "general", "free"],
                   "ids": {"openrouter": "nvidia/nemotron-3-ultra-550b-a55b:free"}},
    "free-reasoner": {"quality": 0.82, "ctx": 128000, "tags": ["reasoning", "general", "free"],
                      "ids": {"openrouter": "deepseek/deepseek-v4-flash-0731:free",
                              "groq": "deepseek-r1-distill-llama-70b",
                              "ollama": "deepseek-r1"}},
    "free-glm": {"quality": 0.83, "ctx": 128000, "tags": ["coding", "reasoning", "general", "free"],
                 "ids": {"openrouter": "z-ai/glm-5.2:free"}},
    "free-coder": {"quality": 0.78, "ctx": 32000, "tags": ["coding", "general", "free"],
                   "ids": {"openrouter": "cohere/north-mini-code:free",
                           "groq": "qwen-qwq-32b",
                           "ollama": "qwen2.5-coder"}},
    "free-coder-qwen": {"quality": 0.76, "ctx": 96000, "tags": ["coding", "general", "free"],
                        "ids": {"openrouter": "qwen/qwen3.8-27b:free"}},
    "free-swift": {"quality": 0.72, "ctx": 128000, "tags": ["general", "fast", "free"],
                   "ids": {"openrouter": "nvidia/nemotron-3-super-120b-a12b:free",
                           "groq": "llama-3.3-70b-versatile"}},
    "free-general": {"quality": 0.70, "ctx": 128000, "tags": ["general", "fast", "free"],
                     "ids": {"openrouter": "google/gemma-4-31b-it:free",
                             "groq": "llama-3.3-70b-versatile",
                             "nvidia": "meta/llama-3.3-70b-instruct",
                             "ollama": "llama3.3"}},
}

GROUPS: dict[str, dict] = {**GROUPS, **FREE_GROUPS}

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


def _openrouter_tier() -> str:
    """'free' if the OpenRouter key is free-tier (paid models 402), else 'full'.
    Cached daily in stats; unknown (no key/offline) -> 'full' (no filtering)."""
    s = _load_stats()
    ent = s.get("_tier", {})
    if ent.get("at", 0) > time.time() - 86400 and ent.get("tier"):
        return ent["tier"]
    tier = "full"
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        try:
            req = urllib.request.Request("https://openrouter.ai/api/v1/auth/key",
                                         headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode()).get("data", {})
                if data.get("is_free_tier"):
                    tier = "free"
        except Exception:
            pass
    s["_tier"] = {"tier": tier, "at": int(time.time())}
    _save_stats(s)
    return tier


def _finalize(cands: list[dict], banned: set, parsed: dict) -> list[dict]:
    out = [c for c in cands if f"{c['provider']}/{c['model']}" not in banned]
    # free-tier OpenRouter keys 402 every paid model: keep :free endpoints only.
    # (Other providers unaffected; pins bypass this filter by design.)
    if parsed["kind"] != "pin" and _openrouter_tier() == "free":
        free_only = [c for c in out
                     if not (c["provider"] == "openrouter" and not c["model"].endswith(":free"))]
        if free_only:
            return free_only
    return out


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
                cands.append({"provider": pname, "model": g.get("ids", {}).get(pname, parsed["group"]), "quality": g["quality"], "ctx": g["ctx"]})
        return _finalize(cands, banned, parsed)
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
                cands.append({"provider": pname, "model": g.get("ids", {}).get(pname, gid), "quality": g["quality"], "ctx": g["ctx"]})
    if not cands and want_tag:
        # relax min_ctx rather than hard-fail
        for gid, g in GROUPS.items():
            if want_tag in g["tags"]:
                for pname in PROVIDERS:
                    if _provider_has_key(pname):
                        cands.append({"provider": pname, "model": g.get("ids", {}).get(pname, gid), "quality": g["quality"], "ctx": g["ctx"]})
    # last resort: any keyed provider with raw model passthrough
    if not cands:
        for pname in PROVIDERS:
            if _provider_has_key(pname):
                cands.append({"provider": pname, "model": "auto", "quality": 0.5, "ctx": 32000})
                break
    return _finalize(cands, banned, parsed)


def rank(cands: list[dict], cfg: dict, need_tools: bool = False) -> list[dict]:
    stats = _load_stats()
    target = (cfg.get("router", {}) or {}).get("target_latency_ms", TARGET_MS_DEFAULT)
    out = []
    now = time.time()
    for c in cands:
        key = f"{c['provider']}|{c['model']}"
        st = stats.get(key, {})
        if st.get("cooldown_until", 0) > now:
            continue
        if need_tools and st.get("no_tools"):
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


def _retry_after_s(e: Exception) -> int:
    """Honor upstream Retry-After on 429s (capped). 0 = none present."""
    try:
        headers = getattr(e, "headers", {}) or {}
        raw = headers.get("Retry-After", headers.get("retry-after", ""))
        return max(0, min(300, int(float(str(raw).split(",")[0].strip()))))
    except Exception:
        return 0


def _record(provider: str, model: str, ms: float, ok: bool, requested: str = "",
            tools: bool = False, not_found: bool = False, rate_limited: int = 0) -> None:
    s = _load_stats()
    key = f"{provider}|{model}"
    st = s.get(key, {"ewma_ms": 3000, "errors": 0})
    st["ewma_ms"] = int(0.8 * st.get("ewma_ms", 3000) + 0.2 * ms)
    st["errors"] = 0 if ok else st.get("errors", 0) + 1
    if not ok and st["errors"] >= 2:
        st["cooldown_until"] = time.time() + 60
    if rate_limited:
        st["cooldown_until"] = time.time() + rate_limited  # explicit backoff wins
    if ok and tools:
        st.pop("no_tools", None)  # serves tools again: capability restored
    if not_found and tools:
        st["no_tools"] = True  # 404 on a tool request: route lacks tool support
    s[key] = st
    if ok and requested:
        recent = s.get("_recent", [])
        recent.append({"requested": requested, "provider": provider, "model": model,
                       "ms": int(ms), "ts": int(time.time())})
        s["_recent"] = recent[-20:]
    _save_stats(s)


def recent_routes(limit: int = 10) -> list[dict]:
    return _load_stats().get("_recent", [])[-limit:]


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
    need = bool(tools)
    ordered = rank(candidates(parsed, cfg), cfg, need_tools=need)
    max_attempts = max(1, int((cfg.get("router", {}) or {}).get("max_attempts", 5)))
    if os.environ.get("HARNESS_MOCK") == "1" or not ordered:
        last = messages[-1].get("content", "") if messages else ""
        yield f'data: {json.dumps({"choices": [{"delta": {"content": f"[mock:{model}] echo: {str(last)[:200]}"}}]})}\n\n'.encode()
        yield b"data: [DONE]\n\n"
        return
    errs: list[str] = []
    for cand in ordered[: max(1, min(len(ordered), max_attempts))]:
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
            _record(pname, mid, (time.time() - t0) * 1000, True, model, tools=need)
            yield first
            for chunk in gen:
                yield chunk if isinstance(chunk, bytes) else str(chunk).encode()
            return
        except StopIteration:
            _record(pname, mid, (time.time() - t0) * 1000, True, model, tools=need)
            yield b"data: [DONE]\n\n"
            return
        except Exception as e:  # noqa: BLE001 - failover path
            nf = "404" in str(e)
            ra = _retry_after_s(e) if "429" in str(e) else 0
            _record(pname, mid, 5000, False, model, tools=need, not_found=nf,
                    rate_limited=ra or (60 if "429" in str(e) or "402" in str(e) else 0))
            errs.append(f"{pname}/{mid}: {str(e)[:100]}")
            continue
    yield f'data: {json.dumps({"error": f"all providers failed for {model!r}: " + "; ".join(errs[:5])})}\n\n'.encode()
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
    need = bool(tools)
    ordered = rank(candidates(parsed, cfg), cfg, need_tools=need)
    max_attempts = max(1, int((cfg.get("router", {}) or {}).get("max_attempts", 5)))
    if os.environ.get("HARNESS_MOCK") == "1" or not ordered:
        last = messages[-1].get("content", "") if messages else ""
        return {"role": "assistant", "content": f"[mock:{model}] echo: {str(last)[:500]}",
                "_route": {"provider": "mock", "model": model, "mock": True}}
    errs: list[str] = []
    for cand in ordered[: max(1, min(len(ordered), max_attempts))]:
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
            _record(pname, mid, ms, True, model, tools=need)
            msg = body["choices"][0]["message"]
            msg["_route"] = {"provider": pname, "model": mid, "ms": int(ms)}
            return msg
        except Exception as e:  # noqa: BLE001 - failover path
            nf = "404" in str(e)
            ra = _retry_after_s(e) if "429" in str(e) else 0
            _record(pname, mid, 5000, False, model, tools=need, not_found=nf,
                    rate_limited=ra or (60 if "429" in str(e) or "402" in str(e) else 0))
            errs.append(f"{pname}/{mid}: {str(e)[:100]}")
            continue
    raise RuntimeError(f"all providers failed for {model!r}: " + "; ".join(errs[:5]))


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
