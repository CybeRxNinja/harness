"""The per-call usage ledger: tokens/cost only, idempotent, quota-aware."""
import json
import os
import sqlite3
import time

os.environ["HARNESS_MOCK"] = "1"


def _db(tmp_path, rows):
    path = tmp_path / "opencode.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT, "
        "type TEXT, seq INTEGER, time_created INTEGER, time_updated INTEGER, "
        "data TEXT)"
    )
    for i, payload in enumerate(rows):
        data = payload if isinstance(payload, str) else json.dumps(payload)
        con.execute(
            "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
            (f"msg_{i}", "ses_test", "assistant", i,
             int(time.time() * 1000), int(time.time() * 1000), data),
        )
    con.commit()
    con.close()
    return path


def _payload(input_tokens=10, output_tokens=5, at=None, provider="acme"):
    return {
        "model": {"providerID": provider, "modelID": "m1", "variant": "high"},
        "tokens": {"input": input_tokens, "output": output_tokens,
                   "reasoning": 1, "cache": {"read": 2, "write": 3}},
        "time": {"created": at or int(time.time() * 1000)},
        "cost": 0.25,
        "agent": "deep",
        "text": "PROMPT TEXT MUST NEVER BE COPIED",
    }


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HARNESS_HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return home


def test_ingest_is_idempotent_and_keeps_only_numbers(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    db = _db(tmp_path, [_payload()])
    from harness import usage as U
    first = U.ingest(db=db, ledger=home / "usage.jsonl")
    assert first["ok"] and first["added"] == 1
    second = U.ingest(db=db, ledger=home / "usage.jsonl")
    assert second["ok"] and second["added"] == 0 and second["skipped"] == 1
    recs = U.records(home / "usage.jsonl")
    assert len(recs) == 1
    assert set(recs[0]) == {"id", "at", "session", "agent", "provider", "model",
                            "variant", "input", "output", "reasoning",
                            "cache_read", "cache_write", "cost"}
    assert "PROMPT" not in (home / "usage.jsonl").read_text()


def test_ingest_skips_malformed_and_missing_db_is_safe(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    db = _db(tmp_path, ["{not json", _payload(output_tokens=9)])
    from harness import usage as U
    out = U.ingest(db=db, ledger=home / "usage.jsonl")
    assert out["ok"] and out["added"] == 1 and out["skipped"] >= 1
    missing = U.ingest(db=tmp_path / "nope.db", ledger=home / "usage.jsonl")
    assert missing["ok"] is False and "not found" in missing["error"]


def test_ledger_file_is_private(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    db = _db(tmp_path, [_payload()])
    from harness import usage as U
    ledger = home / "usage.jsonl"
    U.ingest(db=db, ledger=ledger)
    assert (ledger.stat().st_mode & 0o777) == 0o600


def test_summary_filters_the_window_and_aggregates(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    ledger = home / "usage.jsonl"
    now = int(time.time() * 1000)
    rows = [
        {**_payload(), "id": "a", "at": now, "provider": "acme", "cost": 0.5},
        {**_payload(), "id": "b", "at": now - 48 * 3600_000, "provider": "old"},
    ]
    for r in rows:
        r.pop("tokens", None)
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    from harness import usage as U
    out = U.summary(hours=24, by="provider", ledger=ledger)
    assert [r["key"] for r in out["rows"]] == ["acme"]
    assert out["totals"]["calls"] == 1 and out["totals"]["cost"] == 0.5


def test_quota_exhausts_on_rpm_rph_rpd(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    ledger = home / "usage.jsonl"
    now = int(time.time() * 1000)
    recs = [{"id": f"r{i}", "at": now - i * 1000, "provider": "acme"}
            for i in range(3)]
    ledger.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    from harness import usage as U

    for window, count in (("rpm", 3), ("rph", 3), ("rpd", 3)):
        monkeypatch.setattr(U, "limits_for", lambda p, w=window: {
            "known": True, "free": {"rpm": None, "rph": None, "rpd": None,
                                    w: count}, "note": "test", "source": "test"})
        state = U.quota_state("acme", now=now, ledger=ledger)
        assert state["exhausted"] is True, window

    monkeypatch.setattr(U, "limits_for", lambda p: {
        "known": True, "free": {"rpm": 10, "rph": None, "rpd": 50},
        "note": "test", "source": "test"})
    state = U.quota_state("acme", now=now, ledger=ledger)
    assert state["exhausted"] is False
    assert state["remaining_day"] == 47


def test_unknown_provider_has_no_invented_limit(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    from harness import usage as U
    state = U.quota_state("nobody", ledger=home / "usage.jsonl")
    assert state["exhausted"] is False
    assert state["limits"] == {"rpm": None, "rph": None, "rpd": None,
                               "concurrency": None}
    assert "no published limits" in state["note"]


def test_ingest_reads_opencode_model_id_field(tmp_path, monkeypatch):
    """Real opencode rows carry `model.id`, not `model.modelID`."""
    home = _home(tmp_path, monkeypatch)
    payload = _payload()
    payload["model"] = {"id": "cat-jam-free", "providerID": "opencode",
                        "variant": "max"}
    db = _db(tmp_path, [payload])
    from harness import usage as U
    out = U.ingest(db=db, ledger=home / "usage.jsonl")
    assert out["added"] == 1
    rec = U.records(home / "usage.jsonl")[0]
    assert (rec["provider"], rec["model"], rec["variant"]) == (
        "opencode", "cat-jam-free", "max")
