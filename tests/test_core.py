import json, os
os.environ["HARNESS_MOCK"] = "1"

def test_models_resolve(tmp_path, monkeypatch):
    from harness.models import resolve_model
    import pytest
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    assert resolve_model("acme/foo", {}) == "acme/foo"
    with pytest.raises(RuntimeError):
        resolve_model("tag:reasoning", {})  # legacy id, no user default configured

def test_config_layers(tmp_path, monkeypatch):
    from harness import config
    cfg, info = config.load_config(tmp_path)
    assert "budgets" in cfg
    try:
        config.set_value(tmp_path, "token", "x", "user")
        assert False
    except PermissionError:
        pass

def test_skill_discovery_does_not_create_state_dir(tmp_path, monkeypatch):
    # scan() only probes its candidate roots with .exists(); resolving a path
    # must not mkdir. state_dir() did, so merely LISTING skills left an
    # .opencode/ behind in every project a session touched.
    from harness import skills as S
    from harness.config import load_config
    from pathlib import Path
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    cfg, _ = load_config(root)
    S.scan(root, cfg)
    assert not (root / ".opencode").exists(), "a read must not mkdir"

def test_skills_scan(tmp_path, monkeypatch):
    from harness import skills as S
    from harness.config import load_config
    from pathlib import Path
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    assert "using-agent-skills" in S.ensure_seed_skills()
    cfg, _ = load_config(Path("."))
    names = [s["name"] for s in S.scan(Path("."), cfg)]
    assert "using-agent-skills" in names
