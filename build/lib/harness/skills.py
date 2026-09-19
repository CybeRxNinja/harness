"""Skills manager: progressive disclosure L0/L1/L2, bundles, trust, skill_manage."""
from __future__ import annotations

import re
import time
from pathlib import Path

DANGEROUS = re.compile(r"(curl .*\| *sh|private[_-]?key|BEGIN RSA|exfil|\.env\b.*(send|post|upload))", re.I)


def skill_roots(project_root: Path, cfg: dict) -> list[tuple[str, Path, int]]:
    """(tier, path, precedence). project > local > external."""
    from .config import user_dir
    roots: list[tuple[str, Path, int]] = []
    # project tiers
    for cand in (project_root / ".harness" / "skills", project_root / ".agents" / "skills"):
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


def _skill_dir_for(project_root: Path, cfg: dict, name: str) -> Path:
    from .config import user_dir
    cdir = ((cfg.get("skills", {}) or {}).get("create_dir", "") or "").strip()
    base = Path(cdir).expanduser() if cdir else user_dir() / "skills"
    # find existing
    for s in scan(project_root, cfg):
        if s["name"] == name:
            return Path(s["path"]).parent
    return base / "general" / name


def manage(con, project_root: Path, cfg: dict, action: str, name: str, **kw) -> str:
    approval = (cfg.get("skills", {}) or {}).get("write_approval", True)
    if action == "create":
        content = kw.get("content", "")
        warn = _lint(content)
        if approval:
            con.execute("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,?)",
                        ("skill-create", name, content[:8000], f"create {name} {warn}"[:200], int(time.time())))
            con.commit()
            return f"staged (approval on). /skills approve. {warn}"
        d = _skill_dir_for(project_root, cfg, name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(content)
        return f"created {d} {warn}"
    if action == "patch":
        old, new = kw.get("old_string", ""), kw.get("new_string", "")
        d = _skill_dir_for(project_root, cfg, name)
        p = d / "SKILL.md"
        text = p.read_text(errors="replace")
        if old not in text:
            return "REJECTED: old_string not found"
        diff = f"-{old[:500]}\n+{new[:500]}"
        if approval:
            con.execute("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,?)",
                        ("skill-patch", name, diff, f"patch {name}"[:200], int(time.time())))
            con.commit()
            return "staged (approval on)"
        p.write_text(text.replace(old, new, 1))
        return "patched"
    if action == "delete":
        if approval:
            con.execute("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,?)",
                        ("skill-delete", name, "", f"delete {name}"[:200], int(time.time())))
            con.commit()
            return "staged (approval on)"
        import shutil
        shutil.rmtree(_skill_dir_for(project_root, cfg, name), ignore_errors=True)
        return "deleted"
    if action in ("write_file", "remove_file"):
        d = _skill_dir_for(project_root, cfg, name)
        fp = (d / kw.get("file_path", "")).resolve()
        if d.resolve() not in fp.parents:
            raise PermissionError("escapes skill dir")
        if action == "remove_file":
            fp.unlink(missing_ok=True)
            return "removed"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(kw.get("file_content", ""))
        return f"wrote {fp.name} {_lint(kw.get('file_content',''))}"
    raise ValueError(f"unknown action {action}")


def _lint(content: str) -> str:
    notes = []
    if len(re.findall(r"#\d+|\bPR-\d+\b", content)) > 5:
        notes.append("warn:incident-log-shape (keep rules, drop story)")
    if "TODO later" in content or "add tests later" in content:
        notes.append("warn:rationalization (tests are proof, not later)")
    return " ".join(notes)


def scan_security(text: str) -> str:
    m = DANGEROUS.search(text or "")
    return f"DANGEROUS: {m.group(0)[:80]}" if m else "clean"


def bundles(project_root: Path) -> dict:
    from .config import user_dir
    out: dict = {}
    for d in (user_dir() / "skill-bundles", project_root / ".harness" / "skill-bundles"):
        if d.exists():
            for y in d.glob("*.yaml"):
                try:
                    txt = y.read_text(errors="replace")
                    skills = re.findall(r"-\s*([\w-]+)", txt.split("skills:")[-1].split("instruction:")[0])
                    out[y.stem] = {"skills": skills[:5], "file": str(y)}
                except Exception:
                    continue
    return out
