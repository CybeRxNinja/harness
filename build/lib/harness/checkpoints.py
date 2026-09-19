"""Checkpoints: git diff preferred, shadow-copy fallback."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


def checkpoint(root: Path, touched: list[str] | None = None) -> str:
    dest = root / ".harness" / "shadow" / time.strftime("%Y%m%d-%H%M%S")
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


def restore(root: Path, snap: str) -> str:
    s = Path(snap)
    if not s.exists():
        # allow short id
        cands = sorted((root / ".harness" / "shadow").glob("*"))
        match = [c for c in cands if c.name.startswith(snap)]
        if not match:
            raise FileNotFoundError(f"no snapshot {snap}")
        s = match[-1]
    patch = s / "changes.patch"
    if patch.exists():
        r = subprocess.run(["git", "apply", str(patch)], cwd=root, capture_output=True, text=True, timeout=15)
        return r.stdout[-2000:] + r.stderr[-2000:] or "patched"
    return f"shadow at {s} (manual copy back; files: {[x.name for x in s.iterdir()][:10]})"
