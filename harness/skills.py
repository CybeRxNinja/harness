"""Skills: progressive disclosure L0/L1/L2 plus seed-skill install.

Live sessions discover procedures through the plugin's native skills_list /
skill_view tools, which read through scan()/view() here. Skill authoring
(manage), bundle loading (bundles) and the standalone security scanner
(scan_security) were retired with the headless path — review now happens in
live review subagents.
"""
from __future__ import annotations

from pathlib import Path

from .paths import state_file


def skill_roots(project_root: Path, cfg: dict) -> list[tuple[str, Path, int]]:
    """(tier, path, precedence). project > local > external."""
    from .config import user_dir
    roots: list[tuple[str, Path, int]] = []
    # project tiers
    # state_file, not state_dir: discovery only probes candidates with
    # .exists(), so resolving a path must not create .opencode/ as a side effect.
    for cand in (state_file(project_root, "skills"), project_root / ".agents" / "skills"):
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


_BLOCK_HEAD = {">", "|", ">-", ">+", "|-", "|+"}  # YAML block scalar headers
_QUOTED = "\"'"


def _frontmatter(p: Path) -> dict:
    """Frontmatter keys -> scalar values, one level deep.

    Block scalars (`>-`, `|`, ...) and quoted multi-line values are folded:
    continuation lines belong to the key above them, never a new key, so
    `description: >-` reads as its text instead of the literal `>-`.
    """
    try:
        text = p.read_text(errors="replace")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    try:
        head = text.split("---", 2)[1]
    except Exception:
        return {}
    out: dict[str, str] = {}
    key = None
    parts: list[str] = []
    quote = ""

    def flush() -> None:
        nonlocal key, parts, quote
        if key is None:
            return
        raw = "\n".join(parts).strip()
        if quote and len(raw) >= 2 and raw[0] == raw[-1] == quote:
            raw = raw[1:-1]
        out[key] = " ".join(raw.split())
        key, parts, quote = None, [], ""

    for raw_line in head.splitlines():
        line = raw_line.strip()
        if key is not None and (
            (not line and not parts)          # blank line inside a folded scalar
            or raw_line[:1].isspace()          # indented continuation
            or line in _BLOCK_HEAD                  # header of a nested scalar: ignore
        ):
            parts.append(line)
            continue
        flush()
        if not line or line.startswith("#"):
            continue
        k, sep, v = line.partition(":")
        if not sep:
            continue
        key, v = k.strip(), v.strip()
        parts = [] if v in _BLOCK_HEAD else ([v] if v else [])
        quote = v[:1] if v[:1] in _QUOTED else ""
    flush()
    return out


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


class SeedResult(list):
    """Installed skill names, plus the subset this run actually wrote.

    A plain `list` subclass so existing callers keep working (`in`, `==[...]`,
    `len`, indexing) while callers that REPORT get the truth: `new` is the
    short list of skills this run created or overwrote, so a re-run that wrote
    nothing can say "0 new" instead of counting the whole store.
    """

    def __init__(self, names: list[str], new: list[str]) -> None:
        super().__init__(names)
        self.new = new


def _install(src: Path, target: Path, update: bool) -> bool:
    """Write packaged content to `target`; True if it wrote anything.

    Missing-only by default, so a user edit is never clobbered. `update` is the
    explicit opt-in that also overwrites a stale file — the only way a seed
    installed by an older release ever reaches the store. Identical content is
    skipped either way, so a re-run reports 0 changed instead of rewriting the
    world and calling it news.
    """
    body = src.read_text(errors="replace")
    if not update and target.exists():
        return False
    if target.exists() and target.read_text(errors="replace") == body:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return True


def ensure_seed_skills(update: bool = False) -> SeedResult:
    """Install seed skills to the global dir, whole directory tree.

    SKILL.md is the entry point, not the whole skill: references/*.md and any
    other sibling file ship with it. Default is missing-only, so user edits
    survive but a seed installed by an older release never refreshes; pass
    `update=True` to overwrite it with the packaged content (the CLI exposes
    this as `harness setup --update-skills`). Returns every installed skill
    name, with `.new` naming the ones this run actually wrote.
    """
    from .config import user_dir
    src = seed_source()
    if src is None:
        return SeedResult([], [])
    dest = user_dir() / "skills"
    installed: list[str] = []
    written: list[str] = []
    for md in src.rglob("SKILL.md"):
        name = _frontmatter(md).get("name", md.parent.name)
        sdir = dest / md.parent.parent.name / md.parent.name
        touched = _install(md, sdir / "SKILL.md", update)
        for f in md.parent.rglob("*"):
            if not f.is_file() or f.name == "SKILL.md":
                continue
            touched |= _install(f, sdir / f.relative_to(md.parent), update)
        installed.append(name)
        if touched:
            written.append(name)
    return SeedResult(installed, written)
