"""The two hot memory queries must not full-scan.

The audit found both: `facts` searched with `text LIKE '%…%'` on the recall
path (a leading-wildcard LIKE cannot ride a btree, so the indexed answer is
the FTS5 index), and `pending` grouped by `kind,name` on the auto-refine path
with no index at all. EXPLAIN QUERY PLAN on this schema must say SEARCH /
USING INDEX for both — on the old schema both say SCAN, which is what makes
this the failing-first proof.
"""
import sqlite3

from harness.store import connect


def _plan(con, sql, args=()) -> str:
    return " | ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args))


def test_hot_memory_queries_use_indexes(tmp_path):
    con = connect(tmp_path)

    # the new objects are in the schema
    objects = {r[0] for r in con.execute("SELECT name FROM sqlite_master").fetchall()}
    assert "idx_pending_kind_name" in objects
    assert "facts_fts" in objects  # FTS5 over facts
    assert "facts_fts_ai" in objects and "facts_fts_ad" in objects

    # auto-refine: memory.auto_refine() groups pending by kind,name
    p = _plan(con, "SELECT name, COUNT(DISTINCT diff), COUNT(*) FROM pending "
                   "WHERE kind='lesson' GROUP BY name")
    assert "SCAN" not in p, p  # was: SCAN pending
    assert "SEARCH pending USING INDEX idx_pending_kind_name" in p, p

    # recall: facts reached through the FTS index (the LIKE stays, unindexable)
    p = _plan(con, "SELECT f.text FROM facts_fts "
                   "JOIN facts f ON f.id = facts_fts.rowid WHERE facts_fts MATCH ?",
              ("alpha",))
    assert "SCAN f " not in p, p  # facts via rowid, was: SCAN facts
    assert "SEARCH f USING INTEGER PRIMARY KEY" in p, p
    assert "VIRTUAL TABLE INDEX" in p, p  # the FTS index itself

    # an index is only honest while the triggers keep it in sync
    con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)",
                ("alpha recall probe", "turn", 1))
    con.commit()
    assert con.execute("SELECT text FROM facts_fts WHERE facts_fts MATCH 'alpha'").fetchall()
    con.execute("DELETE FROM facts WHERE text LIKE 'alpha recall%'")
    con.commit()
    assert not con.execute("SELECT text FROM facts_fts WHERE facts_fts MATCH 'alpha'").fetchall()

    # migration is additive: a database whose facts predate the triggers
    # (simulated: sync trigger dropped, then a row written) is backfilled on
    # reopen, exactly once — no duplicates, no re-scan.
    con.execute("DROP TRIGGER facts_fts_ai")
    con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)",
                ("delta legacy row", "turn", 2))
    con.commit()
    con.close()
    con = connect(tmp_path)
    assert con.execute("SELECT text FROM facts_fts WHERE facts_fts MATCH 'delta'").fetchall()
    assert (con.execute("SELECT COUNT(*) FROM facts_fts").fetchone()[0]
            == con.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
    con.close()
