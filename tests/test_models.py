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


def test_role_filter_keeps_the_verified_downgrade(tmp_path, monkeypatch):
    """`--roles deep` must not report a different model than the full route.

    Regression: the downgrade was computed after filtering, so a lone strong
    role saw no verified row besides itself and kept a paid model whose probe
    had already failed — while the unfiltered route used the verified free one.
    """
    _home(tmp_path, monkeypatch, {"acme/strong-one#high": _fail(),
                                  "freeco/tiny-free#low": _ok()})
    from harness import models as M
    full = {r["role"]: r for r in M.route(CFG)}
    one = M.route(CFG, roles=["deep"])[0]
    assert full["deep"]["pin"] == "freeco/tiny-free#low"
    assert one["pin"] == full["deep"]["pin"]
    assert one["health"] == "ok"


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
    # the probe is what route reports: on a paid plan with no credits, the
    # verified free model is the dispatch target, not the strong one
    row = M.route(CFG, roles=["deep"])[0]
    assert row["pin"] == "freeco/tiny-free#high" and row["health"] == "ok"
    assert row["free"] is True
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
