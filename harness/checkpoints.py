"""Checkpoints: git diff preferred, shadow-copy fallback."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .paths import state_dir


def checkpoint(root: Path, touched: list[str] | None = None) -> str:
    dest = state_dir(root) / "shadow" / time.strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    # try git diff
    try:
        diff = subprocess.run(["git", "diff"], cwd=root, capture_output=True, text=True, timeout=10)
        if diff.returncode == 0 and diff.stdout.strip():
            (dest / "changes.patch").write_text(diff.stdout)
            return str(dest)
    except Exception:
        pass
    for t in touched or []:
        try:
            src = (root / t).resolve()
            if root in src.parents or src == root:
                target = dest / Path(t).name
                if src.is_file():
                    shutil.copy2(src, target)
        except Exception:
            continue
    (dest / "note.txt").write_text("shadow copy (no git diff available)\n")
    return str(dest)


def has_content(snap: str | Path) -> bool:
    """True when a snapshot actually captured something restorable.

    Without a git diff and without touched files a snapshot holds only its note
    file — reporting that path as a checkpoint would claim safety it does not
    have.
    """
    d = Path(snap)
    return (d / "changes.patch").exists() or any(p.name != "note.txt" for p in d.iterdir())


def _patch_paths(patch: Path) -> list[str]:
    """Repo-relative paths a `git diff` patch touches."""
    out: list[str] = []
    for line in patch.read_text(errors="replace").splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1].strip()
            if path and path != "/dev/null":
                out.append(path)
    return out


def restore(root: Path, snap: str) -> str:
    """Put the project back to the state the snapshot captured.

    A snapshot is `HEAD + the changes present when it was taken`, so restoring
    means: reset the touched files to HEAD, then re-apply the snapshot patch.
    Applying the patch forward onto a mutated tree fails with "patch does not
    apply" — the old code returned that error text and exited 0, so a restore
    could silently do nothing.
    """
    s = Path(snap)
    if not s.exists():
        # allow short id
        cands = sorted((state_dir(root) / "shadow").glob("*"))
        match = [c for c in cands if c.name.startswith(snap)]
        if not match:
            raise FileNotFoundError(f"no snapshot {snap}")
        s = match[-1]
    patch = s / "changes.patch"
    if not patch.exists():
        files = [x.name for x in s.iterdir()]
        raise RuntimeError(
            f"snapshot {s.name} has no patch (nothing was captured); files: {files[:10]}")
    paths = _patch_paths(patch)
    if paths:
        subprocess.run(["git", "checkout", "HEAD", "--", *paths],
                       cwd=root, capture_output=True, text=True, timeout=15)
    applied = subprocess.run(["git", "apply", str(patch)],
                             cwd=root, capture_output=True, text=True, timeout=15)
    if applied.returncode != 0:
        raise RuntimeError(f"restore failed: {applied.stderr.strip()[:300] or 'git apply error'}")
    return f"restored {len(paths) or 1} file(s) from {s.name}"
