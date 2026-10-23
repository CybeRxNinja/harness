"""Skill packs: import a verified pack, or draft one from this project's memory.

The host (opencode v2) already discovers `SKILL.md` under
`<project>/.opencode/skills` and advertises every description, so the useful
work here is placement and review:

* `install_pack()` copies a pack into that discoverable directory, refuses
  credentials/binaries/symlinks, records provenance, and is `--list`-able;
* `sources()` is the curated, license-checked set of packs worth importing;
* `draft_skill()` turns durable memory into a staged `skill-create` a human
  approves (the same pending queue as lessons).

Network is never touched: a "pack" here is a local directory in the shape a
GitHub repo would be.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from harness.skillpack import (
    AGENTS_FILE,
    SOURCE_FILE,
    SkillPackError,
    draft_skill,
    fetch_pack,
    imported,
    install_agents,
    install_pack,
    parse_spec,
    read_skill,
    sources,
    update_agents,
    update_installed,
)
from harness.skills import match


def _skill(name: str, desc: str, body: str = "## Procedure\n1. do the thing\n") -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n{body}"


def _agent(name: str, body: str = "You review code.", desc: str = "Reviews code. Use when reviewing.") -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n{body}"


@pytest.fixture()
def pack(tmp_path):
    """A repo-shaped pack: two good skills, two refusals, and noise."""
    root = tmp_path / "pack"
    (root / "skills" / "alpha" / "references").mkdir(parents=True)
    (root / "skills" / "alpha" / "SKILL.md").write_text(_skill("Alpha Skill", "Route alpha work; use when alpha"))
    (root / "skills" / "alpha" / "references" / "notes.md").write_text("# notes\n")
    (root / "skills" / "beta").mkdir(parents=True)
    (root / "skills" / "beta" / "SKILL.md").write_text(_skill("Beta Skill", "Beta checks for the beta path"))
    (root / "skills" / "secretive").mkdir(parents=True)
    (root / "skills" / "secretive" / "SKILL.md").write_text(
        _skill("Secretive", "Contains a token") + "\ntoken = ghp_abcdefghijklmnopqrstuvwxyz\n"
    )
    (root / "skills" / "no-description").mkdir(parents=True)
    (root / "skills" / "no-description" / "SKILL.md").write_text("---\nname: No Description\n---\nbody\n")
    (root / "agents").mkdir()
    (root / "agents" / "code-reviewer.md").write_text(_agent("code-reviewer"))
    (root / "agents" / "no-description.md").write_text("---\nname: no-description\n---\nbody\n")
    (root / "agents" / "secret.md").write_text(_agent("secret") + "\ntoken = ghp_abcdefghijklmnopqrstuvwxyz\n")
    (root / "node_modules" / "junk").mkdir(parents=True)
    (root / "node_modules" / "junk" / "SKILL.md").write_text(_skill("Junk", "never imported"))
    (root / ".hidden").mkdir()
    (root / ".hidden" / "SKILL.md").write_text(_skill("Hidden", "never imported"))
    return root


@pytest.fixture()
def project(tmp_path):
    return tmp_path / "project"


# --- specs ------------------------------------------------------------------

def test_parse_spec_forms(pack, tmp_path):
    repo = parse_spec("obra/superpowers")
    assert repo["kind"] == "github" and repo["owner"] == "obra" and repo["repo"] == "superpowers"
    url = parse_spec("https://github.com/obra/superpowers")
    assert url["repo"] == "superpowers" and url["ref"] == ""
    tree = parse_spec("https://github.com/obra/superpowers/tree/main/skills")
    assert tree["ref"] == "main" and tree["subpath"] == "skills"
    tagged = parse_spec("obra/superpowers@v1.2")
    assert tagged["ref"] == "v1.2"
    local = parse_spec(str(pack))
    assert local["kind"] == "local" and Path(local["path"]) == pack
    with pytest.raises(SkillPackError):
        parse_spec("not-a-repo")
    with pytest.raises(SkillPackError):
        parse_spec("a/b/c")
    with pytest.raises(SkillPackError):
        parse_spec("https://gitlab.com/a/b")


def test_curated_sources_include_the_named_packs():
    repos = {s["repo"] for s in sources()}
    for repo in ("obra/superpowers", "addyosmani/agent-skills", "morluto/rea",
                 "Shubhamsaboo/awesome-llm-apps", "K-Dense-AI/scientific-agent-skills"):
        assert repo in repos, repo
    # every entry says where its skills live, so `skills add alias` works
    assert all(s["path"] for s in sources())
    assert [s["alias"] for s in sources("superpowers")] == ["superpowers"]


# --- install ----------------------------------------------------------------

def test_install_copies_good_skills_and_reports_refusals(pack, project):
    rep = install_pack(str(pack), project, dry_run=True)
    assert rep["installed"] == 2 and not (project / ".opencode").exists(), "dry run must not write"

    rep = install_pack(str(pack), project)
    assert rep["installed"] == 2, rep
    actions = {s["id"]: s["action"] for s in rep["skills"]}
    assert actions["alpha"] == "installed" and actions["beta"] == "installed"
    assert actions["secretive"] == "skipped" and "credential" in next(
        s["reason"] for s in rep["skills"] if s["id"] == "secretive"
    )
    assert actions["no-description"] == "skipped"
    assert "junk" not in actions and "hidden" not in actions

    target = project / ".opencode" / "skills"
    assert (target / "alpha" / "SKILL.md").exists()
    assert (target / "alpha" / "references" / "notes.md").exists()
    assert not (target / "secretive").exists()
    # body copied verbatim: the pack owns the content, we only place it
    assert (target / "alpha" / "SKILL.md").read_text() == (pack / "skills" / "alpha" / "SKILL.md").read_text()
    prov = json.loads((target / "alpha" / SOURCE_FILE).read_text())
    assert prov["repo"] == str(pack.resolve()) and prov["files"]
    assert prov["path"] == "skills/alpha"


def test_a_doc_that_reads_a_secret_from_the_environment_is_not_refused(pack, project):
    """The refusal rule exists for leaked literals. A security skill's example
    (`secret: process.env.SESSION_SECRET`) is the opposite of a leak."""
    d = pack / "skills" / "env-docs"
    d.mkdir()
    (d / "SKILL.md").write_text(_skill("Env Docs", "Documents safe configuration"))
    (d / "patterns.md").write_text("secret: process.env.SESSION_SECRET  // from env\n")
    rep = install_pack(str(pack), project)
    actions = {s["id"]: s["action"] for s in rep["skills"]}
    assert actions["env-docs"] == "installed", rep
    assert (project / ".opencode/skills/env-docs/patterns.md").exists()
    assert actions["secretive"] == "skipped", "a literal token is still refused"


def test_existing_skill_is_left_alone_until_update(pack, project):
    assert install_pack(str(pack), project)["installed"] == 2
    again = install_pack(str(pack), project)
    assert again["installed"] == 0
    assert {s["action"] for s in again["skills"]} >= {"exists"}

    beta = pack / "skills" / "beta" / "SKILL.md"
    beta.write_text(_skill("Beta Skill", "Beta checks the UPDATED path"))
    upd = install_pack(str(pack), project, update=True)
    actions = {s["id"]: s["action"] for s in upd["skills"]}
    assert actions["beta"] == "updated" and actions["alpha"] == "unchanged", actions
    assert upd["installed"] == 1, "only the changed skill counts"
    assert "UPDATED" in (project / ".opencode/skills/beta/SKILL.md").read_text()


def test_only_and_path_select_what_gets_imported(pack, project):
    rep = install_pack(str(pack), project, only="beta")
    assert [s["id"] for s in rep["skills"] if s["action"] == "installed"] == ["beta"]
    assert not (project / ".opencode/skills/alpha").exists()
    with pytest.raises(SkillPackError):
        install_pack(str(pack), project, only="missing")


def test_only_finds_a_skill_past_the_import_cap(tmp_path, project):
    """`--only` is the documented way into a big pack, and a big pack is the
    reason the cap exists: filtering after the cap made the 41st skill
    unimportable ("--only matched nothing")."""
    root = tmp_path / "big"
    for i in range(45):
        d = root / "skills" / f"skill-{i:02d}"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_skill(f"Skill {i}", f"does skill {i}"))
    rep = install_pack(str(root), project, only="skill-44")
    assert [s["id"] for s in rep["skills"] if s["action"] == "installed"] == ["skill-44"]


def test_an_unfiltered_import_reports_what_it_left_behind(tmp_path, project):
    """A silent cap reads as "the whole pack is here"."""
    root = tmp_path / "big"
    for i in range(45):
        d = root / "skills" / f"skill-{i:02d}"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_skill(f"Skill {i}", f"does skill {i}"))
    rep = install_pack(str(root), project)
    assert rep["installed"] == 40
    assert rep["truncated"] is True


def test_duplicate_skill_ids_collide_loudly_instead_of_overwriting(tmp_path, project):
    """Two categories may each hold `same/SKILL.md`; installing both would
    silently replace the first with the second under one id."""
    root = tmp_path / "dup"
    for cat in ("alpha", "beta"):
        d = root / "skills" / cat / "same"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_skill("Same", f"from {cat}"))
    rep = install_pack(str(root), project)
    rows = [s for s in rep["skills"] if s["id"] == "same"]
    assert len(rows) == 2, rows
    assert rows[0]["action"] == "installed"
    assert rows[1]["action"] == "skipped" and "duplicate" in rows[1]["reason"]
    assert rep["installed"] == 1
    assert "from alpha" in (project / ".opencode/skills/same/SKILL.md").read_text()


def test_subpath_pins_the_skill_tree(pack, project):
    rep = install_pack(str(pack), project, path="skills")
    assert rep["installed"] == 2
    with pytest.raises(SkillPackError):
        install_pack(str(pack), project, path="nope", dry_run=True)


def test_global_install_uses_the_host_config_dir(pack, project, monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    rep = install_pack(str(pack), project, global_=True)
    assert Path(rep["target"]) == cfg / "opencode" / "skills"
    assert (cfg / "opencode" / "skills" / "alpha" / "SKILL.md").exists()
    assert not (project / ".opencode").exists()


def test_imported_skill_is_visible_to_harness_scan(pack, project):
    install_pack(str(pack), project)
    from harness.config import load_config
    from harness.skills import scan

    cfg, _ = load_config(project)
    names = {s["name"] for s in scan(project, cfg)}
    assert {"Alpha Skill", "Beta Skill"} <= names, names
    # the private overlay is still first for a same-named skill
    import harness.skills as S

    roots = [r[1] for r in S.skill_roots(project, cfg)]
    assert project / ".opencode" / "skills" in roots


def test_curated_alias_resolves_to_its_repo_and_path(pack, tmp_path, monkeypatch):
    """`skills add agent-skills` must work: an alias names a pack in the
    catalog, so it resolves to the curated owner/repo (and its skills
    subdirectory) before anything is fetched."""
    from harness import skillpack as SP

    seen = {}

    def fake_fetch(spec, cache_root, refresh=False, ref=""):
        seen["spec"] = spec
        return {"kind": "local", "root": str(pack), "path": str(pack),
                "repo": str(pack), "ref": "", "commit": ""}

    monkeypatch.setattr(SP, "fetch_pack", fake_fetch)
    rep = install_pack("agent-skills", tmp_path / "project")
    assert seen["spec"] == "addyosmani/agent-skills", seen
    assert rep["source"]["path"] == "skills", "the catalog knows where the skills live"
    assert rep["installed"] == 2


def test_read_skill_refuses_a_symlink(pack, tmp_path):
    linked = pack / "skills" / "alpha" / "link.md"
    outside = tmp_path / "outside.md"
    outside.write_text("secrets")
    linked.symlink_to(outside)
    info = read_skill(pack / "skills" / "alpha" / "SKILL.md")
    # symlinks are skipped, not followed: the skill still imports with the rest
    assert info["id"] == "alpha"
    assert all(rel != "link.md" for _p, rel, _d in info["files"])


# --- update ----------------------------------------------------------------

def test_update_installed_reapplies_upstream_drift(pack, project):
    install_pack(str(pack), project)
    # upstream moves on: one skill rewrites, one drops a file, one adds one
    (pack / "skills" / "beta" / "SKILL.md").write_text(_skill("Beta Skill", "Beta checks the UPDATED path"))
    (pack / "skills" / "alpha" / "references" / "notes.md").unlink()
    (pack / "skills" / "beta" / "notes.md").write_text("new upstream\n")

    dry = update_installed(str(project), dry_run=True)
    assert {s["id"] for s in dry["skills"] if s["action"] == "would update"} == {"alpha", "beta"}
    assert "UPDATED" not in (project / ".opencode/skills/beta/SKILL.md").read_text(), "dry run must not write"

    rep = update_installed(str(project))
    assert (rep["updated"], rep["unchanged"], rep["failed"]) == (2, 0, 0), rep
    assert "UPDATED" in (project / ".opencode/skills/beta/SKILL.md").read_text()
    assert not (project / ".opencode/skills/alpha/references/notes.md").exists(), "a file dropped upstream is removed"
    assert (project / ".opencode/skills/beta/notes.md").read_text() == "new upstream\n"
    # provenance records the files now present, so the next run diffs cleanly
    prov = json.loads((project / ".opencode/skills/beta" / SOURCE_FILE).read_text())
    assert set(prov["files"]) == {"SKILL.md", "notes.md"}

    again = update_installed(str(project))
    assert (again["updated"], again["unchanged"], again["failed"]) == (0, 2, 0), again


def test_update_never_touches_skills_it_does_not_manage(pack, project):
    install_pack(str(pack), project)
    target = project / ".opencode" / "skills"
    # a hand-written skill (no provenance) and an approved memory draft
    (target / "hand-made" / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
    (target / "hand-made" / "SKILL.md").write_text(_skill("Hand", "written by a human"))
    (target / "drafted").mkdir()
    (target / "drafted" / "SKILL.md").write_text(_skill("Draft", "from this project's memory"))
    (target / "drafted" / SOURCE_FILE).write_text(json.dumps({
        "repo": "harness-memory", "path": "", "files": ["SKILL.md"],
    }))
    before = (target / "hand-made" / "SKILL.md").read_text()

    rep = update_installed(str(project))
    managed = {s["id"]: s["action"] for s in rep["skills"]}
    assert managed.get("drafted") == "skipped" and "harness-memory" in next(
        s["reason"] for s in rep["skills"] if s["id"] == "drafted")
    assert "hand-made" not in managed, "a skill without provenance is not imported at all"
    assert rep["updated"] == 0
    assert (target / "hand-made" / "SKILL.md").read_text() == before
    # an installed pack skill that did not drift is reported, not rewritten
    assert managed["alpha"] == "unchanged" and managed["beta"] == "unchanged"


def test_update_reports_an_unresolvable_source(pack, project):
    install_pack(str(pack), project)
    # a provenance recorded by an older release: bare alias, not owner/repo
    prov = json.loads((project / ".opencode/skills/alpha" / SOURCE_FILE).read_text())
    prov["repo"] = "agent-skills"
    (project / ".opencode/skills/alpha" / SOURCE_FILE).write_text(json.dumps(prov))
    rep = update_installed(str(project))
    row = next(s for s in rep["skills"] if s["id"] == "alpha")
    assert row["action"] == "failed" and "owner/repo" in row["reason"]
    assert rep["failed"] == 1


def _git_repo(path: Path, files: dict[str, str]) -> Path:
    """A real git repo, so the cache/fetch path is exercised, not mocked."""
    path.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "v1"], cwd=path, check=True)
    return path


def _bump(path: Path, rel: str, text: str) -> None:
    (path / rel).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "v2"], cwd=path, check=True)


def _cached_clone(project: Path, upstream: Path, owner: str = "acme", repo: str = "pack") -> Path:
    """Whatever `fetch_pack` would have cloned last time."""
    dest = project / ".opencode" / "harness" / "skill-cache" / f"{owner}--{repo}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(upstream), str(dest)], check=True)
    return dest


def test_fetch_pack_pull_moves_a_cached_clone_to_the_remote_head(tmp_path, project):
    """`skills update` used to re-read the last clone: an upstream commit made
    after the import was invisible until `--refresh` re-cloned the pack."""
    upstream = _git_repo(tmp_path / "upstream", {"skills/alpha/SKILL.md": _skill("Alpha", "v1")})
    dest = _cached_clone(project, upstream)
    _bump(upstream, "skills/alpha/SKILL.md", _skill("Alpha", "v2"))
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest,
                            capture_output=True, text=True).stdout.strip()

    got = fetch_pack("acme/pack", dest.parent, pull=True)
    assert got["stale"] is False
    assert got["commit"] and got["commit"] != before
    assert "v2" in (Path(got["root"]) / "skills/alpha/SKILL.md").read_text()


def test_fetch_pack_pull_falls_back_to_the_cached_copy_when_offline(tmp_path, project):
    """An update must not destroy a working install because the network is down."""
    upstream = _git_repo(tmp_path / "upstream", {"skills/alpha/SKILL.md": _skill("Alpha", "cached")})
    dest = _cached_clone(project, upstream)
    subprocess.run(["git", "remote", "set-url", "origin", str(tmp_path / "gone")],
                   cwd=dest, check=True)
    got = fetch_pack("acme/pack", dest.parent, pull=True)
    assert got["stale"] is True
    assert "cached" in (Path(got["root"]) / "skills/alpha/SKILL.md").read_text()


def test_update_installed_pulls_before_diffing(tmp_path, project):
    """End to end: install from a cached clone, move upstream, update has to see it."""
    upstream = _git_repo(tmp_path / "upstream", {"skills/alpha/SKILL.md": _skill("Alpha", "v1")})
    _cached_clone(project, upstream)
    rep = install_pack("acme/pack", project)  # uses the cache: no network
    assert rep["source"]["repo"] == "acme/pack" and rep["installed"] == 1
    assert "v1" in (project / ".opencode/skills/alpha/SKILL.md").read_text()

    _bump(upstream, "skills/alpha/SKILL.md", _skill("Alpha", "v2"))
    upd = update_installed(str(project))
    assert (upd["updated"], upd["failed"]) == (1, 0), upd
    assert "v2" in (project / ".opencode/skills/alpha/SKILL.md").read_text()


def test_github_provenance_records_owner_slash_repo(pack, tmp_path, monkeypatch):
    """A GitHub import must remember enough to fetch it again (owner/repo),
    not just the bare name a printout would show."""
    # reuse the local pack as a fake cache-able repo by pointing fetch at it is
    # not possible (fetch clones), so assert the id helper directly
    from harness.skillpack import _source_id

    assert _source_id({"owner": "obra", "repo": "superpowers", "path": ""}) == "obra/superpowers"
    assert _source_id({"repo": "superpowers", "path": str(pack)}) == str(pack)


# --- agents -----------------------------------------------------------------

def test_install_agents_places_valid_agents_and_refuses_the_rest(pack, project):
    rep = install_agents(str(pack), project)
    assert rep["installed"] == 1, rep
    actions = {a["id"]: a["action"] for a in rep["agents"]}
    assert actions["code-reviewer"] == "installed"
    assert actions["no-description"] == "skipped" and actions["secret"] == "skipped"
    target = project / ".opencode" / "agents"
    assert (target / "code-reviewer.md").read_text() == (pack / "agents" / "code-reviewer.md").read_text()
    assert not (target / "secret.md").exists(), "a credential-shaped agent never lands"
    prov = json.loads((target / AGENTS_FILE).read_text())
    # Per-agent provenance: one repo for the directory let a second pack take
    # over the first pack's agents, so `skills update` looked in the wrong repo.
    assert prov["agents"]["code-reviewer"]["repo"] == str(pack.resolve())
    assert prov["agents"]["code-reviewer"]["path"] == "agents/code-reviewer.md"
    # the host discovers agents from that directory; the agent is plain markdown
    assert (target / "code-reviewer.md").read_text().startswith("---\nname: code-reviewer")


def test_update_agents_restores_upstream_and_is_idempotent(pack, project):
    install_agents(str(pack), project)
    (pack / "agents" / "code-reviewer.md").write_text(_agent("code-reviewer", "UPDATED"))
    dry = update_agents(str(project), dry_run=True)
    assert {a["id"] for a in dry["agents"] if a["action"] == "would update"} == {"code-reviewer"}
    assert "UPDATED" not in (project / ".opencode/agents/code-reviewer.md").read_text()

    rep = update_agents(str(project))
    assert (rep["updated"], rep["failed"]) == (1, 0), rep
    assert "UPDATED" in (project / ".opencode/agents/code-reviewer.md").read_text()
    again = update_agents(str(project))
    assert (again["unchanged"], again["updated"], again["failed"]) == (1, 0, 0), again


def test_two_packs_of_agents_keep_their_own_source(tmp_path, project):
    """One sidecar for every pack meant the second import claimed the first
    pack's agents, so `skills update` looked for them in the wrong repo."""
    def agent_pack(base: Path, names: dict[str, str]) -> Path:
        (base / "agents").mkdir(parents=True)
        for name, desc in names.items():
            (base / "agents" / f"{name}.md").write_text(_agent(name, desc=desc))
        return base

    a = agent_pack(tmp_path / "pack-a", {"code-reviewer": "Reviews code."})
    b = agent_pack(tmp_path / "pack-b", {"test-engineer": "Writes tests."})
    install_agents(str(a), project)
    install_agents(str(b), project)
    sidecar = json.loads((project / ".opencode" / "agents" / AGENTS_FILE).read_text())
    assert sidecar["agents"]["code-reviewer"]["repo"] == str(a.resolve())
    assert sidecar["agents"]["test-engineer"]["repo"] == str(b.resolve())

    (a / "agents" / "code-reviewer.md").write_text(_agent("code-reviewer", "A2"))
    (b / "agents" / "test-engineer.md").write_text(_agent("test-engineer", "B2"))
    rep = update_agents(str(project))
    assert {x["id"]: x["action"] for x in rep["agents"]} == {
        "code-reviewer": "updated", "test-engineer": "updated"}, rep
    assert rep["failed"] == 0
    assert "A2" in (project / ".opencode/agents/code-reviewer.md").read_text()
    assert "B2" in (project / ".opencode/agents/test-engineer.md").read_text()


def test_a_legacy_flat_agent_sidecar_is_upgraded_not_dropped(pack, project):
    """The first sidecar shape was {repo, agents: {id: path}}: keep it readable
    and rewrite it per-agent on the next sync."""
    install_agents(str(pack), project)
    target = project / ".opencode" / "agents"
    (target / AGENTS_FILE).write_text(json.dumps({
        "repo": str(pack.resolve()),
        "agents": {"code-reviewer": "agents/code-reviewer.md"},
    }))
    (pack / "agents" / "code-reviewer.md").write_text(_agent("code-reviewer", "MIGRATED"))
    rep = update_agents(str(project))
    assert {x["id"]: x["action"] for x in rep["agents"]} == {"code-reviewer": "updated"}
    assert "MIGRATED" in (target / "code-reviewer.md").read_text()
    prov = json.loads((target / AGENTS_FILE).read_text())
    assert prov["agents"]["code-reviewer"]["repo"] == str(pack.resolve())
    assert prov["agents"]["code-reviewer"]["path"] == "agents/code-reviewer.md"


def test_skills_update_covers_imported_agents_too(pack, project):
    install_pack(str(pack), project)
    install_agents(str(pack), project)
    (pack / "agents" / "code-reviewer.md").write_text(_agent("code-reviewer", "UPDATED"))
    rep = update_installed(str(project))
    rows = {r["id"]: r["action"] for r in rep["skills"]}
    assert rows["code-reviewer"] == "updated", rows
    assert rows["alpha"] == "unchanged" and rows["beta"] == "unchanged", rows
    assert rep["updated"] == 1, "only the agent drifted, and one command reports it"


def test_a_hand_edited_agent_is_never_clobbered_without_force(pack, project):
    install_agents(str(pack), project)
    dest = project / ".opencode" / "agents" / "code-reviewer.md"
    dest.write_text(dest.read_text() + "\nlocal tweak\n")
    (pack / "agents" / "code-reviewer.md").write_text(_agent("code-reviewer", "TWEAKED"))

    rep = install_agents(str(pack), project)
    row = next(a for a in rep["agents"] if a["id"] == "code-reviewer")
    assert row["action"] == "exists" and "--force" in row["reason"]
    assert "local tweak" in dest.read_text(), "the user's edit survives"

    forced = install_agents(str(pack), project, force=True)
    assert {a["id"]: a["action"] for a in forced["agents"]}["code-reviewer"] == "updated"
    assert "TWEAKED" in dest.read_text() and "local tweak" not in dest.read_text()
    # the sidecar now claims it, so a later update can manage it
    assert "code-reviewer" in json.loads((dest.parent / AGENTS_FILE).read_text())["agents"]

    # an OWNED agent is refreshed by --update, and still not by a bare re-run
    dest.write_text(dest.read_text() + "\nanother local edit\n")
    (pack / "agents" / "code-reviewer.md").write_text(_agent("code-reviewer", "AGAIN"))
    bare = install_agents(str(pack), project)
    assert {a["id"]: a["action"] for a in bare["agents"]}["code-reviewer"] == "exists"
    assert "another local edit" in dest.read_text()
    upd = install_agents(str(pack), project, update=True)
    assert {a["id"]: a["action"] for a in upd["agents"]}["code-reviewer"] == "updated"
    assert "AGAIN" in dest.read_text()


def test_agents_land_in_the_host_global_dir_with_global(pack, project, monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    rep = install_agents(str(pack), project, global_=True)
    assert Path(rep["target"]) == cfg / "opencode" / "agents"
    assert (cfg / "opencode" / "agents" / "code-reviewer.md").exists()


def test_install_agents_reports_a_pack_without_an_agents_dir(pack, project):
    (pack / "agents").rename(pack / "crew")
    with pytest.raises(SkillPackError, match="no agents directory"):
        install_agents(str(pack), project)


# --- matching ---------------------------------------------------------------

def test_match_ranks_a_name_hit_above_a_stray_description_term():
    items = [
        {"name": "ponytail", "description": "Laziness ladder for writing code"},
        {"name": "test-driven-development", "description": "Write the failing test first, then make it pass"},
        {"name": "git-workflow", "description": "Commits, branches and releases"},
    ]
    top = match(items, "add a failing test for the cart bug")
    assert top[0]["name"] == "test-driven-development", top
    assert match(items, "zebra quantum") == []


# --- draft from memory ------------------------------------------------------

def _seed_memory(root: Path, con):
    from harness.memory import save_fact

    mem = root / ".opencode" / "harness"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "MEMORY.md").write_text(
        "# MEMORY.md — durable facts\n"
        "- [index] keep the facts index as a fast path\n"
        "- [index] a missing index falls back to the LIKE scan\n"
    )
    save_fact(con, "Decision: keep the facts index as a fast path", "turn")
    save_fact(con, "Fixed: the facts index is optional, never required", "turn")
    save_fact(con, "Blocker: a stale facts index hides a row until the next write", "turn")
    save_fact(con, "Tests passed: 252 passed", "turn")


def test_draft_stages_a_reviewable_skill_from_memory(tmp_path):
    from harness.store import connect

    con = connect(tmp_path)
    try:
        _seed_memory(tmp_path, con)
        out = draft_skill(con, tmp_path, "facts-index", query="facts index")
        assert out["staged"] > 0, out
        kind, text = con.execute("SELECT kind, diff FROM pending WHERE id=?", (out["staged"],)).fetchone()
        assert kind == "skill-create"
        assert "name: facts-index" in text and "description: Use when facts index" in text
        assert "## Procedure" in text and "## Pitfalls" in text and "## Verification" in text
        # the marker is gone from the step text; the section already says what it is
        assert "keep the facts index as a fast path" in text
        assert "Decision:" not in text
    finally:
        con.close()


def test_draft_refuses_when_the_topic_has_no_evidence(tmp_path):
    from harness.store import connect

    con = connect(tmp_path)
    try:
        _seed_memory(tmp_path, con)
        out = draft_skill(con, tmp_path, "quantum-chemistry", query="quantum chemistry")
        assert out["staged"] == 0
        assert "not enough evidence" in out["reason"]
    finally:
        con.close()


def test_draft_dry_run_shows_text_without_staging(tmp_path):
    from harness.store import connect

    con = connect(tmp_path)
    try:
        _seed_memory(tmp_path, con)
        out = draft_skill(con, tmp_path, "facts-index", query="facts index", dry_run=True)
        assert out["text"].startswith("---\nname: facts-index")
        assert con.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 0
    finally:
        con.close()
