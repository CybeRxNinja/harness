"""Model resolution: the session default, and which model each worker runs on.

Two jobs:

- `resolve_model()` — which model a live opencode session uses: the user's
  configured default unless a `provider/model` is explicit. Unchanged.
- `route()` — which `provider/model#variant` each worker role should run on.

The second exists because one model for every job is wrong in both directions:
recon and mechanical reading run fine on a cheap (often free) model, while
review, planning and security need the strong one. opencode's subagent tool has
no model parameter (the model comes from the agent definition), so routing means
pinning `model:` per agent at install time; the orchestrator then picks the
agent whose model fits the job.

Sources of truth, cheapest first:

- `opencode models` — the models this install can actually name. `harness
  models sync` writes it to `<HARNESS_HOME>/models-available.json`.
- models.dev — cost, context, tool_call and reasoning options, cached to
  `<HARNESS_HOME>/models-dev.json` by the same command (compact, available
  providers only).
- `harness/data/model-tiers.json` — curated patterns (tier, strengths, free)
  that work offline and cover provider prefixes the registry renames.
- `<HARNESS_HOME>/models-health.json` — the last probe per `model#variant`,
  written by `harness models check`.

Nothing here fetches during an agent install: no cache means no pins (agents
inherit the session model), never a surprise network call.
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from . import usage
from .config import user_dir
from .paths import state_file

MODELS_DEV_URL = "https://models.dev/api.json"
AVAILABLE_TTL_S = 7 * 24 * 3600
REGISTRY_TTL_S = 7 * 24 * 3600
PROBE_MARK = "Reply with exactly: OK"
PROBE_OK = "OK"


# ---------------------------------------------------------------------------
# Session default (unchanged)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Caches (written by `harness models sync` / `check`, read everywhere else)
# ---------------------------------------------------------------------------

def _available_path() -> Path:
    return user_dir() / "models-available.json"


def _registry_path() -> Path:
    return user_dir() / "models-dev.json"


def _health_path() -> Path:
    return user_dir() / "models-health.json"


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    tmp.rename(path)


def _age(data: dict) -> int:
    try:
        return max(0, int(time.time()) - int(data.get("refreshed_at") or 0))
    except Exception:
        return 10 ** 9


def _recent_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "opencode" / "model.json"
    return Path.home() / ".local" / "state" / "opencode" / "model.json"


def _recent_state() -> dict:
    """Opencode's recent-state file: capable models `opencode models` omits."""
    path = _recent_state_path()
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"models": [], "variants": {}}
    if not isinstance(data, dict):
        return {"models": [], "variants": {}}
    models: list[str] = []
    seen: set[str] = set()
    for key in ("recent", "favorite"):
        for entry in (data.get(key) or []):
            if not isinstance(entry, dict):
                continue
            pid = entry.get("providerID")
            mid = entry.get("modelID")
            if not isinstance(pid, str) or not isinstance(mid, str):
                continue
            if not pid or not mid or "/" in pid or "/" in mid:
                continue
            model_id = f"{pid}/{mid}"
            if model_id not in seen:
                seen.add(model_id)
                models.append(model_id)
    variants: dict[str, str] = {}
    vmap = data.get("variant")
    if isinstance(vmap, dict):
        for k, v in vmap.items():
            if isinstance(k, str) and isinstance(v, str):
                variants[k] = v
    return {"models": models, "variants": variants}


def _recent_models() -> list[str]:
    return list(_recent_state().get("models") or [])


def available() -> list[str]:
    """`opencode models` ids from the last sync, plus opencode's recent models."""
    data = _load_json(_available_path(), {})
    cached = [str(m) for m in (data.get("models") or []) if "/" in str(m)]
    recent = [str(m) for m in _recent_models() if "/" in str(m)]
    return sorted(set(cached) | set(recent))


def available_age() -> int:
    return _age(_load_json(_available_path(), {}))


def registry() -> dict:
    """provider -> {models: {id: {cost, limit, reasoning_options, tool_call}}}."""
    data = _load_json(_registry_path(), {})
    return data.get("providers") or {}


def registry_age() -> int:
    return _age(_load_json(_registry_path(), {}))


def _opencode_models(timeout: int = 60) -> list[str]:
    try:
        r = subprocess.run(["opencode", "models"], capture_output=True, text=True,
                           timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    return sorted({line.strip() for line in (r.stdout or "").splitlines()
                   if "/" in line and not line.lstrip().startswith("#")})


def _fetch_registry(providers: set[str], timeout: int = 30) -> dict:
    """The models.dev catalog, trimmed to the providers this install uses.

    The full file is ~6 MB and lists 230 providers; only the ones `opencode
    models` named can be routed to, so the cache keeps cost/limits/reasoning
    for those and stays small.
    """
    req = urllib.request.Request(MODELS_DEV_URL, headers={"User-Agent": "harness"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out: dict = {}
    for pid, prov in (data or {}).items():
        if providers and pid not in providers:
            continue
        rows = {}
        for mid, m in ((prov or {}).get("models") or {}).items():
            rows[mid] = {
                "name": str(m.get("name") or mid),
                "cost": m.get("cost") or {},
                "limit": (m.get("limit") or {}).get("context", 0),
                "reasoning": bool(m.get("reasoning")),
                "tool_call": m.get("tool_call", True),
                "reasoning_options": m.get("reasoning_options") or [],
                # Profiles, not rank: release date is a tiebreak and a fact the
                # orchestrator can read; status "deprecated" keeps a model out.
                "released": str(m.get("release_date") or ""),
                "knowledge": str(m.get("knowledge") or ""),
                "status": str(m.get("status") or ""),
            }
        if rows:
            out[pid] = {"models": rows}
    return out


def sync(with_registry: bool = True, timeout: int = 30) -> dict:
    """Refresh what this install can route to: the model list, then metadata.

    A failed `opencode models` call (empty list) never erases a good cache: the
    previous models stay routable, and `attempted_at`/`error` record the miss.
    """
    before = _load_json(_available_path(), {})
    ids = sorted(set(_opencode_models()) | set(_recent_models()))
    now = int(time.time())
    error = ""
    models = ids
    if ids:
        _write_json(_available_path(), {"refreshed_at": now, "attempted_at": now,
                                        "models": ids, "error": ""})
    else:
        models = [str(m) for m in (before.get("models") or []) if "/" in str(m)]
        error = "opencode models returned no models"
        data = {"attempted_at": now, "models": models, "error": error,
                "refreshed_at": (int(before.get("refreshed_at") or 0) if models
                                 else now)}
        _write_json(_available_path(), data)
    providers = {m.partition("/")[0] for m in models}
    result = {"models": len(models), "providers": len(providers), "registry": 0,
              "error": error}
    if with_registry and ids:
        try:
            reg = _fetch_registry(providers, timeout=timeout)
            _write_json(_registry_path(), {"refreshed_at": now, "providers": reg})
            result["registry"] = sum(len(p.get("models") or {}) for p in reg.values())
        except Exception as e:  # offline is not fatal: patterns still route
            result["error"] = str(e)[:160]
    return result


def provider_limits() -> dict:
    """The curated free-tier limits table (harness/data/provider-limits.json)."""
    return usage._load_limits()


def limits_for(model_or_provider: str, free: bool | None = None) -> dict:
    """Free-tier limits for a provider id (or a `provider/model` id)."""
    return usage.limits_for(str(model_or_provider).partition("/")[0])


def sync_auto(cfg: dict | None = None, timeout: int = 30) -> dict:
    """Sync only when the cache is stale, recents moved, or the last try failed.

    An flock makes parallel sessions share one sync; never raises.
    """
    cfgm = (cfg or {}).get("models") or {}
    ttl = int(cfgm.get("sync_ttl_s") or 21600)
    data = _load_json(_available_path(), {})
    reason = ""
    if not data or not data.get("models"):
        reason = "empty"
    elif data.get("error"):
        reason = "error"
    elif _age(data) > ttl:
        reason = "stale"
    else:
        try:
            recent_mtime = int(_recent_state_path().stat().st_mtime)
        except Exception:
            recent_mtime = 0
        if recent_mtime > int(data.get("refreshed_at") or 0):
            reason = "recents"
    if not reason:
        return {"ran": False, "reason": "fresh", "sync": {}}
    lock_path = user_dir() / "models-sync.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        import fcntl
    except Exception:
        return {"ran": False, "reason": "unsupported", "sync": {}}
    handle = None
    try:
        handle = open(lock_path, "w")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        return {"ran": False, "reason": "locked", "sync": {}}
    try:
        result = sync(timeout=timeout)
    except Exception as e:  # never raise into a session hook
        result = {"models": 0, "providers": 0, "registry": 0,
                  "error": str(e)[:160]}
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass
    return {"ran": True, "reason": reason, "sync": result}


def models_status(cfg: dict, project: str | Path = ".",
                  roles: list[str] | None = None) -> dict:
    """Fast model awareness for the orchestrator: cached data only, no probes."""
    cfgm = cfg.get("models") or {}
    ttl = int(cfgm.get("sync_ttl_s") or 21600)
    health_ttl = int(cfgm.get("health_ttl_s") or 1800)
    data = _load_json(_available_path(), {})
    avail = available()
    pool = _pool(avail)
    ledger = load_health()
    age_s = available_age()
    providers = sorted({m.partition("/")[0] for m in avail if "/" in m})
    usage_rows = {str(r.get("key")): r for r in
                  (usage.summary(hours=24, by="provider").get("rows") or [])}
    quotas: dict[str, dict] = {}
    provider_rows = []
    for prov in providers:
        quotas[prov] = usage.quota_state(prov)
        urow = usage_rows.get(prov) or {}
        provider_rows.append({
            "provider": prov,
            "models": sum(1 for m in avail if m.partition("/")[0] == prov),
            "free": sum(1 for m in pool
                        if m["model"].partition("/")[0] == prov and m["free"]),
            "usage_24h": {"calls": int(urow.get("calls") or 0),
                          "input": int(urow.get("input") or 0),
                          "output": int(urow.get("output") or 0),
                          "cost": float(urow.get("cost") or 0.0)},
            "limits": usage.limits_for(prov),
            "quota": quotas[prov],
        })
    state = _recent_state()
    recent = []
    for model in (state.get("models") or [])[:10]:
        cand = classify(model)
        variant = (state.get("variants") or {}).get(model) or variant_for(model, "high")
        pin = pin_for(model, variant)
        hstate, hrow = health_state(pin, health_ttl, ledger)
        if hstate == "unverified":
            mstate, mreason = model_health(model, health_ttl, ledger)
            if mstate == "failed":
                hstate, hrow = "failed", {"reason": mreason}
            elif mstate == "dead":
                hstate, hrow = "failed", {"reason": mreason}
        recent.append({"model": model, "pin": pin, "variant": variant,
                       "tier": cand["tier"], "free": cand["free"],
                       "health": hstate,
                       "reason": str((hrow or {}).get("reason") or ""),
                       "context": int(cand.get("context") or 0),
                       "strengths": list(cand.get("strengths") or []),
                       "released": cand.get("released") or "",
                       "knowledge": cand.get("knowledge") or "",
                       "deprecated": bool(cand.get("deprecated"))})
    verified = [m for m in pool
                if model_health(m["model"], health_ttl, ledger)[0] == "ok"]
    strong_free_verified = [m for m in verified if m["free"] and m["tier"] == "strong"]
    warnings: list[str] = []
    if not avail:
        warnings.append("no models — run `harness models sync`")
    if age_s > ttl:
        warnings.append(f"model list is {age_s // 3600}h old — run `harness models sync --auto`")
    if data.get("error"):
        warnings.append(f"last sync failed: {str(data.get('error'))[:120]}")
    if avail and not strong_free_verified:
        warnings.append("no verified strong free model — free strong work may fail")
    for row in recent:
        if row["health"] == "failed":
            warnings.append(f"recent model failed: {row['model']}: {row['reason'][:100]}")
    for prov, quota in quotas.items():
        if quota.get("exhausted"):
            warnings.append(f"provider quota exhausted: {prov}")
    return {
        "schema": 1,
        "now": int(time.time()),
        "sync": {"models": len(avail), "providers": len(providers),
                 "age_s": age_s, "stale": age_s > ttl,
                 "last_error": str(data.get("error") or "")},
        "counts": {"available": len(avail),
                   "free": sum(1 for m in pool if m["free"]),
                   "verified": len(verified),
                   "strong_free_verified": len(strong_free_verified)},
        "recent": recent,
        "providers": provider_rows,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Classification: tier, free/paid, strengths
# ---------------------------------------------------------------------------

_PATTERNS: list[dict] | None = None
_EXCLUDE: list[str] = []

# What each worker is good at, so a free model that can code outranks a free
# model that cannot (a music model and a 501B chat model are both "free").
ROLE_STRENGTHS: dict[str, tuple[str, ...]] = {
    "explore": ("recon", "code"),
    "librarian": ("research", "reasoning"),
    "quick": ("recon", "code"),
    "test-engineer": ("code", "tests"),
    "code-reviewer": ("review", "code"),
    "security-auditor": ("security", "review"),
    "plan-consultant": ("reasoning", "review"),
    "plan-reviewer": ("review", "reasoning"),
    "deep": ("reasoning", "code"),
    # Panelists (see panel_pool): what each seat is there to argue about.
    "panel-lead": ("reasoning", "review"),
    "panel-peer": ("code", "reasoning"),
    "panel-skeptic": ("review", "reasoning"),
    "panel-edge": ("recon", "code"),
}


def _patterns() -> list[dict]:
    global _PATTERNS, _EXCLUDE
    if _PATTERNS is None:
        try:
            data = json.loads((Path(__file__).resolve().parent / "data" /
                               "model-tiers.json").read_text())
            _PATTERNS = [p for p in (data.get("patterns") or [])
                         if isinstance(p, dict) and p.get("match")]
            _EXCLUDE = [str(p).lower() for p in (data.get("exclude") or [])]
        except Exception:
            _PATTERNS, _EXCLUDE = [], []
    return _PATTERNS


def excluded(model_id: str) -> bool:
    """True for models that are not coding workers at all."""
    _patterns()  # fills _EXCLUDE
    low = str(model_id).lower()
    return any(fnmatch.fnmatch(low, pat) for pat in _EXCLUDE)


def _guess_strengths(model_id: str) -> list[str]:
    low = str(model_id).lower()
    out = []
    for key, tag in (("code", "code"), ("reason", "reasoning"), ("think", "reasoning"),
                     ("vision", "vision"), ("vl", "vision"),
                     ("flash", "fast"), ("mini", "fast"), ("nano", "fast"),
                     ("lightning", "fast")):
        if key in low and tag not in out:
            out.append(tag)
    return out


def classify(model_id: str, reg: dict | None = None) -> dict:
    """One model's routing facts: tier, free, cost, context, strengths.

    A curated pattern wins over cost (a cheap haiku is still 'cheap' whatever a
    gateway charges); cost fills the gap for models no pattern knows, so a new
    model is routed by what it costs rather than hidden.
    """
    reg = registry() if reg is None else reg
    prov, _, mid = str(model_id).partition("/")
    meta = (((reg.get(prov) or {}).get("models") or {}).get(mid)) or {}
    cost = meta.get("cost") or {}
    inp = float(cost.get("input") or 0)
    outp = float(cost.get("output") or 0)
    known_cost = bool(cost)
    free = bool(known_cost and inp == 0 and outp == 0) or str(model_id).lower().endswith(
        ("-free", ":free"))
    tier, strengths = "", []
    low = str(model_id).lower()
    for pat in _patterns():
        if fnmatch.fnmatch(low, str(pat.get("match", "")).lower()):
            tier = str(pat.get("tier") or "")
            strengths = [str(s) for s in (pat.get("strengths") or [])]
            if "free" in pat:
                free = bool(pat["free"]) or free
            break
    if not tier:
        tier = ("cheap" if inp <= 1.0 else "mid" if inp <= 5.0 else "strong") \
            if known_cost else "mid"
    status = str(meta.get("status") or "").lower()
    # Curated strengths lead; name evidence the pattern did not mention (a
    # `-vl-` vision model, a `mini` fast model) is appended, never dropped.
    # Order matters: substitution_ok compares the first two.
    merged = list(strengths)
    merged += [s for s in _guess_strengths(model_id) if s not in merged]
    return {
        "model": model_id, "tier": tier, "free": free,
        "cost_in": inp, "cost_out": outp,
        "context": int((meta.get("limit") or 0) or 0),
        "reasoning": bool(meta.get("reasoning")),
        "tool_call": bool(meta.get("tool_call", True)),
        "strengths": merged,
        "released": str(meta.get("released") or ""),
        "knowledge": str(meta.get("knowledge") or ""),
        "deprecated": status == "deprecated",
        "known": bool(meta),
    }


def _released_key(value: object) -> int:
    """`2026-04-29` -> 20260429 for a newest-wins tiebreak; bad/empty -> 0."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())[:8]
    try:
        return int(digits) if len(digits) == 8 else 0
    except Exception:
        return 0


def variant_for(model_id: str, wanted: str, reg: dict | None = None,
                trust: bool = False) -> str:
    """`wanted` when the model advertises it; "" when it does not.

    models.dev reports variants as `reasoning_options` (`effort` with values, or
    `toggle`). A variant the provider does not list is dropped when it came from
    a default, but kept when the user pinned the model themselves — they may
    know a gateway the registry does not.
    """
    wanted = str(wanted or "")
    if not wanted:
        return ""
    reg = registry() if reg is None else reg
    prov, _, mid = str(model_id).partition("/")
    meta = (((reg.get(prov) or {}).get("models") or {}).get(mid)) or {}
    for opt in meta.get("reasoning_options") or []:
        if isinstance(opt, dict) and opt.get("type") == "effort":
            if wanted in [str(v) for v in (opt.get("values") or [])]:
                return wanted
    return wanted if trust else ""


def pin_for(model_id: str, variant: str = "") -> str:
    return f"{model_id}#{variant}" if variant else str(model_id)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def load_health() -> dict:
    return _load_json(_health_path(), {})


def save_health(ledger: dict) -> None:
    _write_json(_health_path(), ledger)


def health_state(pin: str, ttl_s: int = 1800,
                 ledger: dict | None = None) -> tuple[str, dict]:
    """ok | failed | stale | unverified, plus the ledger row."""
    row = (load_health() if ledger is None else ledger).get(str(pin))
    if not isinstance(row, dict):
        return "unverified", {}
    try:
        age = int(time.time()) - int(row.get("checked_at") or 0)
    except Exception:
        age = 10 ** 9
    if age > max(60, int(ttl_s)):
        return "stale", row
    return ("ok" if row.get("ok") else "failed"), row


def model_health(model_id: str, ttl_s: int = 1800,
                 ledger: dict | None = None) -> tuple[str, str]:
    """ok | failed | dead | unchecked for one model, from the last probes.

    A provider-level failure (quota, auth, no route) retires every model behind
    that provider, so `models list` can answer "which of these actually work"
    without spending a request. Otherwise the newest probe of any variant of
    the model decides. `unchecked` is honest: nothing has proven it either way.
    """
    ledger = load_health() if ledger is None else ledger
    prov = str(model_id).partition("/")[0]
    pstate, prow = health_state(f"__prov__:{prov}", ttl_s, ledger)
    if pstate == "failed":
        return "dead", str((prow or {}).get("reason") or "")
    failed_reason = ""
    for pin, row in ledger.items():
        if str(pin).startswith("__") or not isinstance(row, dict):
            continue
        if str(pin).partition("#")[0] != model_id:
            continue
        state, _ = health_state(str(pin), ttl_s, ledger)
        if state == "ok":
            return "ok", ""
        if state == "failed" and not failed_reason:
            failed_reason = str(row.get("reason") or "probe failed")
    return ("failed", failed_reason) if failed_reason else ("unchecked", "")


def _probe(pin: str, cwd: Path, timeout_s: int) -> dict:
    cmd = ["opencode", "run", "--format", "json", "-m", pin, PROBE_MARK]
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout_s)
    except FileNotFoundError:
        return {"ok": False, "reason": "opencode not on PATH", "cost": 0.0, "reply": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"timeout after {timeout_s}s", "cost": 0.0,
                "reply": ""}
    text, cost, err = "", 0.0, ""
    for line in (r.stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        part = ev.get("part") or {}
        if part.get("type") == "text":
            text += str(part.get("text") or "")
        elif part.get("type") == "step-finish":
            try:
                cost = float(part.get("cost") or 0) or cost
            except Exception:
                pass
        elif ev.get("type") == "error":
            # The provider's own verdict (quota, auth, model not found) is the
            # useful reason; the process exit code alone says nothing.
            e = ev.get("error") or {}
            err = f"{e.get('type') or 'error'}: {str(e.get('message') or '')[:140]}".strip()
    ok = r.returncode == 0 and PROBE_OK in text.upper()
    reason = ""
    if not ok:
        tail = [ln for ln in (r.stderr or "").strip().splitlines() if ln.strip()]
        reason = err or (tail[-1][:160] if tail else
                         f"exit {r.returncode}, " +
                         ("no reply" if not text.strip() else "unexpected reply"))
    return {"ok": ok, "reason": reason, "cost": cost,
            "reply": text.strip()[:40], "error": err}


def check(pins: list[str], cfg: dict, project: str | Path = ".") -> list[dict]:
    """Probe each `model#variant` once, record it, return the results.

    The probe is a real request through opencode (`opencode run`), so it checks
    exactly what a dispatch needs: auth, quota, model name and variant. Cost is
    the request's own reported cost, tiny by construction (`Reply with exactly:
    OK`). Probes run in the project scratch dir so they never create files in
    the product tree.
    """
    timeout_s = int(((cfg.get("models") or {}).get("check_timeout_s")) or 90)
    cwd = state_file(project, "tmp") / "models-probe"
    cwd.mkdir(parents=True, exist_ok=True)
    ledger = load_health()
    out: list[dict] = []
    for pin in dict.fromkeys(str(p) for p in pins if p):
        res = _probe(pin, cwd, timeout_s)
        ledger[pin] = {"ok": res["ok"], "reason": res["reason"],
                       "cost": res["cost"], "checked_at": int(time.time())}
        _mark_provider(ledger, pin, res)
        out.append({"pin": pin, **res})
    if out:
        save_health(ledger)
    return out


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

_TIER_ORDER = ("cheap", "mid", "strong")


def _tier_distance(want: str, have: str) -> int:
    try:
        return abs(_TIER_ORDER.index(want) - _TIER_ORDER.index(have))
    except ValueError:
        return 9


def _unusable(state: str, entry: dict | None) -> bool:
    """A ranking class, not a health verdict: the model's last known probe
    said no — a fresh failure, or that failure cached past the TTL.
    _ranked sinks these rows last and route()/_panel_pick never select them.
    Deliberately NOT used by health_state() or by check_roles()' probe walk:
    a failed pin must stay re-probeable where probes are the point."""
    if state == "failed":
        return True
    return state == "stale" and isinstance(entry, dict) and entry.get("ok") is False


def _health_class(state: str, entry: dict | None) -> int:
    """Selection band: verified-ok 0; stale-ok/unverified 1 (never
    discredited); unusable 2 (last known probe failed)."""
    if _unusable(state, entry):
        return 2
    return 0 if state == "ok" else 1


def _latest_probe_failed(model: str, ledger: dict) -> bool:
    """The newest probe of ANY variant of this model said no, fresh or stale.

    `model_health()` only reports a fresh failure; a new panel pick still needs
    the older verdict, because the untried twin pin would repeat it. An ok row
    newer than every failed row clears the model.
    """
    newest: tuple[int, dict] | None = None
    for pin, row in ledger.items():
        if str(pin).startswith("__") or not isinstance(row, dict):
            continue
        if str(pin).partition("#")[0] != str(model):
            continue
        try:
            ts = int(row.get("checked_at") or 0)
        except Exception:
            ts = 0
        if newest is None or ts >= newest[0]:
            newest = (ts, row)
    return bool(newest and newest[1].get("ok") is False)


def role_routes(cfg: dict) -> dict:
    return dict(((cfg.get("models") or {}).get("routes") or {}))


# Only failures that describe the PROVIDER itself retire every model behind it.
# `provider.no-route`/`provider.not-found` name one withdrawn model; treating
# them as provider-wide death hid every healthy sibling behind that gateway.
DEAD_PROVIDER_ERRORS = ("provider.quota", "provider.auth", "provider.unauthorized",
                        "provider.invalid-request")


def _provider_dead(model: str, ttl: int, ledger: dict) -> tuple[bool, str]:
    state, row = health_state(f"__prov__:{str(model).partition('/')[0]}", ttl, ledger)
    return state == "failed", str((row or {}).get("reason") or "")


def _mark_provider(ledger: dict, pin: str, res: dict) -> None:
    """A provider-wide failure (no credits, bad token, no route) retires the
    whole provider for this TTL — probing its next 40 models would only repeat
    the same error."""
    prov = str(pin).partition("/")[0]
    err = str(res.get("error") or "")
    if res.get("ok"):
        ledger.pop(f"__prov__:{prov}", None)
    elif any(k in err for k in DEAD_PROVIDER_ERRORS):
        ledger[f"__prov__:{prov}"] = {"ok": False, "reason": err[:160],
                                     "checked_at": int(time.time())}


def _ranked(role: str, spec: dict, pool: list[dict], reg: dict,
            prefer_free: bool, ttl: int, ledger: dict,
            quotas: dict | None = None) -> tuple[list[tuple[dict, str, dict]], str]:
    """Candidates for one role, best first, with their health state."""
    tier_wanted = str(spec.get("tier") or "mid")
    quotas = {} if quotas is None else quotas
    explicit = str(spec.get("model") or "")
    wanted_variant = str(spec.get("variant") or "")
    note = ""

    def tier_pool(t: str) -> list[dict]:
        rows = [m for m in pool if m["tier"] == t]
        alive = [m for m in rows if not _provider_dead(m["model"], ttl, ledger)[0]]
        return alive or ([] if rows else [])

    if explicit:
        cands = [classify(explicit, reg)]
        note = "pinned by config"
    else:
        # The wanted tier first, then the nearest tiers as fallbacks. This has
        # to include the other tiers even when the wanted one has models: a
        # strong model that failed its probe must not leave the role with no
        # candidate at all while a verified free model is right there. Order is
        # by tier distance, so an unverified wanted-tier model still wins.
        cands = []
        seen_models: set[str] = set()
        for t in sorted({m["tier"] for m in pool},
                        key=lambda t: _tier_distance(tier_wanted, t)):
            for m in tier_pool(t):
                if m["model"] not in seen_models:
                    seen_models.add(m["model"])
                    cands.append(m)
    if not cands:
        return [], note
    role_fit = set(ROLE_STRENGTHS.get(role, ()))

    def rank(m: dict) -> tuple:
        fit = len(role_fit & set(m.get("strengths") or []))
        # Tier distance leads: a fallback tier is only reached when the wanted
        # one has nothing left. Cheap work then takes the free model (that is
        # the point of cheap); mid/strong work takes the most capable one and
        # price breaks ties. A verified model outranks all of it — see below.
        dist = _tier_distance(tier_wanted, str(m.get("tier") or "mid"))
        # Release date is the LAST tiebreak: newer wins only when tier, fit,
        # price and context are equal. It never overrides capability.
        if tier_wanted == "cheap":
            return (dist, 0 if (m["free"] and prefer_free) else 1, -fit, m["cost_out"],
                    m["cost_in"], -int(m.get("context") or 0),
                    -_released_key(m.get("released")), m["model"])
        return (dist, -fit, 0 if (m["free"] and prefer_free) else 1, m["cost_out"],
                m["cost_in"], -int(m.get("context") or 0),
                -_released_key(m.get("released")), m["model"])

    ranked: list[tuple[dict, str, dict]] = []
    for cand in cands:
        variant = variant_for(cand["model"], wanted_variant, reg, trust=bool(explicit))
        pin = pin_for(cand["model"], variant)
        hstate, hrow = health_state(pin, ttl, ledger)
        prov = str(cand["model"]).partition("/")[0]
        if prov not in quotas:
            quotas[prov] = usage.quota_state(prov)
        quota = quotas[prov]
        ranked.append(({**cand, "variant": variant, "pin": pin,
                        "provider": prov, "quota": quota,
                        "quota_note": usage.quota_note(quota)}, hstate, hrow))
    # Band by what the last probe said BEFORE rank order: ok first; a merely
    # stale or never-probed pin in the middle (it was never discredited); a
    # pin whose last known probe failed last. Selection overrides in route()
    # also skip the last band outright.
    ranked.sort(key=lambda r: (_health_class(r[1], r[2]),) + rank(r[0]))
    return ranked, note


def _pool(available_models: list[str] | None = None, reg: dict | None = None) -> list[dict]:
    """Every model this install could run a job on: the synced list, classified,
    minus no-tool-call and curated-excluded ids. Shared by route(), check_roles()
    and panel_pool() so the three never disagree about eligibility."""
    reg = registry() if reg is None else reg
    avail = list(available_models) if available_models is not None else available()
    pool = [classify(m, reg) for m in avail]
    return [m for m in pool
            if m["tool_call"] and not excluded(m["model"])
            and not m.get("deprecated")]


def route(cfg: dict, roles: list[str] | None = None,
          available_models: list[str] | None = None,
          reg: dict | None = None) -> list[dict]:
    """One row per worker role: model, variant, tier, free, health, fallback."""
    cfgm = cfg.get("models") or {}
    reg = registry() if reg is None else reg
    avail = list(available_models) if available_models is not None else available()
    pool = _pool(avail, reg)
    prefer_free = bool(cfgm.get("prefer_free", True))
    ttl = int(cfgm.get("health_ttl_s") or 1800)
    ledger = load_health()
    quotas: dict[str, dict] = {}
    want = {str(r) for r in (roles or [])}
    rows: list[dict] = []
    # Every role is resolved, then the requested ones are filtered out at the
    # end. Filtering first left a lone strong role guessing its verdict from
    # a half-empty candidate walk (`--roles deep` once reported a paid
    # unverified model while the full route answered with another). One
    # check, one answer, whichever slice you ask for.
    for role, spec in role_routes(cfg).items():
        spec = spec if isinstance(spec, dict) else {}
        ranked, note = _ranked(role, spec, pool, reg, prefer_free, ttl, ledger, quotas)
        # Do not route into a provider whose published window is already spent
        # while another candidate has room. If every candidate is exhausted,
        # keep one and say so (a note, never a silent 429).
        open_rows = [r for r in ranked
                     if not (r[0].get("quota") or {}).get("exhausted")]
        if open_rows:
            ranked = open_rows
        elif ranked:
            ranked = ranked[:1]
            note = note or ("every candidate's provider quota is exhausted — "
                            f"using {ranked[0][0]['provider']}")
        if not ranked:
            rows.append({"role": role, "wanted_tier": str(spec.get("tier") or "mid"),
                         "tier": str(spec.get("tier") or "mid"),
                         "model": "", "variant": "", "pin": "", "free": False,
                         "cost_in": 0.0, "cost_out": 0.0, "context": 0,
                         "health": "unverified", "checked_at": 0, "reason": "",
                         "strengths": [], "fallback": "", "fallback_pin": "",
                         "note": note or (("no model list — run `harness models sync`")
                                          if not avail else
                                          "no reachable model — every provider is down; "
                                          "`harness models check` shows why")})
            continue
        wanted_tier = str(spec.get("tier") or "mid")
        usable = [r for r in ranked if not _unusable(r[1], r[2])]
        # Selection envelope: tier-distance <= 1. A cheap verified model is
        # never written into a strong role just because it answered.
        within = [r for r in usable
                  if _tier_distance(wanted_tier, r[0]["tier"]) <= 1]
        if within:
            chosen, state, entry = within[0]
            if chosen["tier"] != wanted_tier and not note:
                note = f"no {wanted_tier} model available — using {chosen['tier']}"
            fallback_row = next((r for r in within[1:] if r[0]["model"] != chosen["model"]), None)
            fallback = fallback_row[0]["pin"] if fallback_row else ""
            rows.append({
                "role": role, "wanted_tier": wanted_tier,
                "tier": chosen["tier"], "model": chosen["model"],
                "variant": chosen["variant"], "pin": chosen["pin"], "free": chosen["free"],
                "provider": chosen.get("provider") or chosen["model"].partition("/")[0],
                "quota": chosen.get("quota") or {},
                "quota_note": chosen.get("quota_note") or "",
                "cost_in": chosen["cost_in"], "cost_out": chosen["cost_out"],
                "context": chosen.get("context", 0), "strengths": chosen.get("strengths") or [],
                "health": state, "checked_at": int((entry or {}).get("checked_at") or 0),
                "reason": str((entry or {}).get("reason") or ""),
                "fallback": fallback.partition("#")[0], "fallback_pin": fallback,
                "note": note,
            })
            continue
        # Nothing within one tier of the requirement survived the health rule:
        # the row would otherwise carry the best within-1 pin, whose last
        # known probe is bad. The old `fallback_to_verified` collapse handled
        # this by writing ONE global best verified row into every affected
        # role — a strong reviewer got the quick job's free model, variant
        # and all. Per-role instead: consider only verified candidates for
        # THIS role and take one the substitution rule accepts (it compares
        # against the wanted tier, so a >1-tier downgrade can never be
        # written). With no conforming verified model the row goes out
        # UNPINNED and the agent inherits the session model. Selectable
        # non-ok pins (stale-ok / unverified) never reach this branch — they
        # were never discredited, and a verified conforming candidate would
        # already have ranked above them.
        within_all = [r for r in ranked
                      if _tier_distance(wanted_tier, r[0]["tier"]) <= 1]
        best = within_all[0] if within_all else ranked[0]
        old_pin = str(best[0].get("pin") or "")
        verified = [r for r in ranked if r[1] == "ok" and r[0]["pin"]
                    and bool(cfgm.get("fallback_to_verified", True))]
        passing = []
        if verified:
            req = {"tier": wanted_tier,
                   "strengths": list(ROLE_STRENGTHS.get(role, ()))}
            passing = [r for r in verified
                       if substitution_ok(req, old_pin, r[0]["pin"])[0]]
        if passing:
            rc, _rs, r_entry = sorted(
                passing, key=lambda r: (0 if r[0]["free"] else 1,
                                        r[0]["cost_out"], r[0]["model"]))[0]
            rows.append({"role": role, "wanted_tier": wanted_tier,
                         "tier": rc["tier"], "model": rc["model"],
                         "variant": rc["variant"], "pin": rc["pin"], "free": rc["free"],
                         "provider": rc.get("provider") or rc["model"].partition("/")[0],
                         "quota": rc.get("quota") or {},
                         "quota_note": rc.get("quota_note") or "",
                         "cost_in": rc["cost_in"], "cost_out": rc["cost_out"],
                         "context": rc.get("context", 0),
                         "strengths": rc.get("strengths") or [],
                         "health": "ok",
                         "checked_at": int((r_entry or {}).get("checked_at") or 0),
                         "reason": "", "fallback": "", "fallback_pin": "",
                         "note": f"fallback to verified {rc['pin']}"})
            continue
        bc, b_state, b_entry = best[0], best[1], (best[2] or {})
        rows.append({"role": role, "wanted_tier": wanted_tier,
                     "tier": bc.get("tier") or wanted_tier,
                     "model": "", "variant": "", "pin": "",
                     "provider": bc.get("provider") or "", "quota": {},
                     "quota_note": "",
                     "free": bool(bc.get("free")),
                     "cost_in": float(bc.get("cost_in") or 0.0),
                     "cost_out": float(bc.get("cost_out") or 0.0),
                     "context": int(bc.get("context") or 0),
                     "strengths": list(bc.get("strengths") or []),
                     "health": b_state,
                     "checked_at": int((b_entry or {}).get("checked_at") or 0),
                     "reason": str((b_entry or {}).get("reason") or ""),
                     "fallback": "", "fallback_pin": "",
                     "note": (f"no verified model passes the {wanted_tier} requirement — "
                              "agent inherits session model") if verified else
                     (f"no usable {wanted_tier} model within one tier — "
                      "agent inherits session model")})
        continue
    for row in rows:
        row.setdefault("provider", str(row.get("model") or "").partition("/")[0])
        row.setdefault("quota", {})
        row.setdefault("quota_note", "")
        if row.get("model"):
            cand = classify(row["model"], reg)
            row["released"] = cand.get("released") or ""
            row["knowledge"] = cand.get("knowledge") or ""
            if not row.get("strengths"):
                row["strengths"] = list(cand.get("strengths") or [])
    if want:
        rows = [r for r in rows if r["role"] in want]
        # A role nobody configured must still be answered: dropping it silently
        # would have the orchestrator dispatch a typo'd role with no pin and no
        # warning. It is reported as undispatchable, like a failed one.
        for role in sorted(want - {r["role"] for r in rows}):
            rows.append({"role": role, "wanted_tier": "", "tier": "",
                         "model": "", "variant": "", "pin": "", "free": False,
                         "cost_in": 0.0, "cost_out": 0.0, "context": 0,
                         "health": "unverified", "checked_at": 0, "reason": "",
                         "strengths": [], "fallback": "", "fallback_pin": "",
                         "note": "unknown role — add it to models.routes"})
    return rows


def check_roles(cfg: dict, roles: list[str], project: str | Path = ".") -> list[dict]:
    """Probe candidates for these roles until one answers, then record it.

    This is the pre-dispatch check: the orchestrator names the roles it is about
    to launch, and the first ranked model that answers is what `route()` will
    then report. A role already verified inside the TTL is not re-probed (a
    check is a real request, not a ping). A provider that answers with quota or
    auth is retired for the TTL, so the next round skips it instead of probing
    its neighbours one by one.
    """
    cfgm = cfg.get("models") or {}
    reg = registry()
    pool = _pool(reg=reg)
    prefer_free = bool(cfgm.get("prefer_free", True))
    ttl = int(cfgm.get("health_ttl_s") or 1800)
    limit = max(1, int(cfgm.get("check_candidates") or 3))
    timeout_s = int(cfgm.get("check_timeout_s") or 90)
    quotas: dict[str, dict] = {}
    cwd = state_file(project, "tmp") / "models-probe"
    cwd.mkdir(parents=True, exist_ok=True)
    ledger = load_health()
    out: list[dict] = []
    routes = role_routes(cfg)
    for role in roles:
        spec = routes.get(role)
        if not isinstance(spec, dict):
            continue
        probed: set[str] = set()
        ok = False
        wanted = str(spec.get("tier") or "mid")
        for _round in range(2):
            ranked, _note = _ranked(role, spec, pool, reg, prefer_free, ttl, ledger, quotas)
            # A verified candidate only ends the walk when it is at the tier the
            # role actually wants. Stopping on a verified mid left every strong
            # candidate unprobed and `deep` answering mid forever.
            if any(r[1] == "ok" and r[0]["tier"] == wanted for r in ranked):
                ok = True  # verified inside the TTL; no request spent
                break
            # Spend the next probe on the top unverified WANTED-tier candidate;
            # only when that tier has none left fall through to one candidate
            # per tier (so a dead strong tier still gets a mid, then a cheap).
            wanted_cands = [r for r in ranked
                            if r[0]["tier"] == wanted and r[1] != "ok"
                            and r[0]["pin"] not in probed]
            if wanted_cands:
                cands = wanted_cands[:1]
            else:
                cands = []
                seen_tiers: set[str] = set()
                for r in ranked:
                    if r[1] == "failed" or r[0]["pin"] in probed:
                        continue
                    if r[0]["tier"] in seen_tiers:
                        continue
                    seen_tiers.add(r[0]["tier"])
                    cands.append(r)
            cands = cands[:limit]
            if not cands:
                break
            for cand, _state, _row in cands:
                pin = cand["pin"]
                res = _probe(pin, cwd, timeout_s)
                probed.add(pin)
                ledger[pin] = {"ok": res["ok"], "reason": res["reason"],
                               "cost": res["cost"], "checked_at": int(time.time())}
                _mark_provider(ledger, pin, res)
                out.append({"pin": pin, **res})
                if res["ok"]:
                    ok = True
                    break
            if ok:
                break
    if out:
        save_health(ledger)
    return out


def pin_rows(rows: list[dict]) -> dict[str, str]:
    """role -> `model#variant` for roles that resolved to a model."""
    return {r["role"]: r["pin"] for r in rows if r.get("pin")}


# ---------------------------------------------------------------------------
# The panel: a topic-scoped roster of DISTINCT upstreams
# ---------------------------------------------------------------------------

PANEL_CYCLE = ("panel-lead", "panel-peer", "panel-skeptic", "panel-edge")
_UPSTREAM_INDEX: dict[str, str] | None = None


def panel_cycle(size: int) -> list[str]:
    """The role order for a 2-4 seat panel; size 4 adds the cheap edge."""
    return list(PANEL_CYCLE[:max(2, min(4, int(size or 3)))])


def _load_upstream_table() -> dict[str, str]:
    """normalized gateway id / upstream name -> canonical upstream name.

    Sourced from the hand-seeded harness/data/panel-upstream.json. It is ONLY
    ever human-maintained - nothing here fetches or refreshes it (auto-refresh
    is on the spec's no-go list); a corrupt or missing file is an empty index
    and the normalized-id fallback takes over."""
    global _UPSTREAM_INDEX
    if _UPSTREAM_INDEX is None:
        index: dict[str, str] = {}
        try:
            data = json.loads((Path(__file__).resolve().parent / "data" /
                               "panel-upstream.json").read_text())
        except Exception:
            data = {}
        for name, ids in (((data or {}).get("upstreams") or {}).items()
                          if isinstance((data or {}).get("upstreams"), dict) else []):
            name = str(name).strip().lower()
            if not name:
                continue
            index[name] = name
            for mid in ids if isinstance(ids, list) else []:
                key = _bare_upstream(str(mid))
                if key:
                    index[key] = name
        _UPSTREAM_INDEX = index
    return _UPSTREAM_INDEX


def _bare_upstream(gateway_id: str) -> str:
    """Dedup normalization: lowercase, strip `:free`, strip `#variant`, strip the
    gateway prefix (the element before the first `/`)."""
    s = str(gateway_id).strip().lower().split("#", 1)[0]
    if s.endswith(":free"):
        s = s[:-5]
    _, sep, rest = s.partition("/")
    return rest if sep and rest else s


def upstream_key(model_id: str, index: dict | None = None) -> str:
    """The upstream a gateway id serves, i.e. the panel dedupe key.

    A table hit wins; otherwise the normalized id. A multi-segment miss
    (`google/lyria-3` after the prefix strip) keeps its gateway prefix, so two
    gateways naming the same multi-segment id never collapse into one —
    under-dedupe is safe, over-dedupe starves the panel."""
    idx = _load_upstream_table() if index is None else index
    full = str(model_id).strip().lower().split("#", 1)[0]
    if full.endswith(":free"):
        full = full[:-5]
    bare = _bare_upstream(full)
    hit = idx.get(bare) or idx.get(full)
    if hit:
        return str(hit)
    if "/" in bare:
        return full  # multi-segment miss => NO dedupe
    return bare


def substitution_ok(requirement: dict, old_pin: str, new_pin: str,
                    reg: dict | None = None) -> tuple[bool, str]:
    """The drift capability rule: a replacement must be within one tier of the
    REQUIREMENT (never the departing pin's own tier — "always substitute against
    the requirement") and keep at least one of the departing pin's first two
    strengths. Returns (ok, why)."""
    reg = registry() if reg is None else reg
    new = classify(str(new_pin).partition("#")[0], reg)
    dist = _tier_distance(str(requirement.get("tier") or "mid"), new["tier"])
    if dist > 1:
        return False, f"{new['tier']} is {dist} tier(s) from {requirement.get('tier')}"
    old = classify(str(old_pin).partition("#")[0], reg)
    keep = [str(s) for s in (old.get("strengths") or [])[:2]]
    if keep and not (set(keep) & set(new.get("strengths") or [])):
        return False, f"strengths {keep} not preserved by {new_pin}"
    return True, ""


def _requirement(role: str, spec: dict) -> dict:
    return {"tier": str(spec.get("tier") or "mid"),
            "strengths": list(ROLE_STRENGTHS.get(role, ()))}


def _panel_pick(role: str, spec: dict, pool: list[dict], reg: dict, cfg: dict,
                used: set[str], rule_req: dict | None = None,
                rule_old_pin: str = "", exclude: dict | None = None) -> list:
    """The first ranked, health-usable candidate on an UNUSED upstream.

    A model whose latest probe failed (any variant, fresh or stale) is not
    seated for a NEW pick: the twin pin was never probed and would repeat the
    failure. With rule_req set (a DRIFT substitution) the candidate must
    additionally satisfy substitution_ok() against the requirement. Returns
    [cand_row, health_state, health_entry] or [None, "", {}]."""
    cfgm = cfg.get("models") or {}
    prefer_free = bool(cfgm.get("prefer_free", True))
    ttl = int(cfgm.get("health_ttl_s") or 1800)
    ledger = load_health()
    ranked, _note = _ranked(role, spec, pool, reg, prefer_free, ttl, ledger)
    usable = [r for r in ranked if not _unusable(r[1], r[2])] or ranked
    banned = (exclude or {}).get(role) or set()
    for cand, state, entry in usable:
        if cand["pin"] in banned or cand["model"] in banned:
            continue  # a retired seat is never re-seated for that role
        if upstream_key(cand["model"], None) in used:
            continue
        if _latest_probe_failed(cand["model"], ledger):
            continue
        if rule_req is not None:
            ok, _why = substitution_ok(rule_req, rule_old_pin, cand["pin"])
            if not ok:
                continue
        return [cand, state, entry]
    return [None, "", {}]


def panel_pool(cfg: dict, size: int = 3, roles: list[str] | None = None,
               prior: dict | None = None,
               exclude: dict[str, set[str]] | None = None,
               reserved: set[str] | None = None) -> dict:
    """The 2-4 seat panel: a roster of DISTINCT upstreams, one panelist per role.

    Dedupe is by upstream, not gateway id — `opencode/X`, `openrouter/X:free`
    and `kilo/X:free` are one family and count once (upstream_key). A seat on
    an already-taken upstream stays empty: starving beats redundancy, and two
    panelists on one family would argue as one.

    With `prior` (a previous bundle's {"panel": [...]}) this is the consumption
    re-resolution. Three passes: drifted prior seats first (their substitute
    must satisfy the capability rule, and may claim the upstream a healthy seat
    holds — that seat is then re-resolved and recorded too), healthy prior
    seats keep their pin, then brand-new seats. Every prior pin that changes
    is recorded in `model_replaced`; under-substitution is safe, duplication
    of an upstream is not."""
    order = list(roles) if roles else panel_cycle(size)
    routes = role_routes(cfg)
    reg = registry()
    pool = _pool(reg=reg)
    index = _load_upstream_table()
    ttl = int(cfg.get("health_ttl_s") or 1800)
    # `reserved` is for a phase-scoped rebuild: seats this call is NOT
    # re-resolving still hold their upstreams, so a new pick must not take one.
    used: set[str] = set(reserved or ())
    swaps: list[dict] = []
    exclude = exclude or {}
    prior_rows: dict[str, dict] = {}
    for r in ((prior or {}).get("panel") or []):
        if not isinstance(r, dict):
            continue
        role = str(r.get("role"))
        if str(r.get("pin") or "") in (exclude.get(role) or set()):
            continue  # retired: its role takes a fresh seat below
        prior_rows[role] = r
    drifts, keepers, fresh, seats = [], [], [], {}
    prior_state: dict[str, tuple[str, dict]] = {}
    for role in order:
        spec = routes.get(role)
        if not isinstance(spec, dict):
            continue  # only configured roles take a seat
        old = prior_rows.get(role)
        if old is None:
            fresh.append(role)
        elif health_state(str(old.get("pin") or ""), ttl)[0] in ("failed", "stale"):
            prior_state[role] = health_state(str(old.get("pin") or ""), ttl)
            drifts.append(role)
        else:
            keepers.append(role)

    def _add(role: str, cand: dict, state: str, entry: dict,
             requirement: dict) -> None:
        seats[role] = {"role": role, "requirement": requirement,
                       "pin": cand["pin"],
                       "upstream": upstream_key(cand["model"], index),
                       "health": state,
                       "checked_at": int((entry or {}).get("checked_at") or 0),
                       "fallback_pin": ""}
        used.add(upstream_key(str(cand["pin"]).partition("#")[0], index))

    def _refresh(role: str, old: dict) -> None:
        state, entry = health_state(str(old.get("pin") or ""), ttl)
        kept = dict(old)
        kept["requirement"] = _requirement(role, routes.get(role) or {})
        kept["health"] = state
        kept["checked_at"] = int((entry or {}).get("checked_at") or 0)
        seats[role] = kept
        used.add(upstream_key(str(old.get("pin") or "").partition("#")[0], index))

    # drifted seats claim first: the substitute is walked against the capability
    # rule and may take the upstream a healthy seat was merely resting on
    for role in drifts:
        spec = routes.get(role) or {}
        old = prior_rows[role]
        used.add(upstream_key(str(old.get("pin") or "").partition("#")[0], index))
        cand, state, entry = _panel_pick(
            role, spec, pool, reg, cfg, used,
            rule_req=_requirement(role, spec), rule_old_pin=str(old.get("pin") or ""),
            exclude=exclude)
        if cand is None:
            # no acceptable substitute: keep the stored row — its failed health
            # is the wave's own visible blocker (under-substitution is safe)
            seats[role] = {**old, "requirement": _requirement(role, spec)}
            continue
        _add(role, cand, state, entry, _requirement(role, spec))
        old_state, old_entry = prior_state.get(role, ("drifted", {}))
        swaps.append({"role": role, "old": str(old.get("pin") or ""),
                      "new": cand["pin"],
                      "reason": f"{old_state}: {str((old_entry or {}).get('reason') or 'probe drifted')}",
                      "at": int(time.time())})

    for role in keepers:
        spec = routes.get(role) or {}
        old = prior_rows[role]
        if upstream_key(str(old.get("pin") or "").partition("#")[0], index) not in used:
            _refresh(role, old)
            continue
        # displaced by a drifted seat's substitute: re-resolve under the SAME
        # capability rule checked against the requirement — a healthy seat must
        # not be silently downgraded below what it was seated for
        cand, state, entry = _panel_pick(
            role, spec, pool, reg, cfg, used,
            rule_req=_requirement(role, spec), rule_old_pin=str(old.get("pin") or ""),
            exclude=exclude)
        if cand is None:
            # no compatible pin left on a free upstream: the seat starves, and
            # the swap row carries the constraint so the downgrade is visible
            swaps.append({"role": role, "old": str(old.get("pin") or ""),
                          "new": "",
                          "reason": "displaced-by-drift: no free upstream satisfies the "
                                    f"{_requirement(role, spec)['tier']} requirement",
                          "at": int(time.time())})
            continue
        _add(role, cand, state, entry, _requirement(role, spec))
        swaps.append({"role": role, "old": str(old.get("pin") or ""),
                      "new": cand["pin"],
                      "reason": "displaced-by-drift: its upstream is held by another seat",
                      "at": int(time.time())})

    for role in fresh:
        spec = routes.get(role) or {}
        cand, state, entry = _panel_pick(role, spec, pool, reg, cfg, used,
                                         exclude=exclude)
        if cand is not None:
            _add(role, cand, state, entry, _requirement(role, spec))

    rows = [seats[role] for role in order if role in seats]
    for row in rows:
        # every seat carries a fallback: its next usable candidate on a
        # different upstream, so a dead panelist has a known spare
        _next_fallback(row, routes, pool, reg, cfg, exclude)
    return {"panel": rows, "model_replaced": swaps}


def _next_fallback(row: dict, routes: dict, pool: list[dict], reg: dict, cfg,
                   exclude: dict | None = None) -> None:
    """First usable candidate on a DIFFERENT upstream than this seat's pin."""
    role = row["role"]
    spec = routes.get(role)
    if not isinstance(spec, dict):
        return
    ranked, _note = _ranked(role, spec, pool, reg,
                            bool((cfg.get("models") or {}).get("prefer_free", True)),
                            int((cfg.get("models") or {}).get("health_ttl_s") or 1800),
                            load_health())
    usable = [r for r in ranked if r[1] != "failed"] or ranked
    own = upstream_key(str(row["pin"]).partition("#")[0], None)
    ledger = load_health()
    ttl = int((cfg.get("models") or {}).get("health_ttl_s") or 1800)
    banned = (exclude or {}).get(role) or set()
    for cand, _state, _entry in usable:
        if cand["pin"] in banned or cand["model"] in banned:
            continue
        if upstream_key(cand["model"], None) == own:
            continue  # the spare must sit on a DIFFERENT upstream than the seat
        if _latest_probe_failed(cand["model"], ledger):
            continue
        row["fallback_pin"] = cand["pin"]
        return
