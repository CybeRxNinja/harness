"""Tools: read/write/edit/glob/grep/shell/py + skill/config/memory views. All jailed."""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

HUNK = re.compile(r"^@@", re.M)


def _jail(root: Path, p: str) -> Path:
    q = (root / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
    if q != root and root not in q.parents:
        raise PermissionError(f"escapes root: {p}")
    return q


def read(root: Path, path: str, limit: int = 200) -> str:
    p = _jail(root, path)
    if p.is_dir():
        return "\n".join(sorted(x.name for x in p.iterdir()))[:4000]
    lines = p.read_text(errors="replace").splitlines()
    out = [f"{i+1:4d}# {ln}" for i, ln in enumerate(lines[:limit])]
    if len(lines) > limit:
        out.append(f"... ({len(lines)-limit} more lines)")
    return "\n".join(out)[:12000]


def write(root: Path, path: str, content: str) -> str:
    p = _jail(root, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if len(content) > 200_000:
        raise ValueError("file too large (>200k)")
    p.write_text(content)
    return f"wrote {len(content)} bytes to {path}"


def edit(root: Path, path: str, old: str, new: str, hash_id: str = "") -> str:
    """Hash-anchored edit: if hash_id given as 'LINE#ID', verify line content hash before apply."""
    p = _jail(root, path)
    text = p.read_text(errors="replace")
    if hash_id:
        try:
            lineno = int(hash_id.split("#")[0])
            lines = text.splitlines()
            cur = lines[lineno - 1] if 0 < lineno <= len(lines) else None
            import hashlib
            expect = hash_id.split("#")[1]
            got = hashlib.md5((cur or "").encode()).hexdigest()[:2].upper()
            if got != expect.upper():
                return f"REJECTED stale-line: line {lineno} changed (want {expect}, got {got}). Re-read first."
        except Exception as e:
            return f"REJECTED bad hash_id: {e}"
    if old not in text:
        return "REJECTED: old_string not found (no fuzzy match by design)"
    if text.count(old) > 1:
        return "REJECTED: old_string matches >1 location; add context"
    p.write_text(text.replace(old, new, 1))
    return "ok"


def glob(root: Path, pattern: str, limit: int = 50) -> list[str]:
    out: list[str] = []
    for dirpath, _, files in os.walk(root):
        if ".git" in dirpath or ".harness" in dirpath:
            continue
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), root)
            if fnmatch.fnmatch(rel, pattern):
                out.append(rel)
                if len(out) >= limit:
                    return out
    return out


def grep(root: Path, pattern: str, include: str = "*", limit: int = 40) -> list[str]:
    rx = re.compile(pattern)
    out: list[str] = []
    for dirpath, _, files in os.walk(root):
        if ".git" in dirpath or ".harness" in dirpath:
            continue
        for f in files:
            if not fnmatch.fnmatch(f, include):
                continue
            fp = Path(dirpath) / f
            try:
                if fp.stat().st_size > 300_000:
                    continue
                for i, line in enumerate(fp.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        out.append(f"{os.path.relpath(fp, root)}:{i}:{line[:200]}")
                        if len(out) >= limit:
                            return out
            except Exception:
                continue
    return out
