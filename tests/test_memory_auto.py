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


def _recent(con, limit=5):
    """Newest facts, oldest-last — the retired memory.recent() as raw SQL."""
    return [{"text": t, "source": s} for t, s in
            con.execute("SELECT text,source FROM facts ORDER BY id DESC LIMIT ?",
                        (limit,)).fetchall()]


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


def test_recall_promotes_done_over_generic_turns(con):
    from harness import memory as M
    M.save_fact(con, "pytest tests/test_risk.py verifies the generic risk flow", "turn")
    M.save_fact(con, "pytest tests/test_risk.py verifies the verified risk flow", "done")
    hits = M.recall(con, "pytest tests/test_risk.py risk", 3)
    assert hits[0]["source"] == "done"


def test_recall_uses_session_history_too(con):
    import time as _t
    from harness import memory as M
    # the headless turn writer is retired; seed the FTS index directly to prove
    # recall still reads session history when it is there
    con.execute("INSERT INTO messages(session,role,content,ts) VALUES(?,?,?,?)",
                ("s1", "assistant", "the checkpoint restore defect was a forward patch",
                 int(_t.time())))
    con.execute("INSERT INTO messages_fts(content,session) VALUES(?,?)",
                ("the checkpoint restore defect was a forward patch", "s1"))
    con.commit()
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
    assert "number 0" not in " ".join(f["text"] for f in _recent(con, 20))


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
    sources = {f["source"] for f in _recent(con, 5)}
    assert "done" in sources and "done" in {r[0] for r in con.execute("SELECT DISTINCT source FROM facts").fetchall()}


def test_capture_turn_notes_progress_without_evidence(con, root, cfg):
    from harness import memory as M
    M.capture_turn(con, "s1", "Decision: something changed.", verified=False, cfg=cfg, root=root)
    assert not any(f["source"] == "done" for f in _recent(con, 5))
    assert any(f["source"] == "progress" for f in _recent(con, 5))


def test_capture_turn_honors_memory_disabled(con, root, cfg):
    from harness import memory as M
    off = dict(cfg, memory={**cfg["memory"], "enabled": False})
    out = M.capture_turn(con, "s1", "Decision: should not be stored.", cfg=off, root=root)
    assert out["skipped"] == "memory disabled" and M.fact_count(con) == 0


def test_capture_turn_skips_progress_for_a_mock_echo_with_facts(con, root, cfg):
    """A mock-model echo alongside real facts gets no progress note.

    `[mock:..] echo: ..` is the harness talking to itself for tests; the
    captured facts already say where the work got to, and the echo headline
    would only add another `progress [s_x]: [mock:..` sidebar row.
    """
    from harness import memory as M
    text = ("[mock:test-model] echo: Decision: keep the delivered flag "
            "so inbox() does not repeat.")
    out = M.capture_turn(con, "s1", text, verified=False, cfg=cfg, root=root)
    assert out["facts"], "the durable line inside the echo is still remembered"
    assert out["progress"] == 0
    sources = {f["source"] for f in _recent(con, 10)}
    assert "turn" in sources
    assert not ({"progress", "done"} & sources), "no echo headline as a progress note"


def test_capture_turn_keeps_a_breadcrumb_for_a_pure_mock_echo(con, root, cfg):
    """A mock echo with nothing durable stays the turn's only trace."""
    from harness import memory as M
    out = M.capture_turn(con, "s1", "[mock:test-model] echo: hello", cfg=cfg, root=root)
    assert out["facts"] == []
    assert out["progress"] > 0, "the loop-wiring breadcrumb must still be recorded"
    assert any(f["source"] == "progress" for f in _recent(con, 5))


def test_capture_turn_still_notes_progress_for_an_ordinary_turn(con, root, cfg):
    """A non-mock turn records progress exactly as before."""
    from harness import memory as M
    out = M.capture_turn(con, "s1",
                         "Decision: keep the delivered flag so inbox() does not repeat.",
                         verified=False, cfg=cfg, root=root)
    assert out["facts"] and out["progress"] > 0
    assert any(f["source"] == "progress" for f in _recent(con, 5))


def test_prune_drops_notes_but_keeps_progress_and_lessons(con):
    from harness import memory as M
    old = int(time.time()) - 90 * 86400
    con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)", ("an old note", "chat", old))
    con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)", ("an old turn", "turn", old))
    con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)", ("an old done", "done", old))
    con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)", ("an old lesson", "lesson", old))
    assert M.prune(con, 30) == 1
    left = {f["text"] for f in _recent(con, 10)}
    assert "an old note" not in left and {"an old turn", "an old done", "an old lesson"} <= left


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


def test_memory_file_lives_in_the_harness_state_dir_not_the_repo_root(root):
    """Per-project state is never published: the exact path is pinned.

    A `MEMORY.md` at the project root would be a committed per-project file, so
    the durable lessons live in `.opencode/harness/` beside sessions.db.
    """
    from harness import memory as M
    assert M.memory_file(root) == root / ".opencode" / "harness" / "MEMORY.md"
    assert M.memory_file(root).parent != root


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


# --- wiring -----------------------------------------------------------------

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
    import harness.memory as M
    # one excerpt stays staged, so nothing is written to the state dir yet
    assert not M.memory_file(root).exists()
    con = connect(root)
    assert M.evidence(con, "cli-lesson")
    con.close()
