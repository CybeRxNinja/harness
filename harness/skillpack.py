"""Skill packs: import proven packs, and draft one from this project's memory.

opencode already does the "automatic" half of skills: it discovers every
`SKILL.md` under `<project>/.opencode/skills` and `~/.config/opencode/skills`,
advertises each one (id + name + description) at every model step, and loads a
body only when the model asks for it. A skill in a location the host does not
watch is invisible no matter how good it is — so "use the right skill
automatically" is mostly a placement problem.

This module adds the missing halves:

- `install_pack()` — fetch a pack from GitHub (or use a local checkout), scan it
  for `SKILL.md`, refuse credentials/binaries/oversized files, and install each
  skill where the host discovers it, with provenance beside it.
- `sources()` — the curated packs worth importing (license + note + the
  subdirectory their skills live in).
- `draft_skill()` — turn this project's durable memory (MEMORY.md lessons and
  classified facts) into a staged `skill-create`, so a repeated procedure found
  in the work can become a skill without writing it from a blank page.

Imported skills are untrusted text: the importer copies only files (never
symlinks), refuses anything that looks like a credential, and records the repo,
commit and license next to the skill so a review can start from facts.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from .memory import is_secretish, normalize
from .paths import opencode_config_dir, state_file
from .skills import _frontmatter

SOURCE_FILE = ".harness-source.json"
MAX_SKILLS = 40
MAX_FILES = 40
MAX_FILE_BYTES = 200_000
MAX_SKILL_BYTES = 2_000_000
MAX_DEPTH = 8
CLONE_TIMEOUT = 120
EXCLUDED = {".git", "node_modules", "__pycache__"}
AGENTS_FILE = ".harness-agents.json"


class SkillPackError(RuntimeError):
    """A pack could not be read or installed; the message is user-facing."""


# ---------------------------------------------------------------------------
# Curated sources
# ---------------------------------------------------------------------------

def catalog_path() -> Path:
    """`harness/data/skill-sources.json` from the package, repo checkout, or tree."""
    try:
        from importlib.resources import files as _rf
        p = _rf("harness") / "data" / "skill-sources.json"
        if p.is_file():
            return Path(str(p))
    except Exception:
        pass
    for cand in (
        Path(__file__).resolve().parent / "data" / "skill-sources.json",
        Path(__file__).resolve().parent.parent / "harness" / "data" / "skill-sources.json",
    ):
        if cand.is_file():
            return cand
    return Path(__file__).resolve().parent / "data" / "skill-sources.json"


def load_sources() -> list[dict]:
    try:
        data = json.loads(catalog_path().read_text())
    except Exception:
        return []
    out = []
    for row in data.get("sources", []):
        if not isinstance(row, dict) or not row.get("repo"):
            continue
        out.append({
            "alias": str(row.get("alias", "")),
            "repo": str(row["repo"]),
            "path": str(row.get("path", "")),
            "license": str(row.get("license", "")),
            "note": str(row.get("note", "")),
        })
    return out


def find_source(spec: str) -> dict | None:
    """Match a curated entry by alias or repo (case-insensitive)."""
    want = str(spec or "").strip().lower()
    for row in load_sources():
        if want and want in (row["alias"].lower(), row["repo"].lower()):
            return row
    return None


def sources(query: str = "") -> list[dict]:
    """Curated packs, optionally filtered by a term in alias/repo/note."""
    q = normalize(query).lower()
    rows = load_sources()
    if q:
        rows = [r for r in rows if q in f"{r['alias']} {r['repo']} {r['note']}".lower()]
    return rows


# ---------------------------------------------------------------------------
# Specs: local dir | owner/repo[@ref] | github URL (optionally /tree/<ref>/<sub>)
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def parse_spec(spec: str, ref: str = "") -> dict:
    """Normalise a pack spec to {kind, path|owner/repo, ref, subpath}."""
    raw = str(spec or "").strip()
    if not raw:
        raise SkillPackError("skills add needs a repo (owner/repo), a GitHub URL, or a local directory")
    local = Path(raw.replace("~", str(Path.home()))).expanduser()
    if local.is_dir():
        return {"kind": "local", "path": str(local.resolve()), "repo": str(local.resolve()),
                "ref": "", "subpath": ""}
    if "://" in raw:
        u = urlsplit(raw)
        if u.netloc.lower() not in ("github.com", "www.github.com"):
            raise SkillPackError(f"unsupported host {u.netloc!r}: use a github.com repo or a local directory")
        parts = [p for p in u.path.split("/") if p]
        if len(parts) < 2:
            raise SkillPackError(f"not a repository URL: {raw}")
        owner, repo = parts[0], parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        use_ref, subpath = (u.fragment or ref), ""
        if len(parts) > 3 and parts[2] in ("tree", "blob"):
            use_ref = parts[3]
            subpath = "/".join(parts[4:])
        return _remote(owner, repo, use_ref, subpath)
    use_ref, left = ref, raw
    if "@" in raw and "://" not in raw:
        left, _, inline = raw.partition("@")
        if inline:
            use_ref = inline
    parts = [p for p in left.split("/") if p]
    if len(parts) != 2:
        raise SkillPackError(f"not a repo spec: {raw!r} (expected owner/repo, a GitHub URL, or a local directory)")
    return _remote(parts[0], parts[1], use_ref, "")


def _remote(owner: str, repo: str, ref: str, subpath: str) -> dict:
    for part in (owner, repo):
        if not _NAME_RE.match(part) or part in (".", ".."):
            raise SkillPackError(f"invalid repository name: {owner}/{repo}")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return {"kind": "github", "owner": owner, "repo": repo, "path": "",
            "ref": str(ref or ""), "subpath": str(subpath or "")}


def _git(args: list[str], cwd: Path | None = None) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=CLONE_TIMEOUT)
    except FileNotFoundError:
        raise SkillPackError("git is not installed — install git, or pass a local directory")
    except subprocess.TimeoutExpired:
        raise SkillPackError(f"git {args[0]} timed out after {CLONE_TIMEOUT}s")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise SkillPackError(f"git {args[0]} failed: {tail[-1][:240] if tail else 'unknown error'}")
    return r.stdout


def fetch_pack(spec: str, cache_root: Path, refresh: bool = False, ref: str = "") -> dict:
    """Resolve a spec to a local pack root; clones GitHub packs into the cache."""
    src = parse_spec(spec, ref)
    if src["kind"] == "local":
        return {**src, "root": src["path"], "commit": ""}
    dest = Path(cache_root) / f"{src['owner']}--{src['repo']}"
    if refresh and dest.exists():
        shutil.rmtree(dest)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--depth", "1", "--single-branch"]
        if src["ref"]:
            args += ["--branch", src["ref"]]
        args += [f"https://github.com/{src['owner']}/{src['repo']}.git", str(dest)]
        _git(args)
    commit = ""
    try:
        commit = _git(["rev-parse", "HEAD"], cwd=dest).strip()
    except SkillPackError:
        commit = ""
    return {**src, "root": str(dest), "commit": commit}


# ---------------------------------------------------------------------------
# Reading a pack
# ---------------------------------------------------------------------------

def _skill_id(name: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return out[:64]


def discover(root: str | Path, subpath: str = "") -> list[Path]:
    """Every SKILL.md under `subpath` (or the pack root), bounded and filtered."""
    base = Path(root).resolve()
    if subpath:
        target = (base / subpath).resolve()
        if target != base and base not in target.parents:
            raise SkillPackError(f"path escapes the pack: {subpath}")
        if not target.is_dir():
            raise SkillPackError(f"no such directory in the pack: {subpath}")
        base = target
    found: list[Path] = []
    for md in sorted(base.rglob("SKILL.md")):
        rel = md.relative_to(base)
        if any(part.startswith(".") or part in EXCLUDED for part in rel.parts):
            continue
        if len(rel.parts) > MAX_DEPTH:
            continue
        found.append(md)
        if len(found) >= MAX_SKILLS:
            break
    return found


def _skill_files(skill_dir: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    total = 0
    for p in sorted(skill_dir.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = p.relative_to(skill_dir)
        if any(part.startswith(".") or part in EXCLUDED for part in rel.parts):
            continue
        size = p.stat().st_size
        if size > MAX_FILE_BYTES:
            raise SkillPackError(f"{rel} is {size} bytes (cap {MAX_FILE_BYTES})")
        total += size
        if total > MAX_SKILL_BYTES:
            raise SkillPackError(f"skill is larger than {MAX_SKILL_BYTES} bytes")
        files.append((p, str(rel)))
        if len(files) > MAX_FILES:
            raise SkillPackError(f"more than {MAX_FILES} files")
    return files


def read_skill(md: Path) -> dict:
    """One pack skill as {id,name,description,files} or {skip: reason}."""
    fm = _frontmatter(md)
    sid = _skill_id(md.parent.name)
    if not sid:
        return {"skip": f"{md.parent.name!r} does not make a usable id"}
    description = " ".join(str(fm.get("description") or "").split())
    if not description:
        # The host only advertises skills that have a description, so installing
        # one would add a file the model can never be shown.
        return {"id": sid, "skip": "no description in frontmatter"}
    try:
        files = _skill_files(md.parent)
    except SkillPackError as e:
        return {"id": sid, "skip": str(e)}
    payload: list[tuple[Path, str, bytes]] = []
    for path, rel in files:
        try:
            data = path.read_bytes()
        except OSError as e:
            return {"id": sid, "skip": f"unreadable {rel}: {e}"}
        if b"\x00" in data[:4096]:
            return {"id": sid, "skip": f"{rel} is binary"}
        text = data.decode("utf-8", "replace")
        if is_secretish(text):
            return {"id": sid, "skip": f"{rel} looks like it contains a credential"}
        payload.append((path, rel, data))
    return {
        "id": sid,
        "name": str(fm.get("name") or md.parent.name),
        "description": description[:240],
        "dir": md.parent,
        "files": payload,
    }


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def _target_root(root: Path, global_: bool) -> Path:
    return (opencode_config_dir() if global_ else root / ".opencode") / "skills"


def install_root(root: str | Path, global_: bool = False) -> Path:
    """Where skills land — a directory the host itself discovers.

    Public because the CLI's `skills approve` needs the same answer: an
    approved draft written to harness' private overlay would never be
    advertised to the model, which is the one failure this module exists to
    prevent.
    """
    return _target_root(Path(root).resolve(), global_)


def _source_id(fetched: dict) -> str:
    """Provenance repo id: `owner/repo` for GitHub packs, the resolved path
    for local ones. The bare repo name is not enough to fetch again (and an
    alias like "agent-skills" is not a repo at all), so `skills update` can
    only re-resolve what this records."""
    owner = str(fetched.get("owner") or "")
    if owner:
        return f"{owner}/{fetched.get('repo', '')}"
    return str(fetched.get("path") or fetched.get("repo") or "")


def install_pack(spec: str, root: str | Path, *, path: str = "", ref: str = "",
                 global_: bool = False, only: str = "", force: bool = False,
                 update: bool = False, refresh: bool = False, dry_run: bool = False) -> dict:
    """Fetch, verify and install a pack. Returns a printable report."""
    root = Path(root).resolve()
    entry = find_source(spec)
    if entry:
        # A curated alias (`skills add agent-skills`) is a NAME for the pack,
        # not a repo spec: resolve it before parse_spec, or "not-a-repo" is
        # the answer for exactly the command the docs recommend.
        spec = entry["repo"]
        if not path:
            path = entry["path"]
    fetched = fetch_pack(spec, state_file(root, "skill-cache"), refresh=refresh, ref=ref)
    subpath = path or fetched.get("subpath") or ""
    found = discover(fetched["root"], subpath)
    if not found:
        raise SkillPackError(
            f"no SKILL.md found in {fetched['repo']}"
            + (f" under {subpath}" if subpath else "")
            + " — pass --path <subdir> if the pack nests its skills"
        )
    wanted = [w.strip().lower() for w in str(only or "").split(",") if w.strip()]
    target = _target_root(root, global_)
    src_id = _source_id(fetched)
    report = {
        "source": {"repo": src_id, "ref": fetched.get("ref", ""),
                   "commit": fetched.get("commit", ""), "path": subpath,
                   "license": (entry or {}).get("license", "")},
        "target": str(target),
        "dry_run": bool(dry_run),
        "skills": [],
    }
    matched = 0
    for md in found:
        info = read_skill(md)
        sid = info["id"]
        if wanted and sid not in wanted and str(info.get("name", "")).lower() not in wanted:
            continue
        matched += 1
        if info.get("skip"):
            report["skills"].append({"id": sid, "action": "skipped", "reason": info["skip"]})
            continue
        dest = target / sid
        exists = dest.exists()
        if exists and not (force or update):
            report["skills"].append({"id": sid, "name": info["name"], "description": info["description"],
                                     "action": "exists", "reason": "use --update to refresh it"})
            continue
        changed = True
        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            changed = False
            for _path, rel, data in info["files"]:
                out = dest / rel
                if out.exists() and out.read_bytes() == data:
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                changed = True
        if dry_run:
            action = "would update" if exists else "would install"
        elif not exists:
            action = "installed"
        else:
            action = "updated" if changed else "unchanged"
        if not dry_run and (changed or not exists):
            provenance = {
                "repo": src_id,
                "ref": fetched.get("ref", ""),
                "commit": fetched.get("commit", ""),
                "path": str(md.parent.relative_to(Path(fetched["root"]).resolve())),
                "license": (entry or {}).get("license", ""),
                "installed_at": int(time.time()),
                "files": [rel for _p, rel, _d in info["files"]],
            }
            (dest / SOURCE_FILE).write_text(json.dumps(provenance, indent=2) + "\n")
        report["skills"].append({"id": sid, "name": info["name"], "description": info["description"],
                                 "action": action})
    if wanted and not matched:
        ids = sorted({read_skill(md).get("id", "") for md in found} - {""})
        raise SkillPackError(f"--only matched nothing in this pack (available: {', '.join(ids[:20])})")
    report["installed"] = sum(
        1 for s in report["skills"]
        if s["action"] in ("installed", "updated", "would install", "would update")
    )
    return report


def agents_root(root: str | Path, global_: bool = False) -> Path:
    """Where opencode discovers agent markdown (host design): the project's
    `.opencode/agents`, or the user's `~/.config/opencode/agents`.

    The same rule as skills: an agent in a directory nothing watches is
    invisible, and packs ship theirs as plain `agents/*.md` with
    name/description frontmatter — which is exactly the shape opencode reads.
    """
    return (opencode_config_dir() if global_ else Path(root).resolve() / ".opencode") / "agents"


AGENT_MAX_BYTES = 100_000


def _read_agent(md: Path) -> dict:
    """One pack agent as {id, data} or {skip: reason}.

    Only the frontmatter is interpreted, the body is copied verbatim: an
    agent is a system prompt, and rewriting someone else's prompt during
    import is how a review stops matching what actually runs. A missing
    description is a refusal — opencode would never advertise the agent.
    """
    fm = _frontmatter(md)
    aid = _skill_id(fm.get("name") or md.stem)
    if not aid:
        return {"skip": f"{md.name} has no usable name"}
    description = " ".join(str(fm.get("description") or "").split())
    if not description:
        return {"skip": "no description in frontmatter (the host would not advertise it)"}
    try:
        data = md.read_bytes()
    except OSError as e:
        return {"skip": f"unreadable: {e}"}
    if md.is_symlink():
        return {"skip": "symlink"}
    if len(data) > AGENT_MAX_BYTES:
        return {"skip": f"{len(data)} bytes (cap {AGENT_MAX_BYTES})"}
    if b"\x00" in data[:4096]:
        return {"skip": "binary"}
    if is_secretish(data.decode("utf-8", "replace")):
        return {"skip": "looks like it contains a credential"}
    return {"id": aid, "data": data}


def install_agents(spec: str, root: str | Path, *, path: str = "agents", ref: str = "",
                   global_: bool = False, only: str = "", force: bool = False,
                   update: bool = False, refresh: bool = False, dry_run: bool = False) -> dict:
    """Import a pack's `agents/*.md` where opencode discovers agents.

    Companion to install_pack: same fetch (shared cache), same refusal rules,
    same provenance habit — one sidecar, `.harness-agents.json`, in the agents
    directory, so `skills update` can re-sync the agent prompts too. Ids are
    the frontmatter name, so an import never clobbers a `harness-*` agent
    (those are ours) unless the pack really defines that name.
    """
    root = Path(root).resolve()
    entry = find_source(spec)
    if entry:
        spec = entry["repo"]
    fetched = fetch_pack(spec, state_file(root, "skill-cache"), refresh=refresh, ref=ref)
    base = Path(str(fetched["root"])) / str(path or "agents")
    if not base.is_dir():
        raise SkillPackError(
            f"no agents directory in {fetched.get('repo') or spec}: expected {path or 'agents'}/")
    wanted = {w.strip().lower() for w in str(only or "").split(",") if w.strip()}
    target = agents_root(root, global_)
    sidecar = target / AGENTS_FILE
    existing: dict = {}
    if sidecar.is_file():
        try:
            existing = {k: v for k, v in json.loads(sidecar.read_text()).get("agents", {}).items()}
        except Exception:
            existing = {}
    report = {
        "source": {"repo": _source_id(fetched), "ref": fetched.get("ref", ""),
                   "commit": fetched.get("commit", ""), "path": str(path or "agents")},
        "target": str(target), "dry_run": bool(dry_run), "agents": [],
    }
    matched = 0
    prov = dict(existing)
    for md in sorted(base.glob("*.md")):
        info = _read_agent(md)
        aid = info.get("id", _skill_id(md.stem))
        if wanted and aid not in wanted:
            continue
        matched += 1
        if info.get("skip"):
            report["agents"].append({"id": aid, "action": "skipped", "reason": info["skip"]})
            continue
        dest = target / f"{aid}.md"
        data = info["data"]
        exists = dest.exists()
        if exists and dest.read_bytes() != data and not (force or update):
            # Same rule as install_pack for skills: an already-written file is
            # left alone until the caller says --update (refresh it) or
            # --force (adopt this content and own it from here on). A local
            # edit is never silently overwritten.
            reason = ("differs from the pack; use --update to refresh it or --force to adopt it"
                      if aid in existing else
                      "differs from the pack; use --force to adopt it")
            report["agents"].append({"id": aid, "action": "exists", "reason": reason})
            continue
        changed = (not exists) or dest.read_bytes() != data
        if not dry_run and changed:
            target.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        action = ("would install" if not exists else "would update") if dry_run else (
            "installed" if not exists else ("updated" if changed else "unchanged"))
        if changed or not exists:
            prov[aid] = str(md.relative_to(Path(str(fetched["root"])).resolve()))
        report["agents"].append({"id": aid, "action": action})
    if wanted and not matched:
        raise SkillPackError(f"--only matched no agents in {fetched.get('repo') or spec}")
    if not dry_run and any(a["action"] in ("installed", "updated") for a in report["agents"]):
        target.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({
            "repo": _source_id(fetched),
            "ref": str(fetched.get("ref") or ""),
            "commit": str(fetched.get("commit") or ""),
            "installed_at": int(time.time()),
            "agents": prov,
        }, indent=2) + "\n")
    report["installed"] = sum(
        1 for a in report["agents"]
        if a["action"] in ("installed", "updated", "would install", "would update"))
    return report


def update_agents(root: str | Path, *, global_: bool = False, dry_run: bool = False,
                  refresh: bool = False, packs: dict | None = None) -> dict:
    """Re-sync imported agents from their pack, the way update_installed does."""
    root = Path(root).resolve()
    target = agents_root(root, global_)
    sidecar = target / AGENTS_FILE
    report: dict = {"target": str(target), "agents": [], "updated": 0, "unchanged": 0,
                    "skipped": 0, "failed": 0}
    if not sidecar.is_file():
        return report
    try:
        data = json.loads(sidecar.read_text())
        agents_map = {str(k): str(v) for k, v in (data.get("agents") or {}).items()}
    except Exception as e:
        report["failed"] = 1
        report["agents"].append({"id": AGENTS_FILE, "action": "failed", "reason": str(e)[:120]})
        return report
    spec, ref = str(data.get("repo") or ""), str(data.get("ref") or "")

    def note(aid: str, action: str, reason: str = "") -> None:
        report["agents"].append({"id": aid, "action": action, "reason": reason, "kind": "agent"})
        bucket = {"failed": "failed", "skipped": "skipped", "updated": "updated",
                  "unchanged": "unchanged", "would update": "updated"}.get(action)
        if bucket:
            report[bucket] += 1

    cache = packs if packs is not None else {}
    pack = cache.get((spec, ref))
    if pack is None and spec:
        local = Path(spec).expanduser()
        if local.is_dir():
            pack = {"root": str(local.resolve()), "commit": ""}
        elif "/" in spec:
            try:
                pack = fetch_pack(f"{spec}@{ref}" if ref else spec,
                                  state_file(root, "skill-cache"), refresh=refresh)
            except SkillPackError as e:
                pack = {"error": str(e)}
        else:
            pack = {"error": f"{spec!r} is neither an owner/repo nor a local directory"}
        cache[(spec, ref)] = pack
    if pack is None:
        pack = {"error": "no provenance for the agents directory"}
    if pack.get("error"):
        for aid in agents_map:
            note(aid, "failed", str(pack["error"])[:120])
        return report
    changed = False
    for aid, rel in sorted(agents_map.items()):
        upstream = Path(str(pack["root"])) / rel
        dest = target / f"{aid}.md"
        if not upstream.is_file():
            note(aid, "failed", f"{rel} is gone upstream")
            continue
        blob = upstream.read_bytes()
        if dest.exists() and dest.read_bytes() == blob:
            note(aid, "unchanged")
            continue
        if not dry_run:
            dest.write_bytes(blob)
            changed = True
        note(aid, "would update" if dry_run else "updated")
    if changed and not dry_run:
        data["commit"] = str(pack.get("commit") or "")
        data["installed_at"] = int(time.time())
        sidecar.write_text(json.dumps(data, indent=2) + "\n")
    return report


def imported(root: str | Path, global_: bool = False) -> list[dict]:
    """Installed skills that carry provenance, as {id, dir, ...provenance}.

    Hand-written skills have no SOURCE_FILE and are therefore not managed:
    nothing here ever rewrites content that did not come from a pack.
    """
    target = _target_root(Path(root).resolve(), global_)
    out: list[dict] = []
    for src_file in sorted(target.glob(f"*/{SOURCE_FILE}")):
        try:
            prov = json.loads(src_file.read_text())
        except Exception:
            continue
        if isinstance(prov, dict):
            out.append({"id": src_file.parent.name, "dir": src_file.parent, **prov})
    return out


def update_installed(root: str | Path, *, global_: bool = False,
                     dry_run: bool = False, refresh: bool = False) -> dict:
    """Re-fetch the pack each imported skill came from and apply the diff.

    This is the "update" half of a pack: `skills add` places, `skills update`
    keeps the placement current. Each skill is matched back to its source via
    the provenance recorded at install time (repo, ref, path inside the pack),
    so 40 skills from one repo share one fetch. Only files the provenance
    lists are touched; a local edit to an imported skill is overwritten (that
    is what "update" means) and a file removed upstream is deleted here too,
    but never a file the pack did not claim. Drafted skills (repo
    "harness-memory") and untracked ones are reported, never guessed at.
    """
    report: dict = {
        "target": str(_target_root(Path(root).resolve(), global_)),
        "dry_run": bool(dry_run),
        "skills": [],
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
    }
    packs: dict[tuple[str, str], dict] = {}
    for row in imported(root, global_):
        sid = str(row.get("id", ""))
        spec = str(row.get("repo") or "")
        rel = str(row.get("path") or "")
        ref = str(row.get("ref") or "")

        def note(action: str, reason: str = "", **extra) -> None:
            report["skills"].append({"id": sid, "action": action, "reason": reason, **extra})
            bucket = {"failed": "failed", "skipped": "skipped",
                      "unchanged": "unchanged", "updated": "updated",
                      "would update": "updated"}.get(action)
            if bucket:
                report[bucket] += 1

        if spec.startswith("harness-"):
            note("skipped", f"drafted from this project's memory ({spec}), nothing upstream")
            continue
        if not spec or not rel:
            note("failed", "no usable provenance — re-import the skill to track its source")
            continue
        key = (spec, ref)
        if key not in packs:
            local = Path(spec).expanduser()
            if "/" not in spec and not local.is_dir():
                packs[key] = {"error": f"{spec!r} is neither an owner/repo nor a local directory"}
            elif local.is_dir():
                packs[key] = {"root": str(local.resolve()), "commit": ""}
            else:
                try:
                    packs[key] = fetch_pack(f"{spec}@{ref}" if ref else spec,
                                            state_file(root, "skill-cache"), refresh=refresh)
                except SkillPackError as e:
                    packs[key] = {"error": str(e)}
        pack = packs[key]
        if pack.get("error"):
            note("failed", str(pack["error"]))
            continue
        upstream = Path(str(pack["root"])) / rel
        if not (upstream / "SKILL.md").is_file():
            note("failed", f"{rel}/SKILL.md is gone upstream — the pack moved or the skill was dropped")
            continue
        info = read_skill(upstream / "SKILL.md")
        if info.get("skip"):
            note("failed", str(info["skip"]))
            continue
        dest = Path(row["dir"])
        new_files = [r for _p, r, _d in info["files"]]
        changed = [r for _p, r, _d in info["files"]
                   if not (dest / r).exists() or (dest / r).read_bytes() != _d]
        removed = [r for r in (str(f) for f in (row.get("files") or []))
                   if r not in new_files and (dest / r).is_file()]
        if not changed and not removed:
            note("unchanged")
            continue
        if not dry_run:
            for _p, rel_path, data in info["files"]:
                out = dest / rel_path
                if out.exists() and out.read_bytes() == data:
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
            for rel_path in removed:
                (dest / rel_path).unlink(missing_ok=True)
            prov = {k: v for k, v in row.items() if k not in ("id", "dir")}
            prov.update({"commit": pack.get("commit", ""),
                         "installed_at": int(time.time()), "files": new_files})
            (dest / SOURCE_FILE).write_text(json.dumps(prov, indent=2) + "\n")
        note("would update" if dry_run else "updated",
             f"{len(changed)} file(s) changed" + (f", {len(removed)} removed" if removed else ""),
             files=changed, removed=removed)
    # Imported agents ride along: same command, same fetch cache, same rules.
    agents = update_agents(root, global_=global_, dry_run=dry_run, refresh=refresh, packs=packs)
    report["skills"].extend(agents["agents"])
    for key in ("updated", "unchanged", "skipped", "failed"):
        report[key] += agents[key]
    return report


# ---------------------------------------------------------------------------
# Draft a skill from this project's memory
# ---------------------------------------------------------------------------

_FACT_KIND_PREFIXES = (
    ("decision", "procedure"), ("decided", "procedure"), ("root cause", "procedure"),
    ("fix", "procedure"), ("fixed", "procedure"), ("lesson", "procedure"),
    ("blocker", "pitfall"), ("blocked", "pitfall"), ("blocking", "pitfall"), ("error", "pitfall"),
    ("verified", "verification"), ("tests passed", "verification"), ("passed", "verification"),
)
_LESSON_RE = re.compile(r"^- \[([^\]]+)\]\s+(.+)$")


def _fact_kind(text: str) -> str:
    low = normalize(text).lower()
    if re.match(r"^(progress|done) \[", low):
        return "progress"
    if re.match(r"^blocked \[", low):
        return "pitfall"
    for prefix, bucket in _FACT_KIND_PREFIXES:
        if low.startswith(prefix):
            return bucket
    return "note"


def _skill_name(name: str) -> str:
    out = _skill_id(name)
    if not out:
        raise SkillPackError("draft needs a skill name (lowercase letters, digits, dashes)")
    return out


def _lesson_lines(root: Path) -> list[str]:
    path = Path(root) / ".opencode" / "harness" / "MEMORY.md"
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return []
    out: list[str] = []
    for line in text.splitlines():
        m = _LESSON_RE.match(line.strip())
        if m:
            out.append(m.group(2).strip())
    return out


def draft_skill(con, root: str | Path, name: str, query: str = "", limit: int = 12,
                dry_run: bool = False) -> dict:
    """Compose a SKILL.md from durable memory and stage it for approval.

    Nothing is installed here: a skill changes how every later turn behaves, so
    it goes through the same `pending` queue as lessons (`skills approve`).
    """
    from .memory import terms

    root = Path(root).resolve()
    sid = _skill_name(name)
    topic = normalize(query) or sid.replace("-", " ")
    words = terms(topic)
    want = {w.lower() for w in words}

    def relevant(text: str) -> bool:
        low = text.lower()
        return any(w in low for w in want) if want else True

    buckets: dict[str, list[str]] = {"procedure": [], "pitfall": [], "verification": []}
    seen: set[str] = set()
    lessons = [l for l in _lesson_lines(root) if relevant(l)]
    for line in lessons:
        key = line.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        buckets["procedure"].append(line)
    try:
        rows = con.execute("SELECT text, source FROM facts ORDER BY id DESC LIMIT 400").fetchall()
    except Exception:
        rows = []
    for text, _source in rows:
        line = normalize(str(text))
        kind = _fact_kind(line)
        if kind == "progress" or not relevant(line):
            continue
        # strip the writer/fact marker; the section already says what it is
        body = re.sub(r"^(decision|decided|root cause|fix|fixed|blocker|blocked|blocking|verified|lesson|note|error)\s*:\s*", "", line, flags=re.I)
        body = re.sub(r"\s+", " ", body).strip()
        key = body.lower()[:80]
        if len(body) < 20 or key in seen:
            continue
        seen.add(key)
        buckets[kind if kind in buckets else "procedure"].append(body)
        if sum(len(v) for v in buckets.values()) >= max(3, limit):
            break
    bodies = sum(len(v) for v in buckets.values())
    if bodies < 3:
        return {"staged": 0, "id": sid,
                "reason": f"not enough evidence for {sid!r}: {bodies} durable line(s) matched — keep working, or pass --query"}
    description = f"Use when {topic}. Built from this project's durable memory."
    title = sid.replace("-", " ").title()
    lines = [
        "---",
        f"name: {sid}",
        f"description: {description}",
        "license: MIT",
        "source: harness-memory",
        f"generated: {time.strftime('%Y-%m-%d')}",
        "---",
        f"# {title}",
        "## When to Use",
        description,
        "## Procedure",
    ]
    procedure = buckets["procedure"][:limit]
    lines += [f"{i}. {step}" for i, step in enumerate(procedure, 1)] or ["1. (no procedure captured yet)"]
    if buckets["pitfall"]:
        lines += ["## Pitfalls"] + [f"- {cut}" for cut in buckets["pitfall"][:limit]]
    if buckets["verification"]:
        lines += ["## Verification"] + [f"- {cut}" for cut in buckets["verification"][:limit]]
    else:
        lines += ["## Verification", "- Re-run the check that proved this procedure the first time."]
    text = "\n".join(lines) + "\n"
    if is_secretish(text):
        return {"staged": 0, "id": sid, "reason": "draft refused: it would contain a credential"}
    if dry_run:
        return {"staged": 0, "id": sid, "text": text, "lines": bodies}
    # Same evidence queue as lessons: a human approves what becomes a skill.
    cur = con.execute("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,?)",
                      ("skill-create", sid, text[:8000], description[:200], int(time.time())))
    con.commit()
    return {"staged": int(cur.lastrowid or 0), "id": sid, "lines": bodies, "text": text}
