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


def available() -> list[str]:
    """`opencode models` ids from the last sync. Empty is a valid answer."""
    data = _load_json(_available_path(), {})
    return [str(m) for m in (data.get("models") or []) if "/" in str(m)]


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
            }
        if rows:
            out[pid] = {"models": rows}
    return out


def sync(with_registry: bool = True, timeout: int = 30) -> dict:
    """Refresh what this install can route to: the model list, then metadata."""
    ids = _opencode_models()
    _write_json(_available_path(), {"refreshed_at": int(time.time()), "models": ids})
    providers = {m.partition("/")[0] for m in ids}
    result = {"models": len(ids), "providers": len(providers), "registry": 0, "error": ""}
    if with_registry and ids:
        try:
            reg = _fetch_registry(providers, timeout=timeout)
            _write_json(_registry_path(), {"refreshed_at": int(time.time()),
                                           "providers": reg})
            result["registry"] = sum(len(p.get("models") or {}) for p in reg.values())
        except Exception as e:  # offline is not fatal: patterns still route
            result["error"] = str(e)[:160]
    return result


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
    free = bool(known_cost and inp == 0 and outp == 0)
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
    return {
        "model": model_id, "tier": tier, "free": free,
        "cost_in": inp, "cost_out": outp,
        "context": int((meta.get("limit") or 0) or 0),
        "reasoning": bool(meta.get("reasoning")),
        "tool_call": bool(meta.get("tool_call", True)),
        "strengths": strengths or _guess_strengths(model_id),
        "known": bool(meta),
    }


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


def role_routes(cfg: dict) -> dict:
    return dict(((cfg.get("models") or {}).get("routes") or {}))


DEAD_PROVIDER_ERRORS = ("provider.quota", "provider.auth", "provider.no-route",
                        "provider.invalid-request", "provider.not-found",
                        "provider.unauthorized")


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
            prefer_free: bool, ttl: int, ledger: dict) -> tuple[list[tuple[dict, str, dict]], str]:
    """Candidates for one role, best first, with their health state."""
    tier_wanted = str(spec.get("tier") or "mid")
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
        if tier_wanted == "cheap":
            return (dist, 0 if (m["free"] and prefer_free) else 1, -fit, m["cost_out"],
                    m["cost_in"], -int(m.get("context") or 0), m["model"])
        return (dist, -fit, 0 if (m["free"] and prefer_free) else 1, m["cost_out"],
                m["cost_in"], -int(m.get("context") or 0), m["model"])

    ranked: list[tuple[dict, str, dict]] = []
    for cand in cands:
        variant = variant_for(cand["model"], wanted_variant, reg, trust=bool(explicit))
        pin = pin_for(cand["model"], variant)
        hstate, hrow = health_state(pin, ttl, ledger)
        ranked.append(({**cand, "variant": variant, "pin": pin}, hstate, hrow))
    # A model the last probe proved is safe to dispatch to; one it failed is
    # last (never chosen while an alternative exists).
    ranked.sort(key=lambda r: (0 if r[1] == "ok" else 1, rank(r[0])))
    return ranked, note


def route(cfg: dict, roles: list[str] | None = None,
          available_models: list[str] | None = None,
          reg: dict | None = None) -> list[dict]:
    """One row per worker role: model, variant, tier, free, health, fallback."""
    cfgm = cfg.get("models") or {}
    reg = registry() if reg is None else reg
    avail = list(available_models) if available_models is not None else available()
    pool = [classify(m, reg) for m in avail]
    pool = [m for m in pool if m["tool_call"] and not excluded(m["model"])]
    prefer_free = bool(cfgm.get("prefer_free", True))
    ttl = int(cfgm.get("health_ttl_s") or 1800)
    ledger = load_health()
    want = {str(r) for r in (roles or [])}
    rows: list[dict] = []
    # Every role is resolved, then the requested ones are filtered out at the
    # end. Filtering first broke the verified downgrade below: a lone strong
    # role saw no verified row besides itself, so `--roles deep` reported a
    # paid unverified model while the unfiltered route used the verified free
    # one. One check, one answer, whichever slice you ask for.
    for role, spec in role_routes(cfg).items():
        spec = spec if isinstance(spec, dict) else {}
        ranked, note = _ranked(role, spec, pool, reg, prefer_free, ttl, ledger)
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
        usable = [r for r in ranked if r[1] != "failed"] or ranked
        chosen, state, entry = usable[0]
        wanted_tier = str(spec.get("tier") or "mid")
        if chosen["tier"] != wanted_tier and not note:
            note = f"no {wanted_tier} model available — using {chosen['tier']}"
        fallback_row = next((r for r in usable[1:] if r[0]["model"] != chosen["model"]), None)
        fallback = fallback_row[0]["pin"] if fallback_row else ""
        rows.append({
            "role": role, "wanted_tier": str(spec.get("tier") or "mid"),
            "tier": chosen["tier"], "model": chosen["model"],
            "variant": chosen["variant"], "pin": chosen["pin"], "free": chosen["free"],
            "cost_in": chosen["cost_in"], "cost_out": chosen["cost_out"],
            "context": chosen.get("context", 0), "strengths": chosen.get("strengths") or [],
            "health": state, "checked_at": int((entry or {}).get("checked_at") or 0),
            "reason": str((entry or {}).get("reason") or ""),
            "fallback": fallback.partition("#")[0], "fallback_pin": fallback,
            "note": note,
        })
    # A paid tier whose probes already failed on every provider (no credits, bad
    # token) must not keep a role on an unverified model that will 402 while a
    # verified model exists. Downgrade explicitly, with the note saying so.
    if bool(cfgm.get("fallback_to_verified", True)):
        verified = [r for r in rows if r["health"] == "ok" and r["pin"]]
        failed_pins = [p for p, v in ledger.items()
                       if isinstance(v, dict) and v.get("ok") is False
                       and not str(p).startswith("__")]
        if verified and failed_pins:
            best = sorted(verified, key=lambda v: (0 if v["free"] else 1,
                                                   v["cost_out"], v["model"]))[0]
            failed_tiers = {classify(p.partition("#")[0], reg)["tier"] for p in failed_pins}
            for row in rows:
                wanted = row.get("wanted_tier") or row["tier"]
                if (row["health"] == "ok" or not row["pin"]
                        or not ({wanted, row["tier"]} & failed_tiers)):
                    continue
                row.update({"model": best["model"], "variant": best["variant"],
                            "pin": best["pin"], "tier": best["tier"],
                            "free": best["free"], "cost_in": best["cost_in"],
                            "cost_out": best["cost_out"], "health": "ok",
                            "checked_at": best["checked_at"], "reason": "",
                            "fallback": "", "fallback_pin": "",
                            "note": (f"no working {wanted} model — using the verified "
                                     f"{best['tier']} {best['pin']}")})
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
    pool = [classify(m, reg) for m in available()]
    pool = [m for m in pool if m["tool_call"] and not excluded(m["model"])]
    prefer_free = bool(cfgm.get("prefer_free", True))
    ttl = int(cfgm.get("health_ttl_s") or 1800)
    limit = max(1, int(cfgm.get("check_candidates") or 3))
    timeout_s = int(cfgm.get("check_timeout_s") or 90)
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
        for _round in range(2):
            ranked, _note = _ranked(role, spec, pool, reg, prefer_free, ttl, ledger)
            if any(r[1] == "ok" for r in ranked):
                ok = True  # verified inside the TTL; no request spent
                break
            # One candidate per tier per round: when every strong model is
            # dead, the next probe is a mid model, then a cheap (often free)
            # one — not three more strong failures. A tier's next model is
            # picked up by the following round, since `probed` excludes it.
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
