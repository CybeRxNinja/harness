"""rlm.spawn guards (the spawn contract) and the worker prompt. The pool is the
only concurrency limit; the extra semaphore that used to guard it was redundant
and is gone.
"""
import time

import pytest


@pytest.fixture()
def root(tmp_path, monkeypatch):
    # hermetic: background workers must never reach the network, and the pool is
    # module-level, so a worker from an earlier test can still be running
    monkeypatch.setenv("HARNESS_MOCK", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    return tmp_path


def _cfg(root):
    from harness.config import load_config
    return load_config(root)[0]


def test_spawn_refuses_an_ambiguous_or_bad_spec(root):
    from harness import rlm
    from harness.store import connect
    cfg = _cfg(root)
    con = connect(root)
    try:
        with pytest.raises(ValueError):
            rlm.spawn(con, cfg, root, "x", name="", category="quick")
        with pytest.raises(ValueError):
            rlm.spawn(con, cfg, root, "x", name="a")  # neither
        with pytest.raises(ValueError):
            rlm.spawn(con, cfg, root, "x", name="a", category="quick", subagent_type="explore")
        with pytest.raises(ValueError):
            # category is an INTENT (quick/deep/...), never a provider/model
            rlm.spawn(con, cfg, root, "x", name="a", category="anthropic/claude-sonnet-4-5")
        with pytest.raises(ValueError):
            # a typo used to be accepted, producing a worker with an unknown kind
            rlm.spawn(con, cfg, root, "x", name="a", category="quickk")
        with pytest.raises(ValueError):
            rlm.spawn(con, cfg, root, "x", name="a", subagent_type="nope")
        assert rlm.list_subagents(con) == [], "a refused spawn must not register a worker"
    finally:
        con.close()


def test_intents_are_config_driven_and_the_legacy_dict_still_loads(root):
    """`categories` is the list of valid intents (rlm.spawn validates against
    it). A pre-existing harness.jsonc with the old per-category dict shape must
    keep loading rather than starting to refuse every spawn."""
    from harness import rlm
    from harness.config import intents
    from harness.store import connect
    legacy = {"categories": {"quick": {"reasoning": "low", "max_turns": 15},
                             "deep": {"reasoning": "high"}}}
    assert intents(legacy) == ["deep", "quick"]
    cfg = dict(_cfg(root), categories=["quick", "careful"])
    con = connect(root)
    try:
        assert rlm.spawn(con, cfg, root, "x", name="ok", category="careful")["status"] == "queued"
        with pytest.raises(ValueError):
            rlm.spawn(con, cfg, root, "x", name="nope", category="deep")
    finally:
        con.close()


def test_spawn_is_refused_when_depth_is_zero(root):
    from harness import rlm
    from harness.store import connect
    cfg = _cfg(root)
    cfg["budgets"] = dict(cfg.get("budgets", {}), max_depth=0)
    con = connect(root)
    try:
        with pytest.raises(RuntimeError):
            rlm.spawn(con, cfg, root, "x", name="a", category="quick")
    finally:
        con.close()


def test_spawn_queues_and_writes_the_worker_brief(root):
    from harness import rlm
    from harness.store import connect
    cfg = _cfg(root)
    con = connect(root)
    try:
        h = rlm.spawn(con, cfg, root, "read the docs", name="w1", category="quick")
        assert h["status"] == "queued"
        brief = root / ".opencode" / "harness" / "workers" / h["rlm_child_id"] / "prompt.md"
        assert "read the docs" in brief.read_text()
        row = [w for w in rlm.list_subagents(con) if w["id"] == h["rlm_child_id"]]
        assert row and row[0]["kind"] == "quick"
    finally:
        con.close()


def test_read_only_kinds_get_a_read_only_prompt_others_do_not(root, monkeypatch):
    from harness import rlm, models
    from harness.store import connect
    cfg = _cfg(root)
    prompts: dict[str, str] = {}

    def fake_chat(msgs, **kw):
        prompts[msgs[1]["content"]] = msgs[0]["content"]
        return {"content": "SUMMARY\n- nothing to change"}

    monkeypatch.setattr(models, "chat", fake_chat)
    con = connect(root)
    try:
        h = rlm.spawn(con, cfg, root, "where is auth?", name="explore1",
                      subagent_type="explore")
        wdir = root / ".opencode" / "harness" / "workers" / h["rlm_child_id"]
        for _ in range(200):
            if (wdir / "result.md").exists():
                break
            time.sleep(0.05)

        readonly = [s for s in prompts.values() if "READ-ONLY" in s]
        assert readonly and "SUMMARY + DIFF" in readonly[0]
        assert "where is auth?" in prompts, prompts.keys()
        # the result file leads with the worker's identity and terminal status,
        # so a reader can tell a finished run from one that never started
        result = (wdir / "result.md").read_text()
        assert result.startswith("# explore1 [done]"), result[:60]
        assert "SUMMARY" in result
        assert any(m["from"] == "explore1" for m in rlm.inbox(con))
    finally:
        con.close()


def test_writable_kinds_are_not_marked_read_only(root, monkeypatch):
    from harness import rlm, models
    from harness.store import connect
    cfg = _cfg(root)
    prompts: dict[str, str] = {}

    def fake_chat(msgs, **kw):
        prompts[msgs[1]["content"]] = msgs[0]["content"]
        return {"content": "SUMMARY\n- ok"}

    monkeypatch.setattr(models, "chat", fake_chat)
    con = connect(root)
    try:
        h = rlm.spawn(con, cfg, root, "apply the fix", name="impl", category="quick")
        wdir = root / ".opencode" / "harness" / "workers" / h["rlm_child_id"]
        for _ in range(200):
            if (wdir / "result.md").exists():
                break
            time.sleep(0.05)
        assert "apply the fix" in prompts, prompts.keys()
        assert "READ-ONLY" not in prompts["apply the fix"]
    finally:
        con.close()


def test_mailbox_send_and_role_filter(root):
    from harness import rlm
    from harness.store import connect
    con = connect(root)
    try:
        rlm.send(con, "worker-a", "done: auth lives in auth.py")
        rlm.send(con, "worker-b", "blocked on tests", receiver_role="sibling")
        parent = rlm.inbox(con)
        assert [m["from"] for m in parent] == ["worker-a"]
        assert rlm.inbox(con, role="sibling")[0]["content"].startswith("blocked")
        assert rlm.inbox(con, role="nobody") == []
    finally:
        con.close()


# --- lifecycle ---------------------------------------------------------------
# A parent flushes its inbox every turn, so a message that is never marked
# delivered is re-appended forever; and a worker whose thread dies used to sit
# at `running` for good, which is worse than an honest failure.


def _await(con, h, timeout=30):
    from harness import rlm
    return rlm.wait(con, h["rlm_child_id"], timeout_s=timeout)


def test_pool_is_sized_from_the_config_and_reused(root):
    from harness import rlm
    two, four, again = rlm.pool(2), rlm.pool(4), rlm.pool(2)
    assert two._max_workers == 2 and four._max_workers == 4
    assert two is again, "a width must not build a new executor every spawn"
    assert rlm.pool(0)._max_workers == 1


def test_worker_timeout_defaults_to_ten_minutes_and_is_configurable(root):
    from harness import rlm
    assert rlm.worker_timeout({}) == 600
    assert rlm.worker_timeout({"budgets": {"worker_timeout_s": 30}}) == 30


def test_worker_result_file_leads_with_status_and_a_fact_is_recorded(root, monkeypatch):
    from harness import rlm, memory as M
    from harness.store import connect
    con = connect(root)
    try:
        h = rlm.spawn(con, _cfg(root), root, "summarize the module",
                      name="sum1", category="quick")
        assert _await(con, h)["status"] == "done"
        text = rlm.result(con, h["rlm_child_id"])
        assert text.startswith("# sum1 [done]"), text[:60]
        assert any("sum1" in f["text"] for f in M.recall(con, "worker sum1"))
    finally:
        con.close()


def test_mailbox_messages_are_delivered_exactly_once(root):
    from harness import rlm
    from harness.store import connect
    con = connect(root)
    try:
        rlm.send(con, "a", "first")
        rlm.send(con, "b", "second")
        assert len(rlm.inbox(con, mark_delivered=False)) == 2, "peeking must not mark"
        assert len(rlm.inbox(con, mark_delivered=False)) == 2
        assert len(rlm.inbox(con)) == 2
        assert rlm.inbox(con) == [], "a flushed message must not come back next turn"
    finally:
        con.close()


def test_a_failed_worker_records_error_and_a_hung_one_records_timeout(root, monkeypatch):
    from harness import models, rlm
    from harness.store import connect
    con = connect(root)
    calls = {"n": 0}

    def fake_chat(msgs, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("backend exploded")
        raise RuntimeError("opencode run timed out after 300s (model x)")

    monkeypatch.setattr(models, "chat", fake_chat)
    try:
        failed = rlm.spawn(con, _cfg(root), root, "a", name="bad", category="quick")
        assert _await(con, failed)["status"] == "error"
        hung = rlm.spawn(con, _cfg(root), root, "b", name="slow", category="quick")
        assert _await(con, hung)["status"] == "timeout"
        assert "timed out" in rlm.result(con, hung["rlm_child_id"])
        assert not [w for w in rlm.list_subagents(con) if w["status"] in ("queued", "running")]
    finally:
        con.close()


def test_a_worker_that_cannot_start_never_claims_to_be_running(root, monkeypatch):
    """connect() failing used to escape before the status update, leaving a row
    that says `queued` forever and a pool slot nobody can account for."""
    from harness import rlm, store
    from harness.store import connect

    def boom(_root):
        raise RuntimeError("database is locked")

    con = connect(root)
    monkeypatch.setattr(store, "connect", boom)
    try:
        h = rlm.spawn(con, _cfg(root), root, "x", name="nodb", category="quick")
        for _ in range(100):
            wdir = root / ".opencode" / "harness" / "workers" / h["rlm_child_id"]
            if (wdir / "result.md").exists():
                break
            time.sleep(0.05)
        assert "could not start" in (wdir / "result.md").read_text()
        # the row cannot be closed by the worker (it never got a connection), so
        # the stale sweep is what surfaces it instead of a silent `queued`
        stale = rlm.list_subagents(con, stale_after_s=-1)
        assert stale[0]["status"] == "stale", stale
    finally:
        con.close()


def test_stale_workers_are_reported_and_pruned(root):
    from harness import rlm
    from harness.store import connect
    con = connect(root)
    try:
        con.execute("INSERT INTO workers(id,session,name,category,model,status,cost,updated) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    ("w_dead", "", "dead", "quick", "m", "running", 0.0, int(time.time()) - 9000))
        con.commit()
        assert rlm.list_subagents(con, stale_after_s=60)[0]["status"] == "stale"
        assert rlm.prune_stale(con, older_than_s=60) == 1
        row = con.execute("SELECT status FROM workers WHERE id='w_dead'").fetchone()
        assert row[0] == "stale"
    finally:
        con.close()


def test_result_and_wait_are_honest_about_an_unknown_worker(root):
    from harness import rlm
    from harness.store import connect
    con = connect(root)
    try:
        assert "unknown worker" in rlm.result(con, "w_nope")
        assert rlm.wait(con, "w_nope", timeout_s=0.1)["status"] == "unknown"
        con.execute("INSERT INTO workers(id,session,name,category,model,status,cost,updated) "
                    "VALUES(?,?,?,?,?,?,?,?)", ("w_run", "", "r", "quick", "m", "running", 0.0,
                                                int(time.time())))
        con.commit()
        out = rlm.wait(con, "w_run", timeout_s=0.2, interval=0.05)
        assert out["status"] == "running" and out["waited_out"] is True
        assert "still be running" in rlm.result(con, "w_run")
    finally:
        con.close()


def test_skills_are_passed_to_the_worker_prompt(root, monkeypatch):
    from harness import models, rlm, skills
    from harness.store import connect
    skills.ensure_seed_skills()
    prompts: list[str] = []

    def fake_chat(msgs, **kw):
        prompts.append(msgs[0]["content"])
        return {"content": "SUMMARY\n- ok"}

    monkeypatch.setattr(models, "chat", fake_chat)
    con = connect(root)
    try:
        h = rlm.spawn(con, _cfg(root), root, "do the task", name="sk", category="quick",
                      load_skills=["using-agent-skills"])
        assert _await(con, h)["status"] == "done"
        assert "Relevant skills" in prompts[0]
        assert "When to Use" in prompts[0]
    finally:
        con.close()
