"""Full feature audit: opencode-backed model resolution, memory, skills,
config. MOCK mode, temp dirs."""
import os
os.environ["HARNESS_MOCK"] = "1"

import pytest


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))  # no user model: hermetic
    r = tmp_path / "proj"
    r.mkdir(parents=True, exist_ok=True)
    return r


@pytest.fixture()
def cfg(root):
    from harness.config import load_config
    cfg, _ = load_config(root)
    return cfg


def test_models_matrix(cfg, tmp_path, monkeypatch):
    # every agent inherits the user's opencode default; explicit pins pass through
    import json
    from harness import models as M
    cfgdir = tmp_path / "cfg"
    (cfgdir / "opencode").mkdir(parents=True)
    (cfgdir / "opencode" / "opencode.json").write_text(json.dumps({"model": "acme/workhorse"}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    assert M.resolve_model("", cfg) == "acme/workhorse"
    assert M.resolve_model("other/explicit", cfg) == "other/explicit"


def test_memory_loop(root):
    from harness.store import connect
    from harness import memory as M
    con = connect(root)
    M.save_fact(con, "audit fact gamma", "audit")
    assert any("gamma" in h["text"] for h in M.recall(con, "gamma"))
    pid = M.stage_lesson(con, "audit-lesson", "evidence blob", "gist")
    assert any(p["id"] == pid for p in M.list_pending(con))
    from harness.paths import state_dir
    assert M.approve(con, pid, state_dir(root) / "MEMORY.md")
    con.close()


def test_ponytail_skills_ship_with_the_ladder_intact(root, cfg):
    """The bundled ladder is the skill — a rewrite that drops a rung, or that
    loses the MIT attribution while copying someone else's text, is the failure
    mode worth locking."""
    from pathlib import Path
    from harness import skills as S
    S.ensure_seed_skills()
    names = {s["name"] for s in S.scan(Path("."), cfg)}
    assert {"ponytail", "ponytail-review", "ponytail-audit"} <= names, sorted(names)

    body = S.view(Path("."), cfg, "ponytail")
    for rung in ("Does this need to exist", "Already in this codebase", "Stdlib does it",
                 "Native platform feature", "Already-installed dependency",
                 "Can it be one line", "Only then"):
        assert rung in body, f"ladder rung missing: {rung}"
    assert "DietrichGebert/ponytail" in body and "MIT" in body
    low = body.lower()
    for never_cut in ("validation", "error handling", "security", "accessibility"):
        assert never_cut in low, never_cut
    assert "ponytail:" in body, "the corner-cut marker has to be documented"

    # both review skills must report the one metric and list, never apply
    for name, tag in (("ponytail-review", "delete:"), ("ponytail-audit", "stale:")):
        text = S.view(Path("."), cfg, name)
        assert "net: -" in text and tag in text, name
    # report-only, in each skill's own current wording: the cut is a later pass
    review = S.view(Path("."), cfg, "ponytail-review")
    assert "List, do not apply" in review and "separate pass" in review, review[-400:]
    audit = S.view(Path("."), cfg, "ponytail-audit")
    assert "Apply the cuts in a second pass" in audit, audit[-400:]
    assert "never delete and fix in the same step" in audit, audit[-400:]

    # the L0 index is one line per skill: descriptions ship flat (a folded
    # `description: >-` reads as the literal ">-" to any line-based parser) and
    # fit the 120-char cap scan() applies, tail intact.
    shown = {s["name"]: s["description"] for s in S.scan(Path("."), cfg)}
    for name in ("ponytail", "ponytail-review", "ponytail-audit"):
        md = next(Path("harness/data/skills").rglob(f"{name}/SKILL.md"))
        raw = next(l for l in md.read_text().splitlines()
                   if l.startswith("description:"))
        assert ">" not in raw and "|" not in raw, f"{name}: not one flat line: {raw}"
        desc = S._frontmatter(md)["description"]
        assert 0 < len(desc) <= 120, f"{name}: {len(desc)} chars, cap is 120"
        assert shown[name] == desc, f"{name}: skills_list would show a different description"

    notice = Path("harness/data/skills/NOTICE.md")
    assert notice.exists() and "MIT License" in notice.read_text()


def test_skills_progressive(root, cfg):
    from harness import skills as S
    from pathlib import Path as _P
    S.ensure_seed_skills()
    names = [s["name"] for s in S.scan(_P("."), cfg)]
    assert "using-agent-skills" in names
    assert "reverse-router" not in names  # disabled by default (opt-in sec pack)
    cfg2 = dict(cfg, skills={**cfg["skills"], "disabled": []})
    assert "reverse-router" in [s["name"] for s in S.scan(_P("."), cfg2)]
    body = S.view(_P("."), cfg, "using-agent-skills")
    assert "When to Use" in body


def test_seed_install_copies_whole_skill_dir_missing_only(root, tmp_path, monkeypatch):
    """SKILL.md is the entry point, not the whole skill.

    Copying only SKILL.md shipped a skill whose `references/` was a dangling
    path: the model is told to `skill_view` a file that was never installed.
    Every file in the skill dir must land — nested dirs and non-.md siblings
    included — and every one of them missing-only, so a re-run never clobbers
    what the user edited (and does restore what they deleted).
    """
    from harness import skills as S
    from pathlib import Path
    seed = tmp_path / "seed"
    demo = seed / "build" / "demo"
    (demo / "references" / "nested").mkdir(parents=True)
    (demo / "assets").mkdir()
    (demo / "SKILL.md").write_text(
        "---\nname: demo\ndescription: A demo seed.\n---\n# demo\n"
        "See references/notes.md.\n")
    (demo / "references" / "notes.md").write_text("NOTES BODY\n")
    (demo / "references" / "nested" / "deep.md").write_text("DEEP BODY\n")
    (demo / "assets" / "logo.svg").write_text("<svg/>\n")
    monkeypatch.setattr(S, "seed_source", lambda: seed)

    assert S.ensure_seed_skills() == ["demo"]
    dest = Path(os.environ["HARNESS_HOME"]) / "skills" / "build" / "demo"
    for rel, body in (("SKILL.md", "# demo"),
                      ("references/notes.md", "NOTES BODY"),
                      ("references/nested/deep.md", "DEEP BODY"),
                      ("assets/logo.svg", "<svg/>")):
        f = dest / rel
        assert f.is_file(), f"seed did not install {rel}"
        assert body in f.read_text(), rel

    # missing-only: edits survive, a deleted file comes back
    (dest / "SKILL.md").write_text("USER EDIT\n")
    (dest / "references" / "notes.md").write_text("USER EDIT\n")
    (dest / "references" / "nested" / "deep.md").unlink()
    assert S.ensure_seed_skills() == ["demo"]
    assert (dest / "SKILL.md").read_text() == "USER EDIT\n", "re-run overwrote a user edit"
    assert (dest / "references" / "notes.md").read_text() == "USER EDIT\n", "same for references/"
    assert (dest / "references" / "nested" / "deep.md").read_text() == "DEEP BODY\n", (
        "a missing file must be re-seeded — that is what missing-only means")


def test_frontmatter_folds_block_scalars_and_caps_descriptions(root, cfg, tmp_path):
    """A `description: >-` header used to read as the literal ">-" and every
    continuation line as a stray key, so a folded skill showed a one-character
    description in the L0 index. Block scalars fold into the key above them, the
    next real key survives, and the value scan() hands the index is capped."""
    from harness import skills as S
    d = tmp_path / "fm"
    d.mkdir()
    for i, header in enumerate((">-", ">", "|", "|-")):
        (d / f"s{i}.md").write_text(
            f"---\nname: s{i}\ndescription: {header}\n"
            "  first line of the description\n"
            "  second line, wrapped\n"
            "license: MIT\n---\n# body\n")
        fm = S._frontmatter(d / f"s{i}.md")
        assert fm["name"] == f"s{i}", (header, fm)
        assert fm["description"] == "first line of the description second line, wrapped", (header, fm)
        assert fm["license"] == "MIT", f"{header}: folded scalar swallowed the next key"
    quoted = d / "q.md"
    quoted.write_text('---\nname: q\ndescription: "quoted value"\n---\n# body\n')
    assert S._frontmatter(quoted)["description"] == "quoted value"

    # the index line is capped, and the cap is the only thing that shortens it
    long = root / ".agents" / "skills" / "longdesc"
    long.mkdir(parents=True)
    body = "x" * 200
    (long / "SKILL.md").write_text(f"---\nname: longdesc\ndescription: {body}\n---\n# long\n")
    got = {s["name"]: s["description"] for s in S.scan(root, cfg)}
    assert got["longdesc"] == body[:120], len(got["longdesc"])


def test_config_policy(root):
    from harness.config import load_config, get, set_value, redact
    cfg, _ = load_config(root)
    assert get(cfg, "budgets.max_parallel") == 2
    out = set_value(root, "budgets.max_parallel", 3, "user")
    assert "max_parallel" in out
    import pytest as _p
    with _p.raises(PermissionError):
        set_value(root, "token", "x", "user")
    with _p.raises(PermissionError):
        set_value(root, "router.api_key", "x", "user")
    assert "sk-abcdef123456" not in str(redact({"k": "sk-abcdef123456"}))


def test_every_bundled_skill_is_committable():
    """A bare `build/` in .gitignore also matched harness/data/skills/build/,
    so a skill added there was invisible to `git add` — it shipped in the
    package but never in the repo. Anchoring the rule fixes it; this keeps it
    fixed."""
    import subprocess
    from pathlib import Path
    skills = sorted(Path("harness/data/skills").rglob("SKILL.md"))
    assert len(skills) >= 14, len(skills)
    ignored = [str(p) for p in skills
               if subprocess.run(["git", "check-ignore", "-q", str(p)],
                                 capture_output=True).returncode == 0]
    assert not ignored, f"gitignored skills would never be committed: {ignored}"
