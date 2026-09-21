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
            rlm.spawn(con, cfg, root, "x", name="a", subagent_type="nope")
        assert rlm.list_subagents(con) == [], "a refused spawn must not register a worker"
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
        assert (wdir / "result.md").read_text().startswith("SUMMARY")
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
