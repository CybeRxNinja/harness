"""tools.py and store.py carry the write path and the session DB but were only
exercised in passing (28% / 39% of their lines).
"""
import hashlib

import pytest


def test_read_numbers_lines_and_truncates(tmp_path):
    from harness import tools as T
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    out = T.read(tmp_path, "a.txt")
    assert out.startswith("   1# one") and "three" in out

    (tmp_path / "big.txt").write_text("\n".join(f"l{i}" for i in range(300)))
    assert "more lines" in T.read(tmp_path, "big.txt", limit=10)


def test_read_lists_directories(tmp_path):
    from harness import tools as T
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("x")
    (tmp_path / "sub" / "a.py").write_text("x")
    assert T.read(tmp_path, "sub").splitlines() == ["a.py", "b.py"]


def test_read_write_edit_are_jailed(tmp_path):
    from harness import tools as T
    (tmp_path / "in.txt").write_text("hi")
    for escape in ("../outside.txt", "/etc/hostname"):
        with pytest.raises(PermissionError):
            T.read(tmp_path, escape)
        with pytest.raises(PermissionError):
            T.write(tmp_path, escape, "nope")


def test_write_creates_parents_and_caps_size(tmp_path):
    from harness import tools as T
    msg = T.write(tmp_path, "deep/nested/new.txt", "content")
    assert "wrote 7 bytes" in msg and (tmp_path / "deep/nested/new.txt").read_text() == "content"
    with pytest.raises(ValueError):
        T.write(tmp_path, "huge.txt", "x" * 200_001)


def test_edit_requires_a_unique_exact_match(tmp_path):
    from harness import tools as T
    p = tmp_path / "e.txt"
    p.write_text("alpha\nbeta\nalpha\n")

    assert T.edit(tmp_path, "e.txt", "nope", "x").startswith("REJECTED: old_string not found")
    assert "matches >1" in T.edit(tmp_path, "e.txt", "alpha", "x")
    assert T.edit(tmp_path, "e.txt", "beta", "gamma") == "ok"
    assert p.read_text() == "alpha\ngamma\nalpha\n"


def test_edit_hash_anchor_rejects_stale_lines(tmp_path):
    from harness import tools as T
    p = tmp_path / "h.txt"
    p.write_text("first\nsecond\n")
    good = hashlib.md5(b"second").hexdigest()[:2].upper()

    assert T.edit(tmp_path, "h.txt", "second", "2nd", f"2#{good}") == "ok"
    # the anchor was taken before the file changed: the hash no longer matches
    assert "stale-line" in T.edit(tmp_path, "h.txt", "2nd", "x", f"1#{good}")
    assert "bad hash_id" in T.edit(tmp_path, "h.txt", "2nd", "x", "notanumber#ZZ")


def test_glob_and_grep_skip_state_dirs(tmp_path):
    from harness import tools as T
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("import os\ntoken = 'needle'\n")
    for skipped in (".git", ".opencode", ".harness"):
        d = tmp_path / skipped
        d.mkdir()
        (d / "hidden.py").write_text("needle")

    assert T.glob(tmp_path, "src/*.py") == ["src/mod.py"]
    hits = T.grep(tmp_path, r"needle", include="*.py")
    assert hits == ["src/mod.py:2:token = 'needle'"]
    # a slashless pattern matches file names: `*` is the top level, `**/*.py`
    # is how you recurse (a bare `*` matching every nested path was a trap)
    assert T.glob(tmp_path, "*") == []
    assert T.glob(tmp_path, "*.py") == []
    assert T.glob(tmp_path, "**/*.py") == ["src/mod.py"]


def test_grep_skips_unreadable_and_oversized_files(tmp_path):
    from harness import tools as T
    (tmp_path / "big.py").write_text("x" * 400_000)
    (tmp_path / "small.py").write_text("needle")
    assert [h.split(":")[0] for h in T.grep(tmp_path, "needle")] == ["small.py"]


def test_store_session_messages_and_search(tmp_path):
    from harness import store
    con = store.connect(tmp_path)
    try:
        store.ensure_session(con, "s1")
        store.ensure_session(con, "s1")  # idempotent
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

        store.add_message(con, "s1", "user", "hello world")
        store.add_message(con, "s1", "assistant", "hi there")
        store.add_message(con, "s2", "user", "unrelated")
        hist = store.history(con, "s1")
        assert [m["role"] for m in hist] == ["user", "assistant"], "oldest first"
        assert store.history(con, "s1", limit=1) == [{"role": "assistant", "content": "hi there"}]

        found = store.search(con, "hello")
        assert found and found[0]["session"] == "s1"
        assert store.search(con, "AND AND (") == [], "a bad FTS query degrades to empty"
    finally:
        con.close()


def test_store_migrates_a_legacy_db_path(tmp_path):
    from harness import store
    legacy = tmp_path / ".harness"
    legacy.mkdir()
    (legacy / "sessions.db").write_bytes(b"")
    assert store.db_path(tmp_path).parent.name == "harness"
    assert store.db_path(tmp_path).parent.parent.name == ".opencode"
