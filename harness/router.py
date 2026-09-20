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
                   "ids": {"openrouter": "nvidia/nemotron-3-ultra-550b-a55b:free",
                           "kilo": "nvidia/nemotron-3-ultra-550b-a55b:free",
                           "opencode": "nemotron-3-ultra-free"}},
    "free-reasoner": {"quality": 0.82, "ctx": 128000, "tags": ["reasoning", "general", "free"],
                      "ids": {"openrouter": "deepseek/deepseek-v4-flash-0731:free",
                              "groq": "deepseek-r1-distill-llama-70b",
                              "kilo": "deepseek/deepseek-v4-flash-0731:free",
                              "opencode": "muse-spark-1.3-contributor-free",
                              "ollama": "deepseek-r1"}},
    "free-glm": {"quality": 0.83, "ctx": 128000, "tags": ["coding", "reasoning", "general", "free"],
                 "ids": {"openrouter": "z-ai/glm-5.2:free", "kilo": "z-ai/glm-5.2:free"}},
    "free-coder": {"quality": 0.78, "ctx": 32000, "tags": ["coding", "general", "free"],
                   "ids": {"openrouter": "cohere/north-mini-code:free",
                           "groq": "qwen-qwq-32b",
                           "kilo": "cohere/north-mini-code:free",
                           "opencode": "mimo-v2.5-free",
                           "ollama": "qwen2.5-coder"}},
    "free-coder-qwen": {"quality": 0.76, "ctx": 96000, "tags": ["coding", "general", "free"],
                        "ids": {"openrouter": "qwen/qwen3.8-27b:free", "kilo": "qwen/qwen3.8-27b:free"}},
    "free-swift": {"quality": 0.72, "ctx": 128000, "tags": ["general", "fast", "free"],
                   "ids": {"openrouter": "nvidia/nemotron-3-super-120b-a12b:free",
                           "groq": "llama-3.3-70b-versatile",
                           "kilo": "nvidia/nemotron-3-super-120b-a12b:free"}},
    "free-general": {"quality": 0.70, "ctx": 128000, "tags": ["general", "fast", "free"],
                     "ids": {"openrouter": "google/gemma-4-31b-it:free",
                             "groq": "llama-3.3-70b-versatile",
                             "kilo": "stepfun/step-3.7-flash:free",
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
    "opencode": {"base": "https://opencode.ai/zen/v1", "env": "OPENCODE_API_KEY"},
    "kilo": {"base": "https://api.kilo.ai/api/gateway", "env": "KILO_API_KEY", "optional_key": True},
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


def catalog_path():
    from .config import user_dir
    return user_dir() / "catalog.json"


def load_catalog() -> dict:
    import json as _j
    try:
        return _j.loads(catalog_path().read_text())
    except Exception:
        return {}


def _catalog_age_ok(cat: dict, max_age_s: int = 86400) -> bool:
    import time as _t
    return bool(cat) and int(cat.get("at", 0)) > _t.time() - max_age_s


def _fetch_models(pname: str, meta: dict, timeout: int = 10) -> list[dict]:
    """Raw [{id, ctx}] from a provider catalog endpoint. [] on any failure."""
    import json as _j
    import urllib.request as _u
    cpath = meta.get("catalog", "/models")
    if not cpath:
        return []
    headers = {"Accept": "application/json"}
    key = os.environ.get(meta.get("env", ""), "") if meta.get("env") else ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = _u.Request(meta["base"].rstrip("/") + cpath, headers=headers)
        with _u.urlopen(req, timeout=timeout) as r:
            data = _j.loads(r.read().decode())
    except Exception:
        return []
    items = data.get("data", data if isinstance(data, list) else [])
    out = []
    for m in items:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        ctx = m.get("context_length") or m.get("context_window") or m.get("ctx") or 0
        try:
            ctx = int(ctx)
        except Exception:
            ctx = 0
        out.append({"id": str(m["id"]), "ctx": ctx})
    return out


def infer_tags(mid: str) -> list[str]:
    low = mid.lower()
    tags = []
    if ":free" in low:
        tags.append("free")
    if any(k in low for k in ("code", "coder", "dev", "laguna")):
        tags.append("coding")
    if any(k in low for k in ("reason", "r1", "distill", "think", "qwq")):
        tags.append("reasoning")
    if any(k in low for k in ("flash", "fast", "mini", "lightning", "nano", "haiku", "xs", "2b", "7b")):
        tags.append("fast")
    if any(k in low for k in ("ultra", "max", "opus", "pro", "70b", "120b", "550b")):
        tags.append("strong")
    tags.append("general")
    return sorted(set(tags))


def infer_quality(mid: str) -> float:
    low = mid.lower()
    if any(k in low for k in ("ultra-550b", "opus", "glm-5")):
        return 0.84
    if any(k in low for k in ("deepseek", "kimi-k3", "qwen3", "coder")):
        return 0.78
    if any(k in low for k in ("gemma", "nemotron", "llama-3.3", "grok")):
        return 0.72
    if any(k in low for k in ("laguna", "mini", "nano", "2b", "xs", "flash")):
        return 0.62
    return 0.68


def refresh_catalog(probe: bool = False, timeout: int = 10) -> dict:
    """Re-discover servable models from every reachable provider catalog.

    Discovery is cheap (2 GETs) and automatic; probing actually calls each
    free model once (slow, burns rate limits) so it stays opt-in.
    Returns {"providers": {name: [{id, ctx, free}]}, "dead": [...]}.
    """
    import json as _j
    import time as _t
    import urllib.request as _u
    found: dict[str, list] = {}
    for pname, meta in PROVIDERS.items():
        if meta.get("native"):
            continue
        if meta.get("env") and not os.environ.get(meta["env"]) and not meta.get("optional_key"):
            continue
        if pname == "ollama" and not meta.get("env"):
            # local daemon: models endpoint differs (/api/tags); try it
            try:
                req = _u.Request("http://localhost:11434/api/tags", headers={"Accept": "application/json"})
                with _u.urlopen(req, timeout=5) as r:
                    data = _j.loads(r.read().decode())
                found[pname] = [{"id": m["name"], "ctx": 0, "free": True}
                                for m in data.get("models", []) if m.get("name")]
                continue
            except Exception:
                continue
        found[pname] = [{"id": m["id"], "ctx": m["ctx"], "free": ":free" in m["id"]}
                        for m in _fetch_models(pname, meta, timeout)]
    # preserve first-seen timestamps across refreshes (recency signal)
    old = load_catalog()
    old_seen = {}
    for _pn, _ms in (old.get("providers", {}) or {}).items():
        for _m in _ms:
            if _m.get("first_seen"):
                old_seen[f"{_pn}|{_m['id']}"] = _m["first_seen"]
    now_ts = int(_t.time())
    for pname, models in found.items():
        for m in models:
            m["first_seen"] = old_seen.get(f"{pname}|{m['id']}", now_ts)
    dead: list[str] = []
    if probe:
        key_providers = [p for p in found if PROVIDERS[p].get("env") and os.environ.get(PROVIDERS[p]["env"])]
        tested = 0
        for pname in key_providers:
            meta = PROVIDERS[pname]
            for m in found[pname][:15]:
                if not m["free"]:
                    continue
                tested += 1
                try:
                    req = _u.Request(
                        meta["base"].rstrip("/") + "/chat/completions",
                        data=_j.dumps({"model": m["id"], "messages": [{"role": "user", "content": "hi"}],
                                       "max_tokens": 5}).encode(),
                        headers={"Content-Type": "application/json",
                                 "Authorization": f"Bearer {os.environ[meta['env']]}"})
                    with _u.urlopen(req, timeout=30) as r:
                        _j.loads(r.read().decode())
                except Exception:
                    dead.append(f"{pname}|{m['id']}")
    cat = {"at": int(_t.time()), "providers": found, "dead": dead}
    try:
        catalog_path().parent.mkdir(parents=True, exist_ok=True)
        catalog_path().write_text(_j.dumps(cat, indent=1))
    except Exception:
        pass
    return cat


def maybe_refresh_catalog(max_age_s: int = 86400) -> None:
    if os.environ.get("HARNESS_MOCK") == "1":
        return
    try:
        if not _catalog_age_ok(load_catalog(), max_age_s):
            refresh_catalog()
    except Exception:
        pass


def discovered_candidates(parsed: dict, cfg: dict | None = None) -> list[dict]:
    """Candidates derived from the live catalog (deduped against curated).

    Unvetted ids (no benchmark, not approved) are quarantined per
    router.new_model_policy instead of being silently routed to.
    """
    cfg = cfg or {}
    cat = load_catalog()
    if not cat:
        return []
    dead = set(cat.get("dead", []))
    out = []
    kind = parsed.get("kind")
    for pname, models in cat.get("providers", {}).items():
        for m in models:
            mid = m["id"]
            if f"{pname}|{mid}" in dead:
                continue
            tags = infer_tags(mid)
            if kind == "tag" and parsed.get("tag") not in tags:
                continue
            if kind == "group":
                continue  # groups are curated-only (stable slugs)
            if kind == "pin":
                continue  # pins bypass discovery
            min_ctx = parsed.get("min_ctx")
            if min_ctx and (m.get("ctx") or 0) < min_ctx:
                continue
            bench = bench_score(mid)
            allowed, _why = is_allowed(pname, mid, cfg, m.get("first_seen"))
            if not allowed and bench is None:
                continue
            out.append({"provider": pname, "model": mid,
                        "quality": (bench / 100) if bench is not None else infer_quality(mid),
                        "ctx": m.get("ctx") or 32000, "discovered": True})
    return out


# Estimated composite benchmark scores by model family (0-100, coding-weighted).
# Heuristic from public evals (SWE-bench/Aider/LiveBench class data), NOT measured
# here. Unknown families get None -> quarantine gate, never a guessed score.
BENCH: dict[str, float] = {
    "nemotron-3-ultra": 88, "opus": 87, "glm-5": 83, "deepseek-v4": 84,
    "deepseek-v3": 82, "kimi-k3": 82, "kimi-k2": 80, "qwen3": 78, "qwen-coder": 78,
    "minimax-m3": 80, "minimax-m2": 78, "llama-3.3": 72, "gemma-4": 70,
    "grok": 74, "gpt-5": 85, "sonnet": 84, "haiku": 76, "mistral-small": 68,
    "laguna": 60, "north-mini-code": 74, "mimo": 70, "ling-": 66, "inkling": 68,
    "dots-": 66, "nex-": 64, "lfm-": 58, "hy3": 66,
}


def parse_params(mid: str) -> tuple[float | None, float | None]:
    """(total_B, active_B) from MoE-style ids like 550b-a55b / 30b-a3b / 70b.
    Returns (None, None) when no param count is encoded (versions like 2.1
    without a 'b' suffix are NOT params)."""
    import re as _re
    m = _re.search(r"(\d+(?:\.\d+)?)\s*b(?:\s*[-_x]\s*a\s*(\d+(?:\.\d+)?)\s*b?)?", mid.lower())
    if not m:
        return (None, None)
    total = float(m.group(1))
    active = float(m.group(2)) if m.group(2) else None
    if total > 2000:  # sanity: not a param count
        return (None, None)
    return (total, active)


def bench_score(mid: str) -> float | None:
    low = mid.lower()
    for fam, score in sorted(BENCH.items(), key=lambda kv: -len(kv[0])):
        if fam in low:
            return score
    return None


def classify(mid: str, first_seen: int | None = None) -> dict:
    """Static classification card for one model id."""
    import time as _t
    total, active = parse_params(mid)
    bench = bench_score(mid)
    age_days = round((_t.time() - first_seen) / 86400, 1) if first_seen else None
    if bench is not None:
        verdict = "trusted"
    elif age_days is not None and age_days > 90:
        verdict = "unverified"
    else:
        verdict = "quarantine"
    return {"params_total_b": total, "params_active_b": active, "bench": bench,
            "age_days": age_days, "verdict": verdict}


def is_allowed(provider: str, mid: str, cfg: dict, first_seen: int | None = None) -> tuple[bool, str]:
    """Quarantine gate for unvetted models. Returns (allowed, reason)."""
    s = _load_stats()
    if f"{provider}|{mid}" in set(s.get("_approved", [])):
        return True, "approved"
    card = classify(mid, first_seen)
    if card["verdict"] == "trusted":
        return True, "benchmarked"
    policy = (cfg.get("router", {}) or {}).get("new_model_policy", "ask")
    if policy == "allow":
        return True, "policy-allow"
    if policy == "deny":
        return False, "policy-deny"
    return False, f"quarantine: no benchmark ({describe_card(card)}). approve with: harness router approve {provider}/{mid}"


def describe_card(card: dict) -> str:
    bits = []
    if card["params_total_b"]:
        bits.append(f"{card['params_total_b']:g}B" + (f"({card['params_active_b']:g}B active)" if card["params_active_b"] else ""))
    else:
        bits.append("params unknown")
    if card["age_days"] is not None:
        bits.append(f"seen {card['age_days']}d ago")
    else:
        bits.append("new")
    return ", ".join(bits)


def approve_model(provider: str, mid: str) -> None:
    s = _load_stats()
    approved = set(s.get("_approved", []))
    approved.add(f"{provider}|{mid}")
    s["_approved"] = sorted(approved)
    _save_stats(s)


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
    if os.environ.get(env):
        return True
    return bool(meta.get("optional_key"))  # e.g. kilo: anonymous free tier


def _openrouter_tier() -> str:
    """'free' if the OpenRouter key is free-tier (paid models 402), else 'full'.
    Cached daily in stats; unknown (no key/offline) -> 'full' (no filtering)."""
    s = _load_stats()
    ent = s.get("_tier", {})
    if ent.get("at", 0) > time.time() - 86400 and ent.get("tier"):
        return ent["tier"]
    if os.environ.get("HARNESS_MOCK") == "1":
        return ent.get("tier", "full")  # hermetic tests: never hit network
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
    # anonymous kilo (no key): :free endpoints only, paid would 401
    if not os.environ.get("KILO_API_KEY"):
        out = [c for c in out
               if not (c["provider"] == "kilo" and not c["model"].endswith(":free"))]
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
    seen = {f"{c['provider']}|{c['model']}" for c in cands}
    for d in discovered_candidates(parsed, cfg):
        if f"{d['provider']}|{d['model']}" not in seen:
            cands.append(d)
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
        if _prov_cooling(c["provider"]):
            continue
        meta = PROVIDERS.get(c["provider"], {})
        if meta.get("env") and not os.environ.get(meta["env"]):
            # keyless: only anonymous-capable free endpoints may attempt
            if not (meta.get("optional_key") and c["model"].endswith(":free")):
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
            tools: bool = False, not_found: bool = False, rate_limited: int = 0,
            hard402: bool = False) -> None:
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
    if hard402:
        # payment-required: cool the whole provider, this model will 402 too
        consec = ((s.get("_prov402", {}) or {}).get(provider, {}) or {}).get("n", 0) + 1
        s.setdefault("_prov402", {})[provider] = {
            "n": consec,
            "until": time.time() + (600 if consec >= 2 else 60),
        }
    elif ok:
        if "_prov402" in s and provider in s["_prov402"]:
            del s["_prov402"][provider]  # success clears provider-level stain
    s[key] = st
    if ok and requested:
        recent = s.get("_recent", [])
        recent.append({"requested": requested, "provider": provider, "model": model,
                       "ms": int(ms), "ts": int(time.time())})
        s["_recent"] = recent[-20:]
    _save_stats(s)


def _prov_cooling(provider: str) -> bool:
    """True while a provider is cooled down after repeated 402s."""
    import time as _t
    s = _load_stats()
    until = ((s.get("_prov402", {}) or {}).get(provider, {}) or {}).get("until", 0)
    return bool(until and until > _t.time())


def _diversify(ordered: list[dict]) -> list[dict]:
    """Round-robin by provider, preserving score order within each provider.

    Prevents one broken provider from consuming the whole attempt budget.
    """
    buckets: dict[str, list[dict]] = {}
    for c in ordered:
        buckets.setdefault(c["provider"], []).append(c)
    out: list[dict] = []
    while any(buckets.values()):
        for pname in list(buckets):
            if buckets[pname]:
                out.append(buckets[pname].pop(0))
    return out


def recent_routes(limit: int = 10) -> list[dict]:
    return _load_stats().get("_recent", [])[-limit:]


def _post_openai(base: str, key: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json",
               "HTTP-Referer": "https://localhost/harness", "X-Title": "harness"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + "/chat/completions", data=data, headers=headers)
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
    headers = {"Content-Type": "application/json",
               "HTTP-Referer": "https://localhost/harness", "X-Title": "harness",
               "Accept": "text/event-stream"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = _u.Request(base + "/chat/completions", data=data, headers=headers)
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
    maybe_refresh_catalog()
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
    for cand in _diversify(ordered)[: max(1, min(len(ordered), max_attempts))]:
        pname, mid = cand["provider"], cand["model"]
        meta = PROVIDERS.get(pname)
        if not meta or meta.get("native"):
            continue
        key = os.environ.get(meta.get("env", ""), "") if meta.get("env") else ""
        if meta.get("env") and not key and not (
                meta.get("optional_key") and mid.endswith(":free")):
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
            is_free = mid.endswith(":free")
            # 402 on a FREE route = provider-level trouble; 402 on paid = that
            # model is out of reach (long model cooldown, no provider stain)
            paid402 = "402" in str(e) and not is_free
            _record(pname, mid, 5000, False, model, tools=need, not_found=nf,
                    rate_limited=ra or (60 if "429" in str(e) else (1800 if paid402 else 0)),
                    hard402=("402" in str(e) and is_free))
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
    maybe_refresh_catalog()
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
    for cand in _diversify(ordered)[: max(1, min(len(ordered), max_attempts))]:
        pname, mid = cand["provider"], cand["model"]
        meta = PROVIDERS.get(pname)
        if not meta or meta.get("native"):
            continue  # v0: OpenAI-compat path only; native Anthropic mapped via openrouter
        key = os.environ.get(meta.get("env", ""), "") if meta.get("env") else ""
        if meta.get("env") and not key and not (
                meta.get("optional_key") and mid.endswith(":free")):
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
            is_free = mid.endswith(":free")
            # 402 on a FREE route = provider-level trouble; 402 on paid = that
            # model is out of reach (long model cooldown, no provider stain)
            paid402 = "402" in str(e) and not is_free
            _record(pname, mid, 5000, False, model, tools=need, not_found=nf,
                    rate_limited=ra or (60 if "429" in str(e) else (1800 if paid402 else 0)),
                    hard402=("402" in str(e) and is_free))
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
    for pname, models in load_catalog().get("providers", {}).items():
        for m in models[:30]:
            seen[f"{pname}/{m['id']}"] = {"id": f"{pname}/{m['id']}", "owned_by": pname,
                                          "tags": infer_tags(m["id"]), "discovered": True}
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
