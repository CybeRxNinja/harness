"""Skills: progressive disclosure L0/L1/L2 plus seed-skill install.

Live sessions discover procedures through the plugin's native skills_list /
skill_view tools, which read through scan()/view() here. Skill authoring
(manage), bundle loading (bundles) and the standalone security scanner
(scan_security) were retired with the headless path — review now happens in
live review subagents.
"""
from __future__ import annotations

from pathlib import Path

from .paths import state_dir


def skill_roots(project_root: Path, cfg: dict) -> list[tuple[str, Path, int]]:
    """(tier, path, precedence). project > local > external."""
    from .config import user_dir
    roots: list[tuple[str, Path, int]] = []
    # project tiers
    for cand in (state_dir(project_root) / "skills", project_root / ".agents" / "skills"):
        if cand.exists():
            roots.append(("project", cand, 0))
    local = user_dir() / "skills"
    if local.exists():
        roots.append(("local", local, 1))
    for d in ((cfg.get("skills", {}) or {}).get("external_dirs", []) or []):
        p = Path(str(d).replace("~", str(Path.home()))).expanduser()
        if p.exists():
            roots.append(("external", p, 2))
    cdir = ((cfg.get("skills", {}) or {}).get("create_dir", "") or "").strip()
    if cdir:
        p = Path(cdir.replace("~", str(Path.home()))).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        if not any(r[1] == p for r in roots):
            roots.append(("local", p, 1))
    return roots


def scan(project_root: Path, cfg: dict) -> list[dict]:
    out: dict[str, dict] = {}
    disabled = set((cfg.get("skills", {}) or {}).get("disabled", []))
    import fnmatch
    for tier, root, prec in skill_roots(project_root, cfg):
        for skill_md in root.rglob("SKILL.md"):
            name = _frontmatter(skill_md).get("name", skill_md.parent.name)
            if any(fnmatch.fnmatch(name, pat) for pat in disabled):
                continue
            if name in out:
                continue  # higher precedence already won
            fm = _frontmatter(skill_md)
            out[name] = {"name": name, "description": fm.get("description", "")[:120],
                         "tier": tier, "path": str(skill_md), "category": skill_md.parent.parent.name}
    return sorted(out.values(), key=lambda d: d["name"])


def _frontmatter(p: Path) -> dict:
    try:
        text = p.read_text(errors="replace")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    try:
        head = text.split("---", 2)[1]
        out = {}
        for line in head.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip().strip("\"'")
        return out
    except Exception:
        return {}


def view(project_root: Path, cfg: dict, name: str, subpath: str = "") -> str:
    for s in scan(project_root, cfg):
        if s["name"] == name:
            base = Path(s["path"]).parent
            target = (base / subpath) if subpath else base / "SKILL.md"
            target = target.resolve()
            if base.resolve() not in target.parents and target != base.resolve():
                raise PermissionError("reference escapes skill dir")
            return target.read_text(errors="replace")[:12000]
    # did-you-mean
    names = [s["name"] for s in scan(project_root, cfg)]
    sug = [n for n in names if name[:3].lower() in n.lower()][:3]
    raise FileNotFoundError(f"skill {name!r} not found. did-you-mean: {sug}")


def seed_source() -> Path | None:
    """Canonical seed skills: installed package data first, repo tree fallback."""
    try:
        from importlib.resources import files as _rf
        p = _rf("harness") / "data" / "skills"
        if p.is_dir():
            return Path(str(p))
    except Exception:
        pass
    for cand in (Path(__file__).resolve().parent / "data" / "skills",
                 Path(__file__).resolve().parent.parent / ".opencode" / "harness" / "skills"):
        if cand.is_dir():
            return cand
    return None


def ensure_seed_skills() -> list[str]:
    """Copy missing seed skills to the global dir. Never overwrites user edits."""
    from .config import user_dir
    src = seed_source()
    if src is None:
        return []
    dest = user_dir() / "skills"
    installed = []
    for md in src.rglob("SKILL.md"):
        name = _frontmatter(md).get("name", md.parent.name)
        target = dest / md.parent.parent.name / md.parent.name / "SKILL.md"
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(md.read_text(errors="replace"))
        installed.append(name)
    return installed
