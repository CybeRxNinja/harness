"""Orchestrator helpers: plan/execute over boulder.json + subagent waves.

The ledger is only ticked by evidence. `is_verification` / `verification_passed`
recognize a real verification command and its clean result, and `auto_check`
uses them to close the active plan's next box after a turn that proved itself
(the project's rule: the verification section is the acceptance for the
checkbox). A turn that merely says "done" closes nothing.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from .paths import state_dir

# Commands that actually test the work (as opposed to describing it).
VERIFY_RE = re.compile(
    r"^\s*(python3?\s+-m\s+)?(pytest|unittest|nose2|tox|nox)\b|\b(npm|pnpm|yarn|bun|deno)\s+(run\s+)?(test|check|lint|typecheck)\b"
    r"|\b(cargo|go|swift|dotnet)\s+test\b|\bruff\b|\bmypy\b|\btsc\b|\beslint\b"
    r"|\bpyright\b|\bmake\s+(test|check)\b|\bharness\s+doctor\b", re.I)

# A clean run: no failure marker, no non-zero exit.
FAIL_RE = re.compile(
    r"\bFAILED\b|\bTraceback\b|\b[1-9]\d*\s+(failed|error)\b|\bexit [1-9]|\bAssertionError\b"
    r"|\bcommand not found\b|\bImportError\b|\bModuleNotFoundError\b", re.I)


def boulder_path(root: Path) -> Path:
    return state_dir(root) / "boulder.json"


def load_boulder(root: Path) -> dict:
    p = boulder_path(root)
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"works": {}, "active_work_id": None}


def save_boulder(root: Path, data: dict) -> None:
    p = boulder_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def start_plan(root, title: str, items: list[str]) -> str:
    b = load_boulder(root)
    wid = "w_" + uuid.uuid4().hex[:6]
    plan_file = state_dir(root) / "plans" / f"{re.sub(r'[^a-z0-9-]+','-',title.lower())[:40]}.md"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    body = f"# {title}\n\n" + "\n".join(f"- [ ] {i+1}. {t}" for i, t in enumerate(items))
    plan_file.write_text(body + "\n")
    b["works"][wid] = {"title": title, "plan": str(plan_file), "status": "active",
                       "created": int(time.time())}
    b["active_work_id"] = wid
    save_boulder(root, b)
    return wid


def next_box(plan_text: str):
    m = re.search(r"- \[ \] (.+)", plan_text)
    return m.group(1) if m else None


def check_box(root: Path, plan_file: str, idx_hint: str = "") -> bool:
    p = Path(plan_file)
    if not p.exists():
        return False
    text = p.read_text()
    new, n = re.subn(r"- \[ \] ", "- [x] ", text, count=1)
    if n:
        p.write_text(new)
        ledger = state_dir(root) / "ledger.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a") as f:
            f.write(json.dumps({"ts": int(time.time()), "plan": plan_file, "checked": idx_hint[:120]}) + "\n")
        return True
    return False


def is_verification(cmd: str) -> bool:
    """True when a shell command is a test/lint/typecheck run."""
    return bool(VERIFY_RE.search(str(cmd or "")))


def verification_passed(output: str) -> bool:
    """True when a verification run's output shows a clean result."""
    return not FAIL_RE.search(str(output or ""))


def auto_check(root: Path, summary: str, verified: bool) -> str | None:
    """Tick the active plan's next box after a turn that verified its work.

    Returns the checked item, or None when there was nothing to tick, no active
    plan, or no verification evidence.
    """
    if not verified:
        return None
    b = load_boulder(root)
    wid = b.get("active_work_id")
    if not wid or wid not in (b.get("works") or {}):
        return None
    plan = (b["works"][wid] or {}).get("plan")
    if not plan:
        return None
    item = re.sub(r"\s+", " ", str(summary or "")).strip()[:120]
    return item if check_box(root, plan, item) else None
