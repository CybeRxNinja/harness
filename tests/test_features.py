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
        assert "apply nothing" in text or "applies no fixes" in text or "do not apply" in text

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
