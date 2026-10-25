import json
import os
import time
os.environ["HARNESS_MOCK"] = "1"


def _user_model_home(tmp_path, monkeypatch, model="acme/bar-1"):
    cfgdir = tmp_path / "cfg"
    (cfgdir / "opencode").mkdir(parents=True)
    (cfgdir / "opencode" / "opencode.json").write_text(json.dumps({"model": model}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    return model


def test_resolve_explicit_passthrough(tmp_path, monkeypatch):
    _user_model_home(tmp_path, monkeypatch)
    from harness import models as M
    assert M.resolve_model("acme/other", {}) == "acme/other"


def test_resolve_bare_falls_back_to_user_default(tmp_path, monkeypatch):
    want = _user_model_home(tmp_path, monkeypatch, "acme/bar-1")
    from harness import models as M
    assert M.resolve_model("", {}) == want
    assert M.resolve_model("tag:reasoning", {}) == want  # legacy router id
    assert M.resolve_model("auto-fastest", {}) == want


def test_resolve_profile_with_slash(tmp_path, monkeypatch):
    _user_model_home(tmp_path, monkeypatch)
    from harness import models as M
    assert M.resolve_model("", {"model_profile": "acme/via-env"}) == "acme/via-env"


def test_resolve_missing_raises(tmp_path, monkeypatch):
    cfgdir = tmp_path / "empty"
    cfgdir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    from harness import models as M
    import pytest
    with pytest.raises(RuntimeError):
        M.resolve_model("", {})


# ---------------------------------------------------------------------------
# Routing, free/paid and pre-dispatch health
# ---------------------------------------------------------------------------

CFG = {
    "models": {
        "prefer_free": True,
        "pin_agents": True,
        "fallback_to_verified": True,
        "health_ttl_s": 1800,
        "check_timeout_s": 5,
        "check_candidates": 3,
        "routes": {
            "explore": {"tier": "cheap", "variant": "low"},
            "deep": {"tier": "strong", "variant": "high"},
        },
    },
}


def _meta(cost_in=0.0, cost_out=0.0, ctx=128000):
    return {"cost": {"input": cost_in, "output": cost_out}, "limit": ctx,
            "reasoning": True, "tool_call": True,
            "reasoning_options": [{"type": "effort", "values": ["low", "medium", "high"]}]}


def _home(tmp_path, monkeypatch, health=None):
    """A machine that synced two models: a free cheap one and a paid strong one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HARNESS_HOME", str(home))
    cfgdir = tmp_path / "cfg"
    (cfgdir / "opencode").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-state"))
    now = int(time.time())
    (home / "models-available.json").write_text(json.dumps(
        {"refreshed_at": now, "models": ["freeco/tiny-free", "acme/strong-one"]}))
    (home / "models-dev.json").write_text(json.dumps({
        "refreshed_at": now,
        "providers": {
            "freeco": {"models": {"tiny-free": _meta(0, 0)}},
            "acme": {"models": {"strong-one": _meta(10, 30)}},
        },
    }))
    if health is not None:
        (home / "models-health.json").write_text(json.dumps(health))
    return home


def _ok(ts=None):
    return {"ok": True, "reason": "", "cost": 0.0, "checked_at": ts or int(time.time())}


def _fail(reason="provider.quota: no credits", ts=None):
    return {"ok": False, "reason": reason, "cost": 0.0, "checked_at": ts or int(time.time())}


def test_classify_tier_free_and_variant(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from harness import models as M
    tiny = M.classify("freeco/tiny-free")
    strong = M.classify("acme/strong-one")
    assert (tiny["tier"], tiny["free"]) == ("cheap", True)
    assert (strong["tier"], strong["free"]) == ("strong", False)
    # a variant the provider advertises survives; an unknown one does not
    assert M.variant_for("acme/strong-one", "high") == "high"
    assert M.variant_for("acme/strong-one", "ultra") == ""
    assert M.variant_for("acme/strong-one", "ultra", trust=True) == "ultra"


def test_route_takes_the_free_model_for_cheap_work(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from harness import models as M
    rows = {r["role"]: r for r in M.route(CFG)}
    assert rows["explore"]["pin"] == "freeco/tiny-free#low"
    assert rows["explore"]["free"] is True
    assert rows["deep"]["pin"] == "acme/strong-one#high"
    assert rows["deep"]["health"] == "unverified"


def test_verified_model_outranks_an_unverified_one(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, {"freeco/tiny-free#low": _ok()})
    from harness import models as M
    rows = {r["role"]: r for r in M.route(CFG)}
    assert rows["explore"]["health"] == "ok"


def test_strong_role_is_never_driven_to_a_cross_tier_pin(tmp_path, monkeypatch):
    """No >1-tier downgrade into an agent file: with only a verified cheap
    model alive, `deep` goes out UNPINNED (the agent inherits the session
    model) instead of wearing the cheap pin. And `--roles deep` must answer
    exactly like the full route — what the old test actually pinned down."""
    _home(tmp_path, monkeypatch, {"acme/strong-one#high": _fail(),
                                  "freeco/tiny-free#low": _ok()})
    from harness import models as M
    full = {r["role"]: r for r in M.route(CFG)}
    deep = full["deep"]
    assert not deep["pin"] and not deep["model"] and not deep["variant"]
    assert deep["fallback"] == "" and deep["fallback_pin"] == ""
    assert "tiny-free" not in deep["note"]
    assert "strong" in deep["note"]
    one = M.route(CFG, roles=["deep"])[0]
    assert one["pin"] == deep["pin"] and one["note"] == deep["note"]
    assert one["health"] == deep["health"]


def test_check_roles_falls_through_to_the_model_that_answers(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from harness import models as M
    calls = []

    def fake_probe(pin, cwd, timeout):
        calls.append(pin)
        if pin.startswith("acme/strong"):
            return {"ok": False, "reason": "provider.quota: no credits", "cost": 0.0,
                    "reply": "", "error": "provider.quota: no credits"}
        return {"ok": True, "reason": "", "cost": 0.0, "reply": "OK", "error": ""}

    monkeypatch.setattr(M, "_probe", fake_probe)
    results = M.check_roles(CFG, ["deep"], tmp_path)
    assert [r["ok"] for r in results] == [False, True]
    assert calls[0].startswith("acme/strong") and "tiny-free" in calls[1]
    # the probe records what answered — but routing still respects the tier:
    # the strong role is NOT driven to the verified free model, because a
    # >1-tier downgrade is exactly what the old fallback wrote into agents
    row = M.route(CFG, roles=["deep"])[0]
    assert not row["pin"] and not row["model"]
    assert "strong" in row["note"]
    assert M.model_health("freeco/tiny-free") == ("ok", "")


def test_route_answers_for_a_role_it_cannot_configure(tmp_path, monkeypatch):
    """A requested role missing from models.routes must still be answered.

    Dropping it silently let the orchestrator dispatch a typo'd role with no
    pin and no warning; it now comes back as an undispatchable row.
    """
    _home(tmp_path, monkeypatch)
    from harness import models as M
    rows = M.route(CFG, roles=["explore", "writng"])
    assert [r["role"] for r in rows] == ["explore", "writng"]
    unknown = rows[1]
    assert not unknown["pin"] and unknown["health"] == "unverified"
    assert "unknown role" in unknown["note"]


def test_check_roles_spends_no_request_when_already_verified(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, {"freeco/tiny-free#low": _ok()})
    from harness import models as M
    monkeypatch.setattr(M, "_probe", lambda *a: (_ for _ in ()).throw(AssertionError("probed")))
    assert M.check_roles(CFG, ["explore"], tmp_path) == []


def test_tier_patterns_are_shipped_in_the_package():
    """The curated tier/strength patterns must travel in the wheel.

    Without them an installed harness falls back to cost-only tiers and loses
    the `exclude` list (a music model would become a coding worker).
    """
    from pathlib import Path
    from harness import models as M
    assert M._patterns(), "model-tiers.json did not load"
    assert any(p.get("tier") == "cheap" for p in M._patterns())
    assert M.excluded("openrouter/google/lyria-3")
    root = Path(__file__).resolve().parent.parent
    assert "data/model-tiers.json" in (root / "pyproject.toml").read_text()


def test_same_tier_verified_model_replaces_failed_strong(tmp_path, monkeypatch):
    """A verified same-tier model outranks a failed one WITHOUT dropping the
    role across tiers: deep keeps a strong pin, just the verified one."""
    _home(tmp_path, monkeypatch)
    home = tmp_path / "home"
    now = int(time.time())
    (home / "models-available.json").write_text(json.dumps(
        {"refreshed_at": now,
         "models": ["freeco/tiny-free", "acme/strong-one", "acme/strong-two"]}))
    dev = json.loads((home / "models-dev.json").read_text())
    dev["providers"]["acme"]["models"]["strong-two"] = _meta(11, 30)
    (home / "models-dev.json").write_text(json.dumps(dev))
    (home / "models-health.json").write_text(json.dumps({
        "acme/strong-one#high": _fail(), "acme/strong-two#high": _ok()}))
    from harness import models as M
    deep = {r["role"]: r for r in M.route(CFG)}["deep"]
    assert deep["pin"] == "acme/strong-two#high"
    assert deep["health"] == "ok"
    assert deep["fallback"] == "" and deep["fallback_pin"] == ""


def test_stale_failed_pin_is_never_selected(tmp_path, monkeypatch):
    """A stale ledger row whose probe said no is unusable like a fresh
    failure: never the pin, and never a >1-tier downgrade instead of it —
    the role goes out unpinned."""
    old = int(time.time()) - 4 * 3600
    _home(tmp_path, monkeypatch, {"acme/strong-one#high": _fail(ts=old),
                                  "freeco/tiny-free#low": _ok()})
    from harness import models as M
    rows = {r["role"]: r for r in M.route(CFG)}
    deep = rows["deep"]
    assert not deep["pin"] and not deep["model"]
    assert "strong" in deep["note"]
    assert rows["explore"]["pin"] == "freeco/tiny-free#low"  # cheap work unharmed


def test_installed_strong_agent_never_wears_a_cross_tier_pin(tmp_path, monkeypatch):
    """Opt-in path (`models.pin_agents: true`): `expected_agent_files` renders
    the routing rule — a strong agent wears either a strong pin or none; the
    cheap verified model must never leak into harness-security-auditor.md.
    Under the default (pins off) no subagent carries a model at all — see
    test_agent_files_default_to_per_call_models."""
    _home(tmp_path, monkeypatch, {"acme/strong-one#high": _fail(),
                                  "freeco/tiny-free#low": _ok()})
    (tmp_path / "home" / "harness.jsonc").write_text(
        json.dumps({"models": {"pin_agents": True}}))
    from harness import agents as A
    files = A.expected_agent_files()
    content = files[A.agents_dir() / "harness-security-auditor.md"]
    assert "model:" not in content
    assert "tiny-free" not in content
    # the cheap role still gets its pin — only the tier rule changed
    explore = files[A.agents_dir() / "harness-explore.md"]
    assert 'model: "freeco/tiny-free#low"' in explore


def test_agent_files_default_to_per_call_models(tmp_path, monkeypatch):
    """Default (models.pin_agents off) ships NO pins: subagent model choice
    belongs to the orchestrator per dispatch — models_route before each wave,
    then `model: <pin>` on the task call itself. With a syncable pool present,
    every subagent renders no `model:` line and no tier badge; primary agents
    never carried one."""
    _home(tmp_path, monkeypatch, {"acme/strong-one#high": _fail(),
                                  "freeco/tiny-free#low": _ok()})
    from harness import agents as A
    files = A.expected_agent_files()
    for role in ("explore", "code-reviewer", "deep", "test-engineer",
                 "security-auditor"):
        text = files[A.agents_dir() / f"harness-{role}.md"]
        assert "model:" not in text, role
        assert "[cheap" not in text and "[mid" not in text and "[strong" not in text, role
    head = files[A.agents_dir() / "harness-orchestrator.md"].split("---", 2)[1]
    assert "model:" not in head


def test_tier_pattern_order_puts_strong_free_models_first(tmp_path, monkeypatch):
    """Specific curated rows must precede the `*-free` catch-all (first match
    wins, ordering is load-bearing): a strong free model stays strong, and
    beam-free is a MID worker, never the free-cheap universal donor."""
    _home(tmp_path, monkeypatch)
    from harness import models as M
    assert M.classify("nova/nemotron-3-ultra")["tier"] == "strong"
    assert M.classify("nova/nemotron-3-super")["tier"] == "mid"
    assert M.classify("nova/nemotron-lightning-1")["tier"] == "cheap"
    assert M.classify("nova/inkling-small-2")["tier"] == "mid"
    assert M.classify("nova/inkling")["tier"] == "strong"
    assert M.classify("core/flint-40b")["tier"] == "mid"
    assert M.classify("core/flint-v1.0")["tier"] == "mid"
    reg = {"openrouter": {"models": {"beam-free": _meta(0, 0)}}}
    beam = M.classify("openrouter/beam-free", reg)
    assert (beam["tier"], beam["free"]) == ("mid", True)
    ultra = M.classify("nova/nemotron-3-ultra")
    assert {"reasoning", "code"} <= set(ultra["strengths"])
    assert M.classify("zzz/random-free")["tier"] == "cheap"  # catch-all intact


def test_model_health_reports_a_dead_provider(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, {
        "__prov__:acme": {"ok": False, "reason": "provider.auth: bad token",
                           "checked_at": int(time.time())},
    })
    from harness import models as M
    assert M.model_health("acme/strong-one") == ("dead", "provider.auth: bad token")
    assert M.model_health("freeco/tiny-free") == ("unchecked", "")
    # routing never sends a role to a model behind the dead provider
    assert all(not r["model"].startswith("acme/")
               for r in M.route(CFG))


# ---------------------------------------------------------------------------
# Recent opencode state
# ---------------------------------------------------------------------------

def _recent_home(tmp_path, monkeypatch, recent_models):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HARNESS_HOME", str(home))
    state = tmp_path / "state"
    (state / "opencode").mkdir(parents=True)
    (state / "opencode" / "model.json").write_text(__import__("json").dumps({
        "recent": [{"providerID": m.split("/")[0], "modelID": m.split("/", 1)[1]}
                   for m in recent_models],
        "favorite": [],
        "variant": {},
    }))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return home


def test_available_includes_recent_only_model(tmp_path, monkeypatch):
    _recent_home(tmp_path, monkeypatch, ["recentco/only-model"])
    from harness import models as M
    assert "recentco/only-model" in M.available()


def test_sync_writes_recent_model(tmp_path, monkeypatch):
    _recent_home(tmp_path, monkeypatch, ["recentco/only-model"])
    from harness import models as M
    monkeypatch.setattr(M, "_opencode_models", lambda timeout=60: [])
    res = M.sync(with_registry=False)
    assert res["models"] >= 1
    import json
    data = json.loads((tmp_path / "home" / "models-available.json").read_text())
    assert "recentco/only-model" in data["models"]


def test_classify_nemotron_free_is_strong_and_free(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from harness import models as M
    c = M.classify("kilo/nvidia/nemotron-3-ultra-550b-a55b:free")
    assert (c["tier"], c["free"]) == ("strong", True)


def test_new_strong_patterns_classify_strong(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from harness import models as M
    assert M.classify("opencode/space-bunny-free")["tier"] == "strong"
    assert M.classify("opencode/ll-alpha-free")["tier"] == "strong"
    assert M.classify("opencode/muse-spark-1.3-contributor-free")["tier"] == "strong"


# ---------------------------------------------------------------------------
# Model-ops: limits, resilient sync, status, quota-aware routing
# ---------------------------------------------------------------------------

def test_provider_limits_are_shipped_and_loaded():
    from harness import models as M
    kilo = M.limits_for("kilo/nvidia/some-model")
    assert kilo["known"] is True and kilo["free"]["rph"] == 200
    assert "kilo.ai" in kilo["source"]
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    assert "data/provider-limits.json" in (root / "pyproject.toml").read_text()


def test_sync_failure_keeps_the_last_good_list(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    before = json.loads((home / "models-available.json").read_text())
    from harness import models as M
    monkeypatch.setattr(M, "_opencode_models", lambda timeout=60: [])
    monkeypatch.setattr(M, "_recent_models", lambda: [])
    out = M.sync(with_registry=False)
    data = json.loads((home / "models-available.json").read_text())
    assert out["models"] == 2 and out["error"]
    assert data["models"] == before["models"]
    assert data["refreshed_at"] == before["refreshed_at"]
    assert data["error"] and data["attempted_at"] >= before["refreshed_at"]


def test_sync_failure_on_fresh_install_writes_an_empty_cache(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "models-available.json").unlink()
    from harness import models as M
    monkeypatch.setattr(M, "_opencode_models", lambda timeout=60: [])
    monkeypatch.setattr(M, "_recent_models", lambda: [])
    out = M.sync(with_registry=False)
    data = json.loads((home / "models-available.json").read_text())
    assert out["models"] == 0 and data["models"] == [] and data["error"]


def test_sync_auto_skips_fresh_and_runs_stale(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    from harness import models as M
    calls = []
    monkeypatch.setattr(M, "sync", lambda **kw: calls.append(kw) or {"models": 2})
    fresh = M.sync_auto(CFG)
    assert fresh == {"ran": False, "reason": "fresh", "sync": {}}
    assert calls == []
    path = home / "models-available.json"
    data = json.loads(path.read_text())
    data["refreshed_at"] = int(time.time()) - 10 ** 6
    path.write_text(json.dumps(data))
    stale = M.sync_auto(CFG)
    assert stale["ran"] is True and stale["reason"] == "stale"
    assert calls and calls[0].get("timeout") == 30


def test_sync_auto_retries_after_an_error(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    path = home / "models-available.json"
    data = json.loads(path.read_text())
    data["error"] = "opencode models returned no models"
    path.write_text(json.dumps(data))
    from harness import models as M
    monkeypatch.setattr(M, "sync", lambda **kw: {"models": 2})
    out = M.sync_auto(CFG)
    assert out["ran"] is True and out["reason"] == "error"


def test_models_status_shape_warnings_and_recent_health(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch, {
        "acme/strong-one#high": _fail("provider.no-route: Model unavailable"),
        "freeco/tiny-free#low": _ok(),
    })
    state = tmp_path / "state"
    (state / "opencode").mkdir(parents=True)
    (state / "opencode" / "model.json").write_text(json.dumps({
        "recent": [{"providerID": "acme", "modelID": "strong-one"},
                   {"providerID": "freeco", "modelID": "tiny-free"}],
        "favorite": [], "variant": {"acme/strong-one": "high"},
    }))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    from harness import models as M
    st = M.models_status(CFG)
    assert st["schema"] == 1
    assert set(st) == {"schema", "now", "sync", "counts", "recent", "providers",
                       "warnings"}
    assert st["counts"]["available"] == 2
    assert st["counts"]["strong_free_verified"] == 0
    assert [r["model"] for r in st["recent"]][0] == "acme/strong-one"
    assert st["recent"][0]["health"] == "failed"
    assert any("recent model failed" in w for w in st["warnings"])
    assert home.exists()


def test_no_route_does_not_retire_the_whole_provider():
    from harness import models as M
    ledger = {}
    M._mark_provider(ledger, "acme/strong-one#high",
                     {"ok": False, "error": "provider.no-route: Model unavailable"})
    assert "__prov__:acme" not in ledger
    M._mark_provider(ledger, "acme/strong-one#high",
                     {"ok": False, "error": "provider.quota: no credits"})
    assert "__prov__:acme" in ledger


def test_check_roles_probes_the_wanted_tier_even_when_a_mid_is_verified(
        tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, {"freeco/tiny-free#low": _ok()})
    from harness import models as M
    calls = []

    def fake_probe(pin, cwd, timeout_s):
        calls.append(pin)
        return {"ok": True, "reason": "", "cost": 0.0, "reply": "OK"}

    monkeypatch.setattr(M, "_probe", fake_probe)
    M.check_roles(CFG, ["deep"], tmp_path)
    assert calls and calls[0] == "acme/strong-one#high"


def test_exhausted_provider_is_avoided_when_another_has_room(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    now = int(time.time())
    (home / "models-available.json").write_text(json.dumps(
        {"refreshed_at": now,
         "models": ["freeco/tiny-free", "acme/strong-one", "zeta/strong-two"]}))
    dev = json.loads((home / "models-dev.json").read_text())
    dev["providers"]["zeta"] = {"models": {"strong-two": _meta(5, 15)}}
    (home / "models-dev.json").write_text(json.dumps(dev))
    (home / "usage.jsonl").write_text(json.dumps(
        {"id": "u1", "at": int(time.time() * 1000), "provider": "acme"}) + "\n")
    from harness import usage as U
    from harness import models as M
    limits = {"acme": {"known": True, "free": {"rpm": 1, "rph": None, "rpd": None},
                       "note": "spent", "source": "test"},
              "zeta": {"known": True, "free": {"rpm": None, "rph": None, "rpd": None},
                       "note": "open", "source": "test"}}
    monkeypatch.setattr(U, "limits_for", lambda p: limits.get(
        p, {"known": False, "note": "no published limits"}))
    deep = {r["role"]: r for r in M.route(CFG)}["deep"]
    assert deep["pin"] == "zeta/strong-two#high"
    assert deep["provider"] == "zeta" and isinstance(deep["quota"], dict)


def test_all_exhausted_keeps_one_candidate_and_says_so(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    now_ms = int(time.time() * 1000)
    (home / "usage.jsonl").write_text("\n".join(json.dumps(
        {"id": f"u{i}", "at": now_ms, "provider": p})
        for i, p in enumerate(("acme", "freeco"))) + "\n")
    from harness import usage as U
    from harness import models as M
    monkeypatch.setattr(U, "limits_for", lambda p: {
        "known": True, "free": {"rpm": 1, "rph": None, "rpd": None},
        "note": "spent", "source": "test"})
    deep = {r["role"]: r for r in M.route(CFG)}["deep"]
    assert deep["pin"]
    assert "exhausted" in deep["note"]


# ---------------------------------------------------------------------------
# Council profiles: release date, deprecation, newest-wins tiebreak
# ---------------------------------------------------------------------------

def test_classify_exposes_release_and_deprecation(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    dev = json.loads((home / "models-dev.json").read_text())
    meta = _meta(10, 30)
    meta.update({"released": "2026-08-01", "status": "deprecated",
                 "knowledge": "2025-12-01"})
    dev["providers"]["acme"]["models"]["strong-one"] = meta
    (home / "models-dev.json").write_text(json.dumps(dev))
    from harness import models as M
    row = M.classify("acme/strong-one")
    assert row["released"] == "2026-08-01"
    assert row["knowledge"] == "2025-12-01"
    assert row["deprecated"] is True
    assert "acme/strong-one" not in {r["model"] for r in M._pool()}


def test_newer_release_wins_an_otherwise_tied_route(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    now = int(time.time())
    (home / "models-available.json").write_text(json.dumps(
        {"refreshed_at": now,
         "models": ["freeco/tiny-free", "acme/strong-one", "acme/strong-two"]}))
    older = _meta(10, 30)
    older["released"] = "2025-01-01"
    newer = _meta(10, 30)
    newer["released"] = "2026-09-01"
    dev = json.loads((home / "models-dev.json").read_text())
    dev["providers"]["acme"]["models"]["strong-one"] = older
    dev["providers"]["acme"]["models"]["strong-two"] = newer
    (home / "models-dev.json").write_text(json.dumps(dev))
    from harness import models as M
    deep = {r["role"]: r for r in M.route(CFG)}["deep"]
    assert deep["pin"] == "acme/strong-two#high"
    assert deep["released"] == "2026-09-01"


def test_classify_merges_name_evidence_into_curated_strengths(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    dev = json.loads((home / "models-dev.json").read_text())
    dev["providers"]["acme"]["models"]["nemotron-3.5-super-vl-free"] = _meta(0, 0)
    (home / "models-dev.json").write_text(json.dumps(dev))
    from harness import models as M
    row = M.classify("acme/nemotron-3.5-super-vl-free")
    assert row["tier"] == "mid" and row["free"] is True
    assert row["strengths"][:2] == ["reasoning", "code"], \
        "curated order still drives the substitution rule"
    assert "vision" in row["strengths"]


def test_a_stale_failed_probe_disqualifies_a_new_panel_pick(tmp_path, monkeypatch):
    from harness import models as M
    old = int(time.time()) - 4 * 3600
    assert M._latest_probe_failed("acme/x", {
        "acme/x#high": {"ok": False, "checked_at": old}}) is True
    assert M._latest_probe_failed("acme/x", {
        "acme/x#high": {"ok": True, "checked_at": old}}) is False
    assert M._latest_probe_failed("acme/x", {}) is False
