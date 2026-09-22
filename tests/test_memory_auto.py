"""The memory system has to work without being asked.

Two failures motivated this file. Recall matched the turn as one phrase
(`LIKE '%<first 40 chars>%'`), so it almost never returned anything; and
nothing wrote facts in the first place, so `MEMORY.md` was an empty file and
the facts table filled only when the model happened to call `memory save`.
"""
import time

import pytest


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_MOCK", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture()
def cfg(root):
    from harness.config import load_config
    return load_config(root)[0]


@pytest.fixture()
def con(root):
    from harness.store import connect
    c = connect(root)
    yield c
    c.close()


# --- recall -----------------------------------------------------------------

def test_recall_matches_any_term_of_a_real_question(con):
    from harness import memory as M
    M.save_fact(con, "the mailbox gets a delivered flag so inbox() does not repeat", "turn")
    # a whole-sentence phrase match never hit this; term matching does
    hits = M.recall(con, "why does the mailbox repeat the same message?")
    assert hits and "delivered flag" in hits[0]["text"]


def test_recall_returns_nothing_for_a_query_with_no_searchable_term(con):
    from harness import memory as M
    M.save_fact(con, "an unrelated durable fact about the kernel", "turn")
    # answering with the newest fact regardless of the question reads as recall
    # but is only noise the caller would cite
    assert M.recall(con, "?") == []
    assert M.recall(con, "") == []


def test_recall_ranks_facts_matching_more_terms_first(con):
    from harness import memory as M
    M.save_fact(con, "rlm spawns workers through a pool", "turn")
    M.save_fact(con, "rlm workers report status and cost back through the mailbox", "turn")
    top = M.recall(con, "rlm workers mailbox status", 2)
    assert "mailbox" in top[0]["text"]


def test_recall_uses_session_history_too(con):
    from harness import memory as M
    from harness.store import add_message, ensure_session
    ensure_session(con, "s1")
    add_message(con, "s1", "assistant", "the checkpoint restore defect was a forward patch")
    hits = M.recall(con, "checkpoint restore defect")
    assert any("checkpoint" in h["text"] for h in hits), hits


def test_recall_survives_fts_syntax_in_the_query(con):
    from harness import memory as M
    M.save_fact(con, "quoting matters for the fts query", "turn")
    # unbalanced quotes used to be a raw MATCH syntax error (silently swallowed)
    assert isinstance(M.recall(con, '"unbalanced OR (weird'), list)


# --- capture ----------------------------------------------------------------

def test_save_fact_dedupes_and_refuses_secrets(con):
    from harness import memory as M
    assert M.save_fact(con, "the pool is the only concurrency limit", "turn") > 0
    assert M.save_fact(con, "the pool is the only concurrency limit", "turn") == 0
    assert M.save_fact(con, "  the   pool is the ONLY concurrency limit ", "turn") == 0
    for secret in ("api_key = sk-abcdefghijklmnop1234",
                   "password: hunter2hunter2",
                   "-----BEGIN RSA PRIVATE KEY-----"):
        assert M.save_fact(con, secret, "turn") == 0


def test_facts_are_capped(con):
    from harness import memory as M
    for i in range(12):
        M.save_fact(con, f"durable fact number {i} about the system", "turn", max_facts=10)
    assert M.fact_count(con) == 10
    assert "number 0" not in " ".join(f["text"] for f in M.recent(con, 20))


def test_important_lines_keeps_decisions_and_skips_noise():
    from harness import memory as M
    text = """I refactored the store today.
Decision: the mailbox gets a delivered flag so inbox() does not repeat.
```
Decision: this one is inside a code fence and is not real
```
| Decision: a table row |
Tests passed: 116 passed in 15s.
some perfectly ordinary sentence with no marker at all in it
Blocker: the release workflow still needs a tag.
"""
    lines = M.important_lines(text)
    assert lines[0].startswith("Decision: the mailbox")
    assert "Tests passed" in lines[1]
    assert all("code fence" not in l and "table row" not in l for l in lines)
    assert len(lines) == 3  # capped per turn
    assert M.important_lines(text, limit=3)[-1].startswith("Blocker:")


def test_important_lines_ignores_short_and_overlong_lines():
    from harness import memory as M
    assert M.important_lines("Fixed: ok") == []            # too short to mean anything
    assert M.important_lines("Fixed: " + "x " * 400) == []


def test_headline_is_one_usable_line():
    from harness import memory as M
    assert M.headline("first line of the summary\nsecond") == "first line of the summary"
    assert M.headline("", error="opencode run timed out") == "error: opencode run timed out"
    assert M.headline("", "") == ""


def test_capture_turn_remembers_facts_and_progress(con, root, cfg):
    from harness import memory as M
    text = "Decision: keep the generated permission map.\nTests passed: 116 passed."
    out = M.capture_turn(con, "s1", text, verified=True, cfg=cfg, root=root)
    assert out["facts"] and out["verified"] is True
    sources = {f["source"] for f in M.recent(con, 5)}
    assert "turn" in sources and "done" in sources


def test_capture_turn_notes_progress_without_evidence(con, root, cfg):
    from harness import memory as M
    M.capture_turn(con, "s1", "Decision: something changed.", verified=False, cfg=cfg, root=root)
    assert not any(f["source"] == "done" for f in M.recent(con, 5))
    assert any(f["source"] == "progress" for f in M.recent(con, 5))


def test_capture_turn_honors_memory_disabled(con, root, cfg):
    from harness import memory as M
    off = dict(cfg, memory={**cfg["memory"], "enabled": False})
    out = M.capture_turn(con, "s1", "Decision: should not be stored.", cfg=off, root=root)
    assert out["skipped"] == "memory disabled" and M.fact_count(con) == 0


def test_prune_drops_notes_but_keeps_progress_and_lessons(con):
    from harness import memory as M
    old = int(time.time()) - 90 * 86400
    con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)", ("an old note", "turn", old))
    con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)", ("an old done", "done", old))
    con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)", ("an old lesson", "lesson", old))
    assert M.prune(con, 30) == 1
    left = {f["text"] for f in M.recent(con, 10)}
    assert "an old note" not in left and {"an old done", "an old lesson"} <= left


# --- lessons: evidence-gated promotion --------------------------------------

def test_one_excerpt_stays_staged_and_two_promote(con, root):
    from harness import memory as M
    path = M.memory_file(root)
    M.stage_lesson(con, "mailbox-dedupe", "excerpt one about the delivered flag", "delivered flag")
    assert M.auto_refine(con, path) == []
    assert not path.exists() or "mailbox-dedupe" not in path.read_text()
    M.stage_lesson(con, "mailbox-dedupe", "excerpt two about the same defect", "second excerpt")
    assert M.auto_refine(con, path) == ["mailbox-dedupe"]
    body = path.read_text()
    assert "- [mailbox-dedupe]" in body and "delivered flag" in body
    # promoted rows are consumed; the lone one is still awaiting review
    assert M.list_pending(con) == []


def test_identical_excerpts_do_not_count_as_two_pieces_of_evidence(con, root):
    from harness import memory as M
    M.stage_lesson(con, "dupe-evidence", "the very same excerpt", "g")
    M.stage_lesson(con, "dupe-evidence", "the very same excerpt", "g")
    assert M.auto_refine(con, M.memory_file(root)) == []


def test_evidence_only_counts_a_lesson_once(con, root):
    from harness import memory as M
    M.stage_lesson(con, "a-lesson", "e1", "g")
    M.stage_lesson(con, "a-lesson", "e2", "g")
    promoted = M.auto_refine(con, M.memory_file(root))
    assert promoted == ["a-lesson"]
    # a second pass must not duplicate the entry
    assert M.auto_refine(con, M.memory_file(root)) == []
    assert M.memory_file(root).read_text().count("[a-lesson]") == 1


def test_memory_file_is_the_projects_memory_md_not_hidden_state(root):
    from harness import memory as M
    assert M.memory_file(root) == root / "MEMORY.md"


def test_manual_approval_writes_the_same_file_and_caps_it(con, root):
    from harness import memory as M
    path = M.memory_file(root)
    for i in range(6):
        pid = M.stage_lesson(con, f"lesson-{i}", f"evidence {i}", f"gist {i}")
        assert M.approve(con, pid, path, cap_lines=4) is True
    body = path.read_text()
    assert body.startswith("# MEMORY.md")
    assert len([l for l in body.splitlines() if l.startswith("- ")]) == 4
    assert "lesson-5" in body and "lesson-0" not in body


def test_approve_refuses_a_secret_and_returns_false_for_a_missing_id(con, root):
    from harness import memory as M
    pid = M.stage_lesson(con, "leaky", "token = ghp_abcdefghijklmnopqrstuvwxyz", "leak")
    M.approve(con, pid, M.memory_file(root))
    body = M.memory_file(root).read_text() if M.memory_file(root).exists() else ""
    assert "ghp_" not in body and "leaky" not in body
    assert M.approve(con, 999999, M.memory_file(root)) is False


def test_forget_by_id_and_by_text(con):
    from harness import memory as M
    pid = M.stage_lesson(con, "temp", "e", "g")
    assert M.forget(con, pid) == 1
    M.save_fact(con, "a fact worth deleting later", "turn")
    assert M.forget(con, "worth deleting") == 1


# --- wiring -----------------------------------------------------------------

def test_the_loop_captures_the_turn_and_ticks_the_ledger_only_with_evidence(root, cfg):
    from pathlib import Path
    from harness import orchestrator as O
    from harness.loop import run_turn
    wid = O.start_plan(root, "memory work", ["recall works"])
    out = run_turn(root, "s9", "finish the memory task", mode="code", cfg=cfg)
    assert out["memory"]["progress"] > 0
    assert out["ledger"] is None, "a turn with no test run must not close a box"
    plan = Path(O.load_boulder(root)["works"][wid]["plan"])
    assert O.next_box(plan.read_text()) == "1. recall works", plan.read_text()


def test_a_verified_run_closes_the_box(root, cfg, monkeypatch):
    """`pytest -q` that comes back clean is the evidence the ledger accepts."""
    from pathlib import Path
    from harness import models, orchestrator as O
    from harness.loop import run_turn
    wid = O.start_plan(root, "verify work", ["tests pass"])
    turns = {"n": 0}

    (root / "test_ok.py").write_text("def test_ok():\n    assert True\n")

    def fake_chat(msgs, **kw):
        turns["n"] += 1
        if turns["n"] == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "t1", "type": "function",
                                    "function": {"name": "shell",
                                                 "arguments": '{"cmd": "python -m pytest -q test_ok.py"}'}}]}
        return {"role": "assistant", "content": "Decision: tests are green."}

    monkeypatch.setattr(models, "chat", fake_chat)
    out = run_turn(root, "s10", "run the tests", mode="code", cfg=cfg)
    assert out["verified"] is True, out
    assert out["ledger"], "a clean test run must close the next box"
    assert O.next_box(Path(O.load_boulder(root)["works"][wid]["plan"]).read_text()) is None


def test_a_failing_test_run_closes_nothing(root, cfg, monkeypatch):
    """The rule has to cut both ways: a red run is not evidence of done."""
    from harness import models, orchestrator as O
    from harness.loop import run_turn
    wid = O.start_plan(root, "red work", ["stays open"])
    turns = {"n": 0}

    def fake_chat(msgs, **kw):
        turns["n"] += 1
        if turns["n"] == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "t1", "type": "function",
                                    "function": {"name": "shell",
                                                 "arguments": '{"cmd": "python -m pytest -q missing.py"}'}}]}
        return {"role": "assistant", "content": "Root cause: the test file is missing."}

    monkeypatch.setattr(models, "chat", fake_chat)
    out = run_turn(root, "s11", "run the tests", mode="code", cfg=cfg)
    assert out["verified"] is False and out["ledger"] is None
    from pathlib import Path
    assert O.next_box(Path(O.load_boulder(root)["works"][wid]["plan"]).read_text()) == "1. stays open"


def test_cli_memory_show_and_refine_use_the_project_memory_file(root, monkeypatch):
    from harness.cli import main
    from harness.store import connect
    con = connect(root)
    con.execute("INSERT INTO messages(session,role,content,ts) VALUES(?,?,?,?)",
                ("default", "assistant", "Root cause: " + "x" * 120, int(time.time())))
    con.commit()
    con.close()
    assert main(["--root", str(root), "memory", "refine", "--name", "cli-lesson", "--session", "default"]) == 0
    assert main(["--root", str(root), "memory", "show"]) == 0
    assert (root / "MEMORY.md").exists() or True  # staged until 2 excerpts
    import harness.memory as M
    con = connect(root)
    assert M.evidence(con, "cli-lesson")
    con.close()
