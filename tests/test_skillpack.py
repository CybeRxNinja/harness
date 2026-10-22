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
from pathlib import Path

import pytest

from harness.skillpack import (
    SOURCE_FILE,
    SkillPackError,
    draft_skill,
    imported,
    install_pack,
    parse_spec,
    read_skill,
    sources,
    update_installed,
)
from harness.skills import match


def _skill(name: str, desc: str, body: str = "## Procedure\n1. do the thing\n") -> str:
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


def test_github_provenance_records_owner_slash_repo(pack, tmp_path, monkeypatch):
    """A GitHub import must remember enough to fetch it again (owner/repo),
    not just the bare name a printout would show."""
    # reuse the local pack as a fake cache-able repo by pointing fetch at it is
    # not possible (fetch clones), so assert the id helper directly
    from harness.skillpack import _source_id

    assert _source_id({"owner": "obra", "repo": "superpowers", "path": ""}) == "obra/superpowers"
    assert _source_id({"repo": "superpowers", "path": str(pack)}) == str(pack)


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
