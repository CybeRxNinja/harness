"""Single source of truth for project-local state locations.

Everything project-local lives in <project>/.opencode/harness/ so one folder
holds sessions, memory, kernel, workers, checkpoints, plans and skills.
Global state stays in ~/.harness/ (token, router stats, global skills).
"""
from __future__ import annotations

from pathlib import Path

STATE_PARTS = (".opencode", "harness")
LEGACY = ".harness"


def state_dir(root: str | Path) -> Path:
    """<root>/.opencode/harness (created on demand)."""
    p = Path(root).resolve()
    for part in STATE_PARTS:
        p = p / part
    p.mkdir(parents=True, exist_ok=True)
    return p


def migrate_legacy(root: str | Path) -> str:
    """One-time move of <root>/.harness to the new location.

    Moves only when the target does not exist yet (never merges, never
    deletes user data). Returns what happened: moved|already|absent|both.
    """
    r = Path(root).resolve()
    legacy = r / LEGACY
    if not legacy.exists():
        return "absent"
    target = r.joinpath(*STATE_PARTS)
    if target.exists():
        return "both"
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(target)
    return "moved"
