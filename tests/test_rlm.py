"""Worker-state readers kept for `harness doctor` (the pool/spawn/mailbox path
was retired: live sessions route subagents through opencode's native task
tool, and the sidebar reads the workers table directly)."""


def test_worker_timeout_defaults_to_ten_minutes_and_is_configurable():
    from harness import rlm
    assert rlm.worker_timeout({}) == 600
    assert rlm.worker_timeout({"budgets": {"worker_timeout_s": 30}}) == 30


def test_is_terminal_leaves_stale_unresolved():
    from harness import rlm
    assert rlm.is_terminal("done") and rlm.is_terminal("error") and rlm.is_terminal("timeout")
    assert not rlm.is_terminal("queued") and not rlm.is_terminal("running")
    assert not rlm.is_terminal("stale"), "stale is a computed verdict, not a final state"
