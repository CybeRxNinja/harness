import json
import os
os.environ["HARNESS_MOCK"] = "1"


def _user_model_home(tmp_path, monkeypatch, model="acme/bar-1"):
    cfgdir = tmp_path / "cfg"
    (cfgdir / "opencode").mkdir(parents=True)
    (cfgdir / "opencode" / "opencode.json").write_text(json.dumps({"model": model}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    return model


def test_resolve_explicit_passthrough(tmp_path, monkeypatch):
    _user_model_home(tmp_path, monkeypatch)
    from harness import models as M
    assert M.resolve_model("acme/other", {}) == "acme/other"


def test_resolve_bare_falls_back_to_user_default(tmp_path, monkeypatch):
    want = _user_model_home(tmp_path, monkeypatch, "acme/bar-1")
    from harness import models as M
    assert M.resolve_model("", {}) == want
    assert M.resolve_model("tag:reasoning", {}) == want  # legacy router id
    assert M.resolve_model("auto-fastest", {}) == want


def test_resolve_profile_with_slash(tmp_path, monkeypatch):
    _user_model_home(tmp_path, monkeypatch)
    from harness import models as M
    assert M.resolve_model("", {"model_profile": "acme/via-env"}) == "acme/via-env"


def test_resolve_missing_raises(tmp_path, monkeypatch):
    cfgdir = tmp_path / "empty"
    cfgdir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    from harness import models as M
    import pytest
    with pytest.raises(RuntimeError):
        M.resolve_model("", {})
