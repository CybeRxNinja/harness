"""The model council: profiles, votes, and the survival rule (two straight losses)."""
import json
import os
import time

os.environ["HARNESS_MOCK"] = "1"


def _meta(cost_in=0.0, cost_out=0.0, released="", variants=("low", "high")):
    return {"cost": {"input": cost_in, "output": cost_out}, "limit": 128000,
            "reasoning": True, "tool_call": True, "released": released,
            "release_date": released,
            "reasoning_options": [{"type": "effort", "values": list(variants)}]}


def _home(tmp_path, monkeypatch, health=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HARNESS_HOME", str(home))
    cfgdir = tmp_path / "cfg"
    (cfgdir / "opencode").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "no-state"))
    now = int(time.time())
    rows = {
        "supernova/reason-9": _meta(12, 30, "2026-08-01"),
        "midlane/reason-mid": _meta(2, 6, "2026-06-01"),
        "valeska/reason-clone": _meta(12, 30, "2026-09-01"),
        "craftland/code-40": _meta(12, 30, "2026-07-01"),
        "wolftone/reason-8": _meta(12, 30, "2026-05-01"),
        "openrouter/google/lyria-9": _meta(0, 0),
    }
    (home / "models-available.json").write_text(json.dumps(
        {"refreshed_at": now, "models": list(rows)}))
    providers: dict = {}
    for model, meta in rows.items():
        prov, _, mid = model.partition("/")
        providers.setdefault(prov, {"models": {}})["models"][mid] = meta
    (home / "models-dev.json").write_text(json.dumps(
        {"refreshed_at": now, "providers": providers}))
    health = health or {}
    (home / "models-health.json").write_text(json.dumps(health))
    return home


def _cfg():
    from harness.config import DEFAULT_CONFIG
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _ok():
    return {"ok": True, "reason": "", "cost": 0.0, "checked_at": int(time.time())}


def _healthy():
    return {"supernova/reason-9": _ok(), "midlane/reason-mid#high": _ok(),
            "valeska/reason-clone#high": _ok()}


def _build(tmp_path, monkeypatch, health=None):
    _home(tmp_path, monkeypatch, health or _healthy())
    from harness import council as C
    return C.build(tmp_path, _cfg(), topic="feature-x")


def test_build_profiles_the_roster_with_strengths_and_release(tmp_path, monkeypatch):
    doc, path = _build(tmp_path, monkeypatch)
    assert path.exists() and json.loads(path.read_text()) == doc
    assert doc["topic"] == "feature-x" and doc["round"] == 0
    seats = doc["seats"]
    assert [s["role"] for s in seats] == ["panel-lead", "panel-peer", "panel-skeptic"]
    assert all("strengths" in s and s["strengths"] for s in seats)
    assert all(s["health"] == "ok" for s in seats)
    assert len({s["upstream"] for s in seats}) == 3
    lead = seats[0]
    assert lead["pin"] == "supernova/reason-9"
    assert lead["released"] == "2026-08-01"
    assert set(lead) >= {"role", "pin", "upstream", "health", "tier", "free",
                         "context", "strengths", "released", "knowledge",
                         "wins", "losses", "streak", "score", "last_verdict",
                         "fallback_pin"}


def test_one_loss_does_not_retire_and_a_win_resets_the_streak(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    C.build(tmp_path, cfg, topic="x")
    doc, _ = C.record(tmp_path, cfg, topic="x", votes=["panel-lead=loss"])
    lead = next(s for s in doc["seats"] if s["role"] == "panel-lead")
    assert (lead["losses"], lead["streak"]) == (1, -1) and not doc["retired"]
    doc, _ = C.record(tmp_path, cfg, topic="x", votes=["panel-lead=win"])
    lead = next(s for s in doc["seats"] if s["role"] == "panel-lead")
    assert (lead["wins"], lead["streak"], lead["score"]) == (1, 1, 0)
    doc, _ = C.record(tmp_path, cfg, topic="x", votes=["panel-lead=loss"])
    lead = next(s for s in doc["seats"] if s["role"] == "panel-lead")
    assert lead["losses"] == 2 and lead["streak"] == -1
    assert not doc["retired"], "a win between the losses breaks the streak"


def test_two_straight_losses_retire_and_replace_on_another_upstream(
        tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="x")
    old = first["seats"][0]
    C.record(tmp_path, cfg, topic="x", votes=[f"{old['role']}=loss:weak"])
    doc, _ = C.record(tmp_path, cfg, topic="x", votes=[f"{old['role']}=loss:still weak"])
    lead = next(s for s in doc["seats"] if s["role"] == old["role"])
    assert doc["retired"] and doc["retired"][0]["pin"] == old["pin"]
    assert "lost 2 in a row" in doc["retired"][0]["reason"]
    assert lead["pin"] != old["pin"]
    assert lead["upstream"] != old["upstream"]
    assert lead["wins"] == lead["losses"] == 0, "a replacement starts clean"
    swap = [s for s in doc["model_replaced"] if s["role"] == old["role"]][-1]
    assert swap["old"] == old["pin"] and swap["new"] == lead["pin"]
    assert "council vote" in swap["reason"]
    assert doc["round"] == 2


def test_a_retired_pin_is_never_reseated(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="x")
    old = first["seats"][0]
    C.record(tmp_path, cfg, topic="x", votes=[f"{old['role']}=loss"])
    doc, _ = C.record(tmp_path, cfg, topic="x", votes=[f"{old['role']}=loss"])
    for _ in range(3):
        doc, _ = C.build(tmp_path, cfg, topic="x")
        assert all(s["pin"] != old["pin"] for s in doc["seats"]), "retired pin came back"
    # direct exclusion also holds without any vote history
    from harness import models as M
    pin = doc["seats"][0]["pin"]
    excluded = M.panel_pool(cfg, size=3, exclude={"panel-lead": {pin}})
    assert all(s["pin"] != pin for s in excluded["panel"] if s["role"] == "panel-lead")


def test_history_survives_an_unrelated_rebuild(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    doc, _ = C.build(tmp_path, cfg, topic="x")
    lead = doc["seats"][0]
    C.record(tmp_path, cfg, topic="x", votes=[f"{lead['role']}=win"])
    again, _ = C.build(tmp_path, cfg, topic="x")
    seat = next(s for s in again["seats"] if s["role"] == lead["role"])
    assert seat["pin"] == lead["pin"] and seat["wins"] == 1 and seat["streak"] == 1


def test_three_losses_without_a_win_also_retire(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    doc, _ = C.build(tmp_path, cfg, topic="x")
    role = doc["seats"][0]["role"]
    for _ in range(2):
        doc, _ = C.record(tmp_path, cfg, topic="x", votes=[f"{role}=loss"])
    # streak already retired it at 2; prove the alternate rule directly
    from harness import council as C2
    path = C2.path(tmp_path, "x")
    data = json.loads(path.read_text())
    seat = next(s for s in data["seats"] if s["role"] == role)
    seat.update({"losses": 3, "wins": 0, "streak": 0})
    data["retired"] = [r for r in data["retired"] if r["role"] != role]
    path.write_text(json.dumps(data))
    doc, _ = C.record(tmp_path, cfg, topic="x", votes=[f"{role}=loss"])
    assert any(r["role"] == role and "no win" in r["reason"]
               for r in doc["retired"])


def test_bad_votes_warn_and_change_nothing(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="x")
    doc, _ = C.record(tmp_path, cfg, topic="x",
                      votes=["not-a-vote", "panel-lead=maybe", "nobody=win"])
    assert doc["round"] == first["round"]
    assert len(doc["warnings"]) == 3
    assert [s["pin"] for s in doc["seats"]] == [s["pin"] for s in first["seats"]]


def test_votes_are_attributed_and_self_votes_are_refused(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    doc, _ = C.build(tmp_path, cfg, topic="x")
    a, b = [s["role"] for s in doc["seats"][:2]]
    got, _ = C.record(tmp_path, cfg, topic="x",
                      votes=[f"{a}>{a}=win", f"nobody>{a}=win"])
    assert got["round"] == 0, "a refused ballot must not advance the round"
    assert not any(s["wins"] for s in got["seats"])
    assert any("own answer" in w for w in got["warnings"])
    assert any("not a seat" in w for w in got["warnings"])
    got, _ = C.record(tmp_path, cfg, topic="x", votes=[f"{a}>{b}=win"])
    vote = got["rounds"][-1]["votes"][0]
    assert (vote["voter"], vote["role"], vote["verdict"]) == (a, b, "win")
    target = next(s for s in got["seats"] if s["role"] == b)
    assert target["wins"] == 1 and target["streak"] == 1


def test_mutual_wins_in_two_rounds_retire_the_lower_scoring_colluder(
        tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="x")
    a, b, c = [s["role"] for s in first["seats"]]
    # a and b award each other a win while c backs b: after two rounds their
    # scores diverge, so the lower scorer is the one the bloc loses.
    votes = [f"{a}>{b}=win", f"{b}>{a}=win", f"{c}>{b}=win"]
    C.record(tmp_path, cfg, topic="x", votes=votes)
    doc, _ = C.record(tmp_path, cfg, topic="x", votes=votes)
    entry = next(e for e in doc["collusion"] if set(e["pair"]) == {a, b})
    assert len(entry["rounds"]) == 2
    assert entry["retired"] == first["seats"][0]["pin"], "the lower scorer is retired"
    assert any(r["role"] == a and "collusion with" in r["reason"]
               for r in doc["retired"])
    seat = next(s for s in doc["seats"] if s["role"] == a)
    assert seat["pin"] != first["seats"][0]["pin"]
    assert seat["wins"] == seat["losses"] == 0, "the replacement starts clean"
    swap = [s for s in doc["model_replaced"] if s["role"] == a][-1]
    assert swap["reason"] == "retired by collusion"
    # the verdict is durable: a later rebuild keeps the evidence
    again, _ = C.build(tmp_path, cfg, topic="x")
    assert any(set(e["pair"]) == {a, b} for e in again["collusion"])
    assert all(s["pin"] != first["seats"][0]["pin"]
               for s in again["seats"] if s["role"] == a)


def test_phase_roles_narrow_the_round_without_dropping_or_churning_seats(
        tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="phase")
    lead, peer = first["seats"][0]["pin"], first["seats"][1]["pin"]
    phase, _ = C.build(tmp_path, cfg, topic="phase",
                       roles=["panel-skeptic", "panel-edge"])
    roles = {s["role"]: s for s in phase["seats"]}
    assert set(roles) == {"panel-lead", "panel-peer", "panel-skeptic", "panel-edge"}
    assert roles["panel-lead"]["pin"] == lead
    assert roles["panel-peer"]["pin"] == peer
    assert len({s["upstream"] for s in phase["seats"]}) == len(phase["seats"]), \
        "a phase seat must not claim a carried seat's upstream"
    again, _ = C.build(tmp_path, cfg, topic="phase",
                       roles=["panel-skeptic", "panel-edge"])
    assert {s["role"]: s["pin"] for s in again["seats"]} == \
        {s["role"]: s["pin"] for s in phase["seats"]}


def test_collusion_evidence_does_not_retire_a_replacement(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="x")
    a, b = [s["role"] for s in first["seats"][:2]]
    votes = [f"{a}>{b}=win", f"{b}>{a}=win"]
    C.record(tmp_path, cfg, topic="x", votes=votes)
    doc, _ = C.record(tmp_path, cfg, topic="x", votes=votes)
    replacement = next(s for s in doc["seats"] if s["role"] == a)
    assert replacement["pin"] != first["seats"][0]["pin"]
    retired_before = len(doc["retired"])
    # the replacement's first mutual round is new evidence, not a 3rd strike
    doc, _ = C.record(tmp_path, cfg, topic="x", votes=votes)
    assert len(doc["retired"]) == retired_before
    assert len(doc["seats"]) == 3
    assert next(s for s in doc["seats"] if s["role"] == a)["pin"] == replacement["pin"]


def test_unattributed_votes_never_trigger_collusion(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="x")
    a, b = [s["role"] for s in first["seats"][:2]]
    for _ in range(2):
        doc, _ = C.record(tmp_path, cfg, topic="x",
                          votes=[f"{a}=win", f"{b}=win"])
    assert not doc.get("collusion")
    assert not doc["retired"]


def test_cli_council_builds_and_records(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch, _healthy())
    from harness.cli import main
    assert main(["--root", str(tmp_path), "models", "council", "--topic", "cli",
                 "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    role = first["seats"][0]["role"]
    assert main(["--root", str(tmp_path), "models", "council", "--topic", "cli",
                 "--vote", f"{role}=loss", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["round"] == 1 and doc["seats"][0]["losses"] == 1


def test_round_verdict_names_the_seat_the_ballots_backed(tmp_path, monkeypatch):
    """A round is a decision: the most-voted seat and its pin are recorded,
    and a rebuild cannot forget what the round decided."""
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="verdict")
    a, b, c = [s["role"] for s in first["seats"]]
    doc, _ = C.record(tmp_path, cfg, topic="verdict",
                      votes=[f"{a}>{b}=win", f"{c}>{b}=win", f"{b}>{a}=loss"])
    v = doc["verdict"]
    assert (v["role"], v["round"], v["wins"]) == (b, 1, 2)
    assert (v["ballots"], v["voters"]) == (3, 3)
    assert v["pin"] == next(s["pin"] for s in first["seats"] if s["role"] == b)
    again, _ = C.build(tmp_path, cfg, topic="verdict")
    assert again["verdict"]["role"] == b and again["verdict"]["pin"] == v["pin"]


def test_a_single_ballot_round_warns_it_is_one_voice(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="one")
    a, b = [s["role"] for s in first["seats"][:2]]
    doc, _ = C.record(tmp_path, cfg, topic="one", votes=[f"{a}>{b}=win"])
    assert doc["verdict"]["role"] == b and doc["verdict"]["voters"] == 1
    assert any("one voice" in w for w in doc["warnings"])


def test_votes_feed_the_cross_topic_pin_standing(tmp_path, monkeypatch):
    """The topic file is per project; a pin's record follows the model, so
    routing can see it in a later session or another topic."""
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    from harness import models as M
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="stand")
    lead, peer, skeptic = [s["role"] for s in first["seats"]]
    C.record(tmp_path, cfg, topic="stand",
             votes=[f"{peer}>{lead}=loss", f"{skeptic}>{lead}=loss"])
    book = M.standing()
    pin = first["seats"][0]["pin"]
    assert book[pin]["losses"] == 2
    assert book[pin]["wins"] == 0


# ---------------------------------------------------------------------------
# Blind judge pass: each seat scores the OTHER seats' anonymized answers
# ---------------------------------------------------------------------------

def _fake_judge(captured, fail_pin=""):
    def ask(pin, prompt, cwd, timeout_s):
        captured.append(prompt)
        if fail_pin and pin == fail_pin:
            return {"ok": False, "reason": "provider.quota: no credits",
                    "cost": 0.0, "text": "", "error": "provider.quota"}
        sections = {}
        for chunk in prompt.split("### ")[1:]:
            label, _, body = chunk.partition("\n")
            sections[label.strip()] = body.strip()
        scores = {label: (9 if "GOOD" in body else 1 if "BAD" in body else 5)
                  for label, body in sections.items()}
        return {"ok": True, "reason": "", "cost": 0.0,
                "text": json.dumps(scores), "error": ""}
    return ask


def _answers():
    return [{"role": "panel-lead", "text": "BAD plan"},
            {"role": "panel-peer", "text": "GOOD plan"},
            {"role": "panel-skeptic", "text": "MEH plan"}]


def test_judge_scores_anonymized_answers_and_records_ballots(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    from harness import models as M
    cfg = _cfg()
    C.build(tmp_path, cfg, topic="judge")
    captured = []
    monkeypatch.setattr(M, "ask_model", _fake_judge(captured))
    doc, _ = C.judge(tmp_path, cfg, topic="judge", answers=_answers())
    assert doc["round"] == 1, "a judge pass is a recorded round"
    assert doc["verdict"]["role"] == "panel-peer" and doc["verdict"]["wins"] == 2
    votes = doc["rounds"][-1]["votes"]
    assert votes and all(v["source"] == "judge" for v in votes)
    assert all(v["voter"] != v["role"] for v in votes), "no seat judges itself"
    assert not any(v["role"] == "panel-lead" for v in votes), \
        "the BAD answer was nobody's best"
    # Blind: prompts arrive in seat order and each omits that judge's own
    # answer, so no judge can see (or score) its own text.
    assert len(captured) == 3
    assert "BAD plan" not in captured[0] and "GOOD plan" in captured[0]
    assert "GOOD plan" not in captured[1] and "BAD plan" in captured[1]
    assert "MEH plan" not in captured[2] and "BAD plan" in captured[2]


def test_judge_needs_two_answers_and_spends_no_request(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    from harness import models as M
    cfg = _cfg()
    C.build(tmp_path, cfg, topic="thin")
    monkeypatch.setattr(M, "ask_model", lambda *a: (_ for _ in ()).throw(
        AssertionError("called without enough answers")))
    doc, _ = C.judge(tmp_path, cfg, topic="thin",
                     answers=[{"role": "panel-lead", "text": "only one"}])
    assert doc["round"] == 0
    assert any("at least two" in w for w in doc["warnings"])


def test_a_failed_judge_warns_instead_of_inventing_a_vote(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import council as C
    from harness import models as M
    cfg = _cfg()
    first, _ = C.build(tmp_path, cfg, topic="fail")
    lead_pin = first["seats"][0]["pin"]
    monkeypatch.setattr(M, "ask_model", _fake_judge([], fail_pin=lead_pin))
    doc, _ = C.judge(tmp_path, cfg, topic="fail", answers=_answers())
    assert any("judge panel-lead failed" in w for w in doc["warnings"])
    assert doc["round"] == 1, "the other judges still decide the round"
    votes = doc["rounds"][-1]["votes"]
    assert all(v["voter"] != "panel-lead" for v in votes)


def test_judge_ballots_cannot_prove_collusion(tmp_path, monkeypatch):
    """Independent blind scores are not a voting pact: mutual top picks in two
    rounds must not retire a seat as colluders."""
    from harness import council as C
    rounds = [
        {"round": n, "votes": [
            {"voter": "panel-lead", "role": "panel-peer", "verdict": "win",
             "source": "judge"},
            {"voter": "panel-peer", "role": "panel-lead", "verdict": "win",
             "source": "judge"},
        ]} for n in (1, 2)
    ]
    assert C.detect_collusion(rounds) == []
    manual = [
        {"round": n, "votes": [
            {"voter": "panel-lead", "role": "panel-peer", "verdict": "win"},
            {"voter": "panel-peer", "role": "panel-lead", "verdict": "win"},
        ]} for n in (1, 2)
    ]
    assert C.detect_collusion(manual), "manual mutual wins are still a bloc"


def test_cli_judges_answers_from_a_file(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch, _healthy())
    from harness import models as M
    from harness.cli import main
    monkeypatch.setattr(M, "ask_model", _fake_judge([]))
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(_answers()))
    args = ["--root", str(tmp_path), "models", "council", "--topic", "cli-judge",
            "--answers", str(path), "--json"]
    assert main(args) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["round"] == 1 and doc["verdict"]["role"] == "panel-peer"
