"""Orchestrator helpers: plan/execute over boulder.json + subagent waves."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from .paths import state_dir


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


def classify(task: str) -> str:
    t = task.lower()
    if any(k in t for k in ("typo", "rename", "single file", "config")):
        return "quick"
    if any(k in t for k in ("frontend", "css", "ui ", "component")):
        return "visual"
    if any(k in t for k in ("doc", "readme", "prose")):
        return "writing"
    if any(k in t for k in ("race", "deadlock", "architec", "security", "crypto")):
        return "ultrabrain"
    if len(t) > 200 or "across" in t or "refactor" in t:
        return "deep"
    return "unspecified-low"
