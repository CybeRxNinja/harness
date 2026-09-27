"""Worker-state readers for `harness doctor`.

The headless pool/spawn/mailbox path was retired: live sessions route
subagents through opencode's native `task` tool (see the orchestrator agent in
harness-opencode.json), and the sidebar reads the `workers`/`waits` tables
directly. What remains here are the two read-only helpers doctor needs to
classify worker rows without importing the deleted machinery.
"""
from __future__ import annotations

TERMINAL = ("done", "error", "timeout")


def is_terminal(status: str) -> bool:
    """True when a worker has reached a status nothing will move it out of.

    `stale` is deliberately not terminal: it is a computed verdict for a row
    that still claims to be running.
    """
    return str(status) in TERMINAL


def _budgets(cfg: dict) -> dict:
    return (cfg.get("budgets", {}) or {})


def worker_timeout(cfg: dict) -> int:
    return int(_budgets(cfg).get("worker_timeout_s", 600))
