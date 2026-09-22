"""Memory: durable facts, keyword+FTS recall, progress notes, auto-promoted lessons.

Four defects this module used to have, all fixed here:

1. **Recall almost never matched.** The query was matched as one phrase
   (`text LIKE '%<first 40 chars of the turn>%'`), so a question recalled
   nothing. Recall now tokenizes into terms and ORs them, exactly like the
   plugin's `memory_recall` reads the same table from disk.
2. **Nothing was ever remembered.** Facts only appeared if the model called
   `memory save`. `capture_turn()` now extracts the durable lines from a turn
   (decisions, root causes, migrations, blockers) and stores them, deduped and
   capped, so the next session starts with them.
3. **Lessons were written where nothing read them.** `harness memory refine`
   and `harness skills approve` appended to `<state>/.opencode/harness/MEMORY.md`
   while the durable file is the project's `MEMORY.md`. `memory_file()` is now
   the single answer to where lessons go.
4. **The `memory` config block did nothing.** `enabled`, `cap_lines` and
   `retention_days` were read by nothing; they are all honored now (cap by the
   writer, retention by `prune()`).

`MEMORY.md > AGENTS.md` in the project's precedence chain, so promotions are
evidence-gated: a lesson is only auto-written when at least `min_evidence`
distinct excerpts back it (`/refine`'s rule). Everything else stays staged.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

MAX_FACT_LEN = 1000
MAX_EVIDENCE_LEN = 8000
MEMORY_HEADER = "# MEMORY.md — durable facts (cap {cap} lines, evidence-backed lessons only)"

# A marker at the start of a line says "this was meant to be kept".
IMPORTANT_MARKERS = (
    "decision:", "decided", "root cause", "fixed:", "fix:", "fixed ", "added:",
    "verified:", "verified ", "lesson:", "migration", "breaking", "blocked",
    "blocker:", "next step", "todo:", "note:", "important:", "gotcha",
    "remember:", "constraint:", "requirement:", "must ", "never ", "always ",
)
# A line carrying a failure/verification result is worth keeping too.
OUTCOME_RE = re.compile(
    r"\b(passed|failing|failed|regression|tests? (pass|fail)|reproduced|timeout"
    r"|permission denied|not found|traceback)\b", re.I)

# Never remember anything that looks like a credential.
SECRET_RES = (
    re.compile(r"\b(sk|xoxb|ghp|gho|aiza|AKIA)[-_A-Za-z0-9]{12,}"),
    re.compile(r"\b(api[_-]?key|password|passwd|secret|token)\b\s*[:=]\s*\S{8,}", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),  # JWT
)


def normalize(text: str) -> str:
    """One-line form used for storage and duplicate detection.

    Only a real list marker is stripped (`- item`, `1. item`). An earlier
    version stripped every leading `-`/`.`/digit, which quietly ate the opening
    dashes of a PEM header before the secret check could see them.
    """
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", t)
    return t


def is_secretish(text: str) -> bool:
    return any(r.search(text or "") for r in SECRET_RES)


def memory_file(root: str | Path) -> Path:
    """Where lessons live: <root>/MEMORY.md (read by AGENTS.md's chain)."""
    return Path(root).resolve() / "MEMORY.md"


def terms(query: str) -> list[str]:
    """Words worth matching: >2 chars, no duplicates, at most 5."""
    ws = [w.strip("\"'(),.:;!?") for w in str(query or "").split()]
    return [w for w in dict.fromkeys(w for w in ws if len(w) > 2)][:5]


def _fts_query(ws: list[str]) -> str:
    """Quote each term so FTS5 syntax in user text cannot break the query."""
    return " OR ".join('"' + w.replace('"', "") + '"' for w in ws if w)


def fact_count(con) -> int:
    try:
        return int(con.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
    except Exception:
        return 0


def _dupe(con, norm: str) -> bool:
    try:
        return con.execute("SELECT 1 FROM facts WHERE lower(text)=lower(?) LIMIT 1",
                           (norm,)).fetchone() is not None
    except Exception:
        return False


def save_fact(con, text: str, source: str = "chat", max_facts: int = 2000) -> int:
    """Store one durable fact. Returns its id, or 0 when it was a duplicate.

    Dedupe is on the normalized text; secrets are refused outright (a memory
    that leaks a key is worse than a forgetful one).
    """
    norm = normalize(text)[:MAX_FACT_LEN]
    # both forms: normalization must never be able to hide a credential
    if len(norm) < 8 or is_secretish(norm) or is_secretish(text) or _dupe(con, norm):
        return 0
    cur = con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)",
                      (norm, source, int(time.time())))
    _enforce_fact_cap(con, max_facts)
    con.commit()
    return int(cur.lastrowid or 0)


def _enforce_fact_cap(con, max_facts: int) -> None:
    """Keep the newest `max_facts` rows; older ones are dropped."""
    if max_facts <= 0:
        return
    try:
        n = fact_count(con)
        if n > max_facts:
            con.execute(
                "DELETE FROM facts WHERE id NOT IN "
                "(SELECT id FROM facts ORDER BY id DESC LIMIT ?)", (max_facts,))
    except Exception:
        pass


def important_lines(text: str, limit: int = 3) -> list[str]:
    """Pull the durable lines out of a turn's output.

    Deliberately conservative: a line must carry an explicit marker or an
    outcome verb, be long enough to mean something, and not sit inside a code
    fence or a diff/table row. Three lines per turn, in document order.
    """
    out: list[str] = []
    fenced = False
    for raw in str(text or "").splitlines():
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = normalize(raw)
        if len(line) < 20 or len(line) > 400:
            continue
        if line.startswith(("|", "+", ">", "$", "/", "#!")):
            continue
        if re.match(r"^(\.\.\.|\d+\s*[|:])", line):
            continue
        low = line.lower()
        if not (any(low.startswith(m) or f" {m}" in low for m in IMPORTANT_MARKERS)
                or OUTCOME_RE.search(line)):
            continue
        if line not in out:
            out.append(line)
    return out[:limit]


def headline(text: str, error: str = "", limit: int = 160) -> str:
    """One-line summary of a turn, for the progress note."""
    for raw in str(text or "").splitlines():
        line = normalize(raw)
        if len(line) >= 8:
            return line[:limit]
    if error:
        return f"error: {normalize(error)[:limit]}"
    return ""


def remember(con, text: str, source: str = "agent", limit: int = 3,
             max_facts: int = 2000) -> list[int]:
    """Auto-capture the durable lines of `text`. Returns the new fact ids."""
    ids: list[int] = []
    for line in important_lines(text, limit):
        fid = save_fact(con, line, source, max_facts=max_facts)
        if fid:
            ids.append(fid)
    return ids


def progress(con, session: str, text: str, status: str = "note") -> int:
    """Record where the work got to. `status` is note|done|blocked."""
    line = normalize(text)
    if not line:
        return 0
    tag = {"done": "done", "blocked": "blocked"}.get(status, "progress")
    return save_fact(con, f"{tag} [{session[:24]}]: {line}"[:MAX_FACT_LEN], tag)


def mark_done(con, session: str, text: str) -> int:
    return progress(con, session, text, status="done")


def recent(con, limit: int = 5, exclude: tuple[str, ...] = ()) -> list[dict]:
    """Newest facts, for the compaction brief (there is no query to match)."""
    q = "SELECT text,source FROM facts"
    if exclude:
        q += " WHERE source NOT IN (" + ",".join("?" * len(exclude)) + ")"
    q += " ORDER BY id DESC LIMIT ?"
    try:
        rows = con.execute(q, (*exclude, limit)).fetchall()
    except Exception:
        return []
    return [{"text": t, "source": s} for t, s in rows]


def recall(con, query: str, limit: int = 3) -> list[dict]:
    """Facts + session snippets relevant to `query`.

    Returns nothing when the query has no searchable term: answering an
    unrelated question with the newest handful of facts reads as recall but is
    only noise the caller would cite. Facts matching more of the query's terms
    come first, then newest.
    """
    ws = terms(query)
    if not ws:
        whole = normalize(query)
        if len(whole) <= 2:
            return []
        ws = [whole[:40]]
    hits: list[dict] = []
    try:
        where = " OR ".join("text LIKE ?" for _ in ws)
        rows = con.execute(
            f"SELECT text,source,id FROM facts WHERE {where} ORDER BY id DESC LIMIT ?",
            (*[f"%{w}%" for w in ws], max(limit * 4, 8))).fetchall()
        scored = []
        for text, source, fid in rows:
            low = text.lower()
            score = sum(1 for w in ws if w.lower() in low)
            scored.append((score, fid, text, source))
        scored.sort(key=lambda r: (-r[0], -r[1]))
        hits += [{"text": t, "source": s, "score": sc} for sc, _, t, s in scored]
    except Exception:
        pass
    try:
        from .store import search
        fq = _fts_query(ws)
        if fq:
            for h in search(con, fq, limit):
                hits.append({"text": h["snippet"], "source": f"session:{h['session']}"})
    except Exception:
        pass
    seen: set[str] = set()
    out: list[dict] = []
    for h in hits:
        key = normalize(h["text"])[:120].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Lessons: staged -> evidence-gated promotion
# ---------------------------------------------------------------------------

def stage_lesson(con, name: str, diff: str, gist: str) -> int:
    """Stage one evidence excerpt for a lesson (one row = one excerpt)."""
    cur = con.execute("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,?)",
                      ("lesson", name[:80], diff[:MAX_EVIDENCE_LEN], gist[:200], int(time.time())))
    con.commit()
    return int(cur.lastrowid or 0)


def list_pending(con) -> list[dict]:
    return [{"id": r[0], "kind": r[1], "name": r[2], "gist": r[3]}
            for r in con.execute("SELECT id,kind,name,gist FROM pending ORDER BY id").fetchall()]


def evidence(con, name: str) -> list[str]:
    """The distinct excerpts staged for a lesson (the `/refine` evidence set)."""
    rows = con.execute("SELECT diff FROM pending WHERE kind='lesson' AND name=? ORDER BY id",
                       (name[:80],)).fetchall()
    return list(dict.fromkeys(normalize(d)[:600] for (d,) in rows if normalize(d)))


def _read_entries(path: Path) -> tuple[str, list[str]]:
    """(header, entries) — entries are the `- [...]` lines, oldest first."""
    try:
        lines = path.read_text().splitlines()
    except Exception:
        return "", []
    header, entries = [], []
    for ln in lines:
        if ln.startswith("- "):
            entries.append(ln)
        elif not entries:
            header.append(ln)
    return "\n".join(header).strip(), entries


def _write_entries(path: Path, header: str, entries: list[str], cap_lines: int) -> None:
    cap = max(1, int(cap_lines or 200))
    kept = entries[-cap:] if len(entries) > cap else entries
    head = header or MEMORY_HEADER.format(cap=cap)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head + "\n" + ("\n".join(kept) + "\n" if kept else ""))


def add_lesson(memory_path: str | Path, name: str, text: str, cap_lines: int = 200) -> bool:
    """Append one lesson entry, enforcing the line cap. False for secrets/dupes."""
    p = Path(memory_path)
    body = normalize(text)
    if not body or is_secretish(body):
        return False
    entry = f"- [{name[:60]}] {body[:500]}"
    header, entries = _read_entries(p)
    if entry in entries:
        return False
    entries.append(entry)
    _write_entries(p, header, entries, cap_lines)
    return True


def approve(con, pid: int, memory_file_path, cap_lines: int = 200) -> bool:
    """Manually approve one staged item (the CLI path)."""
    row = con.execute("SELECT kind,name,diff FROM pending WHERE id=?", (pid,)).fetchone()
    if not row:
        return False
    kind, name, diff = row
    if kind == "lesson":
        add_lesson(memory_file_path, name, diff, cap_lines)
    con.execute("DELETE FROM pending WHERE id=?", (pid,))
    con.commit()
    return True


def auto_refine(con, memory_path, min_evidence: int = 2, cap_lines: int = 200) -> list[str]:
    """Promote lessons that `min_evidence` distinct excerpts already support.

    This is `/refine` with the approval step automated *by the evidence rule*
    instead of by a human: a lesson with two independent excerpts is written to
    MEMORY.md, everything else stays staged for review. Returns promoted names.
    """
    rows = con.execute(
        "SELECT name, COUNT(DISTINCT diff), COUNT(*) FROM pending "
        "WHERE kind='lesson' GROUP BY name").fetchall()
    promoted: list[str] = []
    for name, distinct, _total in rows:
        ev = evidence(con, name)
        if len(ev) < max(2, int(min_evidence)) or len(set(ev)) < max(2, int(min_evidence)):
            continue
        gist = con.execute(
            "SELECT gist FROM pending WHERE kind='lesson' AND name=? ORDER BY id DESC LIMIT 1",
            (name,)).fetchone()
        text = f"{gist[0] if gist else name} :: " + " | ".join(e[:200] for e in ev[:2])
        if add_lesson(memory_path, name, text, cap_lines):
            promoted.append(name)
        con.execute("DELETE FROM pending WHERE kind='lesson' AND name=?", (name,))
        con.commit()
    return promoted


def prune(con, retention_days: int = 30) -> int:
    """Drop facts older than `retention_days`, keeping decisions and progress.

    Notes decay; why-decisions and where-the-work-got-to do not.
    """
    keep = ("lesson", "decision", "done", "blocked", "progress")
    cutoff = int(time.time()) - int(retention_days) * 86400
    try:
        cur = con.execute(
            "DELETE FROM facts WHERE ts < ? AND source NOT IN ("
            + ",".join("?" * len(keep)) + ")", (cutoff, *keep))
        con.commit()
        return int(cur.rowcount or 0)
    except Exception:
        return 0


def capture_turn(con, session: str, text: str, verified: bool = False,
                 error: str = "", cfg: dict | None = None,
                 root: str | Path | None = None) -> dict:
    """Remember what a turn should carry forward. Called once per turn.

    Facts come from the output's own durable lines; progress is recorded either
    as `done` (when the turn proved itself: tests/lint actually ran clean) or as
    a plain progress note. Honors `memory.enabled` / `cap_lines` / `max_facts`,
    and auto-promotes any lesson that now has enough evidence.
    """
    cfg = cfg or {}
    mend = (cfg.get("memory", {}) or {})
    if not mend.get("enabled", True):
        return {"facts": [], "progress": 0, "verified": verified, "promoted": [],
                "skipped": "memory disabled"}
    cap = int(mend.get("cap_lines", 200))
    max_facts = int(mend.get("max_facts", 2000))
    ids = remember(con, text, source="turn", max_facts=max_facts)
    if error:
        fid = save_fact(con, f"error: {normalize(error)[:300]}", "error", max_facts=max_facts)
        if fid:
            ids.append(fid)
    headline_text = headline(text, error)
    pid = progress(con, session, headline_text, status="done" if verified else "note")
    promoted: list[str] = []
    if root is not None:
        promoted = auto_refine(con, memory_file(root), min_evidence=2, cap_lines=cap)
    prune(con, int(mend.get("retention_days", 30)))
    return {"facts": ids, "progress": pid, "verified": verified, "promoted": promoted}


def forget(con, pid_or_text: str) -> int:
    """Drop a staged item by id, or every fact matching a text fragment."""
    try:
        pid = int(pid_or_text)
    except (TypeError, ValueError):
        cur = con.execute("DELETE FROM facts WHERE text LIKE ?", (f"%{str(pid_or_text)[:60]}%",))
        con.commit()
        return int(cur.rowcount or 0)
    cur = con.execute("DELETE FROM pending WHERE id=?", (pid,))
    con.commit()
    return int(cur.rowcount or 0)
