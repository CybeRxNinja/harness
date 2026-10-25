"""Per-call usage ledger from opencode's session_message table.

Records EXACTLY: id, at, session, agent, provider, model, variant, input, output,
reasoning, cache_read, cache_write, cost. Never stores message text/paths/prompts.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


def user_dir() -> Path:
    return Path(os.environ.get("HARNESS_HOME", str(Path.home() / ".harness")))


def ledger_path() -> Path:
    p = user_dir() / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    # Ensure mode 0600 on create; atomic rewrite will preserve it.
    if not p.exists():
        p.write_text("")
        p.chmod(0o600)
    return p


def db_path() -> Path:
    """Path to opencode's session database."""
    override = os.environ.get("HARNESS_OPENCODE_DB")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "opencode" / "opencode.db"
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def _load_limits() -> dict:
    """Load provider limits from package data."""
    try:
        data_file = Path(__file__).resolve().parent / "data" / "provider-limits.json"
        return json.loads(data_file.read_text())
    except Exception:
        return {"providers": {}}


def limits_for(provider: str) -> dict:
    """Return the free-tier limits for a provider, or a sentinel for unknown."""
    data = _load_limits()
    prov = (data.get("providers") or {}).get(provider)
    if not prov:
        return {"known": False, "note": "no published limits"}
    free = prov.get("free")
    if free is None:
        return {"known": True, "free": None, "note": prov.get("note", "paid only"),
                "source": prov.get("source")}
    note = (free.get("note") if isinstance(free, dict) else "") or prov.get("note") or ""
    source = ((free.get("source") if isinstance(free, dict) else "")
              or prov.get("source") or "")
    return {"known": True, "free": free, "note": note, "source": source}


def _atomic_write(path: Path, lines: list[str]) -> None:
    """Write lines atomically with mode 0600."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""))
    tmp.chmod(0o600)
    tmp.rename(path)


def ingest(db: Path | None = None, ledger: Path | None = None, limit: int = 20000) -> dict:
    """Read assistant rows from opencode.db, parse data JSON, write ledger.

    Idempotent by message id. Skips malformed rows. Never raises on missing/
    locked/foreign DB. Returns {ok, added, total, skipped, error}.
    """
    db = db or db_path()
    ledger = ledger or ledger_path()

    if not db.exists():
        return {"ok": False, "added": 0, "total": 0, "skipped": 0,
                "error": f"database not found: {db}"}

    # Read existing ledger IDs for idempotency
    existing_ids: set[str] = set()
    if ledger.exists():
        try:
            for line in ledger.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if "id" in rec:
                        existing_ids.add(str(rec["id"]))
                except Exception:
                    pass
        except Exception:
            pass  # an unreadable ledger is not a reason to lose new usage

    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, session_id, type, seq, time_created, time_updated, data "
            "FROM session_message WHERE type = 'assistant' ORDER BY id ASC"
        )
        rows = cur.fetchall()
    except sqlite3.Error as e:
        return {"ok": False, "added": 0, "total": 0, "skipped": 0, "error": str(e)}
    finally:
        if conn:
            conn.close()

    added = 0
    skipped = 0
    new_records: list[dict] = []

    for row in rows:
        msg_id = str(row["id"])
        if msg_id in existing_ids:
            skipped += 1
            continue

        data_raw = row["data"]
        if not data_raw:
            skipped += 1
            continue

        try:
            data = json.loads(data_raw)
        except Exception:
            skipped += 1
            continue

        model_info = data.get("model") or {}
        provider = str(model_info.get("providerID") or "")
        # opencode stores the id as `model.id`; older builds wrote `modelID`.
        model = str(model_info.get("modelID") or model_info.get("id") or "")
        variant = str(model_info.get("variant") or "")

        if not provider or not model:
            skipped += 1
            continue

        tokens = data.get("tokens") or {}
        if not tokens:
            skipped += 1
            continue

        cost_val = data.get("cost")
        try:
            cost = float(cost_val) if cost_val is not None else 0.0
        except Exception:
            cost = 0.0

        at_ms = int(data.get("time", {}).get("created") or row["time_created"] or 0)
        agent = str(data.get("agent") or "")

        rec = {
            "id": msg_id,
            "at": at_ms,
            "session": str(row["session_id"]),
            "agent": agent,
            "provider": provider,
            "model": model,
            "variant": variant,
            "input": int(tokens.get("input") or 0),
            "output": int(tokens.get("output") or 0),
            "reasoning": int(tokens.get("reasoning") or 0),
            "cache_read": int(((tokens.get("cache") or {}).get("read")) or 0),
            "cache_write": int(((tokens.get("cache") or {}).get("write")) or 0),
            "cost": cost,
        }
        new_records.append(rec)
        existing_ids.add(msg_id)
        added += 1

    if new_records:
        # Read current ledger, append new, keep newest `limit`
        all_lines: list[str] = []
        if ledger.exists():
            try:
                all_lines = [l.strip() for l in ledger.read_text().splitlines() if l.strip()]
            except Exception:
                all_lines = []
        for rec in new_records:
            all_lines.append(json.dumps(rec, separators=(",", ":")))
        if len(all_lines) > limit:
            all_lines = all_lines[-limit:]
        _atomic_write(ledger, all_lines)

    return {"ok": True, "added": added, "total": len(existing_ids), "skipped": skipped, "error": ""}


def records(ledger: Path | None = None) -> list[dict]:
    """Read all ledger records, tolerant of blank/bad lines."""
    ledger = ledger or ledger_path()
    out: list[dict] = []
    if not ledger.exists():
        return out
    try:
        for line in ledger.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict) and "id" in rec:
                    out.append(rec)
            except Exception:
                pass
    except Exception:
        pass
    return out


def summary(hours: int = 24, by: str = "provider", ledger: Path | None = None) -> dict:
    """Aggregate usage over the last `hours` hours, grouped by `by`."""
    ledger = ledger or ledger_path()
    now = int(time.time() * 1000)
    since = now - hours * 3600 * 1000

    rows = records(ledger)
    filtered = [r for r in rows if r.get("at", 0) >= since]

    groups: dict[str, dict] = {}
    for r in filtered:
        key = str(r.get(by) or "unknown")
        g = groups.get(key)
        if g is None:
            g = {"key": key, "calls": 0, "input": 0, "output": 0, "reasoning": 0,
                 "cache_read": 0, "cache_write": 0, "cost": 0.0, "last_at": 0}
            groups[key] = g
        g["calls"] += 1
        g["input"] += int(r.get("input") or 0)
        g["output"] += int(r.get("output") or 0)
        g["reasoning"] += int(r.get("reasoning") or 0)
        g["cache_read"] += int(r.get("cache_read") or 0)
        g["cache_write"] += int(r.get("cache_write") or 0)
        g["cost"] += float(r.get("cost") or 0.0)
        if r.get("at", 0) > g["last_at"]:
            g["last_at"] = r["at"]

    sorted_rows = sorted(groups.values(), key=lambda x: x["calls"], reverse=True)
    totals = {"calls": 0, "input": 0, "output": 0, "reasoning": 0,
              "cache_read": 0, "cache_write": 0, "cost": 0.0}
    for r in sorted_rows:
        for k in totals:
            totals[k] += r[k]

    return {
        "since": since,
        "hours": hours,
        "by": by,
        "rows": sorted_rows,
        "totals": totals,
    }


def quota_state(provider: str, now: int | None = None, ledger: Path | None = None) -> dict:
    """Rolling quota state for a provider from the ledger."""
    now = now or int(time.time() * 1000)
    ledger = ledger or ledger_path()
    limits = limits_for(provider)
    free = limits.get("free")

    calls_1m = 0
    calls_1h = 0
    calls_24h = 0
    rpm_limit = None
    rph_limit = None
    rpd_limit = None
    concurrency = None

    if free and isinstance(free, dict):
        rpm_limit = free.get("rpm")
        rph_limit = free.get("rph")
        rpd_limit = free.get("rpd")
        concurrency = free.get("concurrency")

    if ledger.exists():
        try:
            for line in ledger.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if str(rec.get("provider")) != provider:
                    continue
                at = int(rec.get("at") or 0)
                if at <= 0:
                    continue
                if now - at < 60_000:
                    calls_1m += 1
                if now - at < 3_600_000:
                    calls_1h += 1
                if now - at < 86_400_000:
                    calls_24h += 1
        except Exception:
            pass

    exhausted = False
    if rpm_limit is not None and calls_1m >= rpm_limit:
        exhausted = True
    if rph_limit is not None and calls_1h >= rph_limit:
        exhausted = True
    if rpd_limit is not None and calls_24h >= rpd_limit:
        exhausted = True

    remaining_day = None
    if rpd_limit is not None:
        remaining_day = max(0, rpd_limit - calls_24h)

    note = limits.get("note", "")
    if not limits.get("known", True):
        note = "no published limits"

    return {
        "provider": provider,
        "calls_1m": calls_1m,
        "calls_1h": calls_1h,
        "calls_24h": calls_24h,
        "limits": {
            "rpm": rpm_limit,
            "rph": rph_limit,
            "rpd": rpd_limit,
            "concurrency": concurrency,
        },
        "exhausted": exhausted,
        "remaining_day": remaining_day,
        "note": note,
    }


def quota_note(state: dict) -> str:
    """Human-readable quota note for CLI/plugin rows."""
    if not state.get("known", True):
        return "no published limits"
    limits = state.get("limits") or {}
    rpm = limits.get("rpm")
    rph = limits.get("rph")
    rpd = limits.get("rpd")
    c1m = state.get("calls_1m", 0)
    c1h = state.get("calls_1h", 0)
    c24h = state.get("calls_24h", 0)
    parts = []
    if rpm is not None:
        parts.append(f"{c1m}/{rpm} rpm")
    if rph is not None:
        parts.append(f"{c1h}/{rph} rph")
    if rpd is not None:
        parts.append(f"{c24h}/{rpd} rpd")
    if state.get("exhausted"):
        parts.append("EXHAUSTED")
    if not parts:
        return state.get("note", "no limits")
    return "; ".join(parts)


__all__ = [
    "db_path",
    "ledger_path",
    "limits_for",
    "ingest",
    "records",
    "summary",
    "quota_state",
    "quota_note",
]