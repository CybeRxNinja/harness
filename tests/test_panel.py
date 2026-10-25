"""TDD target for the topic-scoped model panel (spec:
.opencode/harness/tmp/panel-feature-spec.md).

T1  the panel role ROUTES alone (config change, before any panel code) make
    route(cfg, roles=['panel-lead']) resolve to a non-empty row.
T2  panel_pool() dedupes gateway ids that point at one upstream: three ids,
    one panelist. Upstream table aliases collapse too; a multi-segment miss
    must NOT be deduped.
T3  with one of the three pins marked failed, the rebuilt bundle records the
    swap and the replacement satisfies the capability rule (tier within 1 and
    first-2 strengths intersecting).
T4  untouched roles keep byte-identical route()/check_roles() rows after the
    panel additions.
"""
import json
import os
import time

import pytest

os.environ["HARNESS_MOCK"] = "1"

DEFAULT_PANEL_ROLES = ("panel-lead", "panel-peer", "panel-skeptic", "panel-edge")
UNTOUCHED_ROLES = ("explore", "librarian", "quick", "test-engineer",
                   "code-reviewer", "security-auditor", "plan-consultant",
                   "plan-reviewer", "deep")


def _meta(cost_in=0.0, cost_out=0.0, variants=("low", "medium", "high")):
    """One model's models.dev metadata: cost decides tier, options carry variants."""
    return {"cost": {"input": cost_in, "output": cost_out}, "limit": 128000,
            "reasoning": True, "tool_call": True,
            "reasoning_options": [{"type": "effort", "values": list(variants)}]}


def _registry(meta_rows, ctx: dict | None = None):
    return {"refreshed_at": ctx and ctx.get("refreshed_at") or int(time.time()),
            "providers": meta_rows}


def _home(tmp_path, monkeypatch, models, costs, health=None):
    """A machine that synced the named gateway ids; costs map id -> (in, out)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HARNESS_HOME", str(home))
    cfgdir = tmp_path / "cfg"
    (cfgdir / "opencode").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-state"))
    now = int(time.time())
    (home / "models-available.json").write_text(json.dumps(
        {"refreshed_at": now, "models": models}))
    providers: dict = {}
    for m in models:
        prov, _, mid = str(m).partition("/")
        providers.setdefault(prov, {"models": {}})["models"][mid] = _meta(*costs[m])
    (home / "models-dev.json").write_text(json.dumps(
        {"refreshed_at": now, "providers": providers}))
    if health is not None:
        (home / "models-health.json").write_text(json.dumps(health))
    return home


def _probe_mock(calls, fail: str = ""):
    def fake_probe(pin, cwd, timeout):
        calls.append(pin)
        if fail and str(pin).partition("#")[0].startswith(fail):
            # like _probe(): a provider-level failure retires the whole provider
            return {"ok": False, "reason": "provider.quota: no credits",
                    "cost": 0.0, "reply": "",
                    "error": "provider.quota: no credits"}
        return {"ok": True, "reason": "", "cost": 0.0, "reply": "OK", "error": ""}
    return fake_probe


def _dead(fail: str = "supernova/reason-9"):
    """A ledger as _probe leaves it: the pin AND its provider-level row."""
    return {fail: _fail(), f"__prov__:{fail.partition('/')[0]}": _fail()}


def _cfg():
    from harness.config import DEFAULT_CONFIG
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _ok(checked=True):
    return {"ok": True, "reason": "", "cost": 0.0,
            "checked_at": int(time.time()) if checked else 0}


def _fail(reason="provider.quota: no credits"):
    return {"ok": False, "reason": reason, "cost": 0.0,
            "checked_at": int(time.time())}


# ---------------------------------------------------------------------------
# T1 — the config change alone
# ---------------------------------------------------------------------------

def test_t1_panel_routes_make_panel_lead_resolvable(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch,
          ["supernova/reason-9", "midlane/reason-mid", "craftland/code-40"],
          {"supernova/reason-9": (12, 30), "midlane/reason-mid": (2, 6),
           "craftland/code-40": (12, 30)})
    from harness import models as M
    cfg = _cfg()
    # the defaults carry the panel rows and the fresh-sync window
    assert cfg["models"]["sync_ttl_s"] == 21600
    for role, tier in (("panel-lead", "strong"), ("panel-peer", "mid"),
                       ("panel-skeptic", "strong"), ("panel-edge", "cheap")):
        assert role in cfg["models"]["routes"], f"missing route for {role}"
        assert cfg["models"]["routes"][role]["tier"] == tier
    # ...and that alone (no panel module, no new code path) resolves the lead
    rows = M.route(cfg, roles=["panel-lead"])
    assert len(rows) == 1
    assert rows[0]["pin"], "panel-lead must resolve to a non-empty pin"


def test_config_has_no_top_level_panel_section():
    """NO-GO: `panel` must live under models.*, never as a top-level section."""
    cfg = _cfg()
    assert "panel" not in cfg


# ---------------------------------------------------------------------------
# T2 — upstream dedupe
# ---------------------------------------------------------------------------

def test_t2_three_gateways_one_upstream_become_one_panelist(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch,
          ["acme/dusk-9", "dawn/dusk-9:free", "loten/dusk-9"],
          {"acme/dusk-9": (0, 0), "dawn/dusk-9:free": (0, 0),
           "loten/dusk-9": (0, 0)})
    from harness import models as M
    out = M.panel_pool(_cfg())
    panel = out["panel"]
    assert len(panel) == 1, "distinct upstreams only — one panelist"
    assert panel[0]["pin"].partition("#")[0] in ("acme/dusk-9", "dawn/dusk-9:free",
                                                 "loten/dusk-9")


def test_t2_upstream_table_aliases_and_multisegment_miss(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, ["acme/dusk-9"], {"acme/dusk-9": (0, 0)})
    from harness import models as M
    assert M._load_upstream_table(), "upstream table did not load"
    # hand-seeded aliases collapse across gateways...
    assert M.upstream_key("opencode/glm-5.3-flash-free") == \
        M.upstream_key("openrouter/z-ai/glm-5.3-flash-free:free") == \
        M.upstream_key("kilo/z-ai/glm-5.3-flash-free:free")
    # ...but a multi-segment miss is never deduped (under-dedupe is safe)
    a = M.upstream_key("openrouter/google/lyria-3:free")
    b = M.upstream_key("kilo/google/lyria-3:free")
    assert a != b
    assert a.startswith("openrouter/") and b.startswith("kilo/")


# ---------------------------------------------------------------------------
# T3 — drift substitution
# ---------------------------------------------------------------------------

def _panel_meta_home(tmp_path, monkeypatch, health=None):
    """Five distinct upstreams: supernova (lead) can be failed; tundra/craftland
    are the reasoning/code spares so a displaced seat can re-resolve."""
    return _home(tmp_path, monkeypatch,
                 ["supernova/reason-9", "midlane/reason-mid",
                  "valeska/reason-clone", "craftland/code-40",
                  "wolftone/reason-8", "openrouter/google/lyria-9"],
                 {"supernova/reason-9": (12, 30), "midlane/reason-mid": (2, 6),
                  "valeska/reason-clone": (12, 30), "craftland/code-40": (12, 30),
                  "wolftone/reason-8": (12, 30), "openrouter/google/lyria-9": (0, 0)},
                 health=health)


def test_t3_failed_pin_is_swapped_to_an_equally_capable_upstream(tmp_path, monkeypatch):
    calls = []
    _panel_meta_home(tmp_path, monkeypatch)
    import harness.models as M
    monkeypatch.setattr(M, "_probe", _probe_mock(calls))
    cfg = _cfg()
    prior = M.panel_pool(cfg, size=3)["panel"]
    assert [r["role"] for r in prior] == ["panel-lead", "panel-peer", "panel-skeptic"]
    lead0, peer0, skeptic0 = prior
    assert lead0["pin"] == "supernova/reason-9"
    assert peer0["pin"] == "midlane/reason-mid#high"

    # one of the three pins fails its probe mid-project
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "models-health.json").write_text(json.dumps(
        {"supernova/reason-9": _fail()}))
    out = M.panel_pool(cfg, size=3, prior={"panel": prior})
    panel = out["panel"]
    assert [r["role"] for r in panel] == ["panel-lead", "panel-peer", "panel-skeptic"]
    lead, peer, skeptic = panel
    # the failed lead is re-resolved against ITS requirement, not the frozen pin
    assert lead["pin"] == "valeska/reason-clone"
    ok, _ = M.substitution_ok(lead0["requirement"], lead0["pin"], lead["pin"])
    assert ok, "replacement must satisfy the capability rule"
    assert peer["pin"] == "midlane/reason-mid#high"  # healthy rows stay put
    # the skeptic's stored upstream was claimed by the swap and is re-resolved
    assert skeptic["pin"] != skeptic0["pin"]
    keys = [r["upstream"] for r in panel]
    assert len(set(keys)) == len(keys), "the panel stays upstream-distinct"
    swap_roles = {s["role"] for s in out["model_replaced"]}
    assert "panel-lead" in swap_roles and "failed" in out["model_replaced"][0]["reason"]


def test_t3_substitution_rule_shape(tmp_path, monkeypatch):
    """The rule itself: tier within 1 and first-2 strengths intersecting."""
    _panel_meta_home(tmp_path, monkeypatch)
    from harness import models as M
    req = {"tier": "strong", "strengths": ["reasoning", "review"]}
    # fresh panel rows carry tier + the role's wanted strengths
    out = M.panel_pool(_cfg(), size=3)
    row = out["panel"][0]
    assert row["requirement"]["tier"] == "strong"
    assert row["requirement"]["strengths"]
    # tier distance 1 with a shared strength passes...
    ok, _ = M.substitution_ok(req, "supernova/reason-9", "midlane/reason-mid")
    assert ok
    # ...tier distance 2 fails and a strengthless gap fails
    ok2, _ = M.substitution_ok({"tier": "cheap", "strengths": []},
                               "supernova/reason-9", "craftland/code-40")
    assert not ok2


# ---------------------------------------------------------------------------
# T4 — untouched roles are byte-identical
# ---------------------------------------------------------------------------

def test_t4_untouched_roles_keep_their_rows(tmp_path, monkeypatch):
    _panel_meta_home(tmp_path, monkeypatch)
    from harness import models as M
    full = _cfg()
    trimmed = _cfg()
    for role in DEFAULT_PANEL_ROLES:
        del trimmed["models"]["routes"][role]
    trimmed["models"].pop("sync_ttl_s", None)
    a = {r["role"]: r for r in M.route(trimmed)}
    b = {r["role"]: r for r in M.route(full)}
    for role in UNTOUCHED_ROLES:
        assert json.dumps(a[role], sort_keys=True) == json.dumps(b[role], sort_keys=True), \
            f"route row for {role} changed"
    for role in DEFAULT_PANEL_ROLES:
        assert role in b, f"panel role {role} must still be answered"
    # check_roles() only ever probes the roles it was asked for
    calls = []
    monkeypatch.setattr(M, "_probe", _probe_mock(calls))
    M.check_roles(full, ["deep"], tmp_path)
    assert calls and all("panel-" not in c for c in calls), \
        "panel routes must not leak into existing-role checks"
    # absolute, not just differential: a non-coding model harness already
    # excludes (model-tiers.json: *lyria*) never reaches the pool — so it can
    # never be routed, panel or otherwise
    pool_ids = {p["model"] for p in M._pool()}
    assert "openrouter/google/lyria-9" not in pool_ids
    assert all("lyria" not in r["pin"] for r in M.route(full, roles=DEFAULT_PANEL_ROLES))
    assert all("lyria" not in r["pin"] for r in M.panel_pool(full)["panel"])


# ---------------------------------------------------------------------------
# Bundle + CLI + doctor + plugin seams
# ---------------------------------------------------------------------------

def test_bundle_written_topic_scoped_under_state(tmp_path, monkeypatch, capsys):
    calls = []
    _panel_meta_home(tmp_path, monkeypatch, health=_dead())
    import harness.models as M
    monkeypatch.setattr(M, "_probe", _probe_mock(calls, fail="supernova"))
    from harness.cli import panel_bundle
    cfg = _cfg()
    bundle, path = panel_bundle(str(tmp_path), cfg, topic="Design Decisions!")
    assert path == (tmp_path / ".opencode" / "harness" / "panel" /
                    "design-decisions.json")
    assert path.exists() and json.loads(path.read_text()) == bundle
    assert set(bundle) == {"version", "topic", "project", "created_at", "git_head",
                           "panel", "model_replaced", "verdict", "decisions", "sources"}
    assert bundle["version"] == 1 and bundle["topic"] == "Design Decisions!"
    assert bundle["verdict"] is None
    assert bundle["decisions"] == [] and bundle["sources"] == []
    role = bundle["panel"][0]
    assert set(role) == {"role", "requirement", "pin", "upstream", "health",
                         "checked_at", "fallback_pin"}
    assert set(role["requirement"]) == {"tier", "strengths"}
    # a first build never invents replacement history for a dead model — it
    # simply seats the roster around it
    assert bundle["model_replaced"] == []
    assert all(str(r["pin"]).partition("#")[0] != "supernova/reason-9"
               for r in bundle["panel"])


def test_bundle_rebuild_records_the_failing_swap_and_carries_history(tmp_path,
                                                                     monkeypatch):
    calls = []
    _panel_meta_home(tmp_path, monkeypatch)
    import harness.models as M
    monkeypatch.setattr(M, "_probe", _probe_mock(calls))
    from harness.cli import panel_bundle
    cfg = _cfg()
    first, path = panel_bundle(str(tmp_path), cfg, topic="w1")
    assert first["panel"][0]["pin"] == "supernova/reason-9"
    # a wave that reads the bundle writes its verdict back into the same file
    first["verdict"] = "ship the splitter"
    first["decisions"] = [{"what": "split panel pool"}]
    path.write_text(json.dumps(first))
    # mid-project drift: the lead's probe now fails (check_roles re-records it)
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "models-health.json").write_text(json.dumps(_dead()))
    again, _ = panel_bundle(str(tmp_path), cfg, topic="w1")
    assert again["verdict"] == "ship the splitter"
    assert again["decisions"] == [{"what": "split panel pool"}]
    swap = [s for s in again["model_replaced"] if s["role"] == "panel-lead"][0]
    assert swap["old"] == "supernova/reason-9" and "failed" in swap["reason"]
    assert again["panel"][0]["pin"] == swap["new"]
    assert swap["new"].partition("#")[0] != "supernova/reason-9"


def test_cli_models_panel_prints_the_bundle(tmp_path, monkeypatch, capsys):
    calls = []
    _panel_meta_home(tmp_path, monkeypatch)
    import harness.models as M
    monkeypatch.setattr(M, "_probe", _probe_mock(calls))
    from harness.cli import main
    rc = main(["--root", str(tmp_path), "models", "panel", "--size", "2",
               "--json"])
    assert rc == 0
    bundle = json.loads(capsys.readouterr().out)
    assert [r["role"] for r in bundle["panel"]] == ["panel-lead", "panel-peer"]


def test_doctor_reports_panel_roster_and_upstream_table(tmp_path, monkeypatch):
    _panel_meta_home(tmp_path, monkeypatch)
    from harness import doctor
    out = doctor.run(tmp_path)
    line = out["checks"]["panel"]
    assert "panel-lead" in line and "upstream" in line
    assert "3/3" in line, line
    # a machine that never synced still reports the table without broken ok
    _home(tmp_path, monkeypatch, ["acme/dusk-9"], {"acme/dusk-9": (0, 0)})
    bare = doctor.run(tmp_path)
    assert "upstream" in bare["checks"]["panel"]


def test_plugin_registers_panel_route_inline_next_to_models_route():
    from pathlib import Path
    from harness.cli import _plugin_files
    text = Path(_plugin_files()[0]).read_text()
    assert text.index('"models_route"') < text.index('"panel_route"'), \
        "panel_route must be registered inline beside models_route"
    assert '"models", "panel", "--json"' in text, "shells `harness models panel --json`"
    assert "runBudget(" in text, "the panel shells out under a budgeted timeout"
    assert "panelLines(" in text, "the tool renders the bundle rows itself"
