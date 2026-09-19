import os
os.environ["HARNESS_MOCK"] = "1"

def test_no_tools_flag_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    from harness import router
    cfg = {}
    router._record("openrouter", "m1", 100, False, "tag:x", tools=True, not_found=True)
    cs = [{"provider": "openrouter", "model": "m1", "quality": 0.9, "ctx": 1},
          {"provider": "openrouter", "model": "m2", "quality": 0.1, "ctx": 1}]
    assert [c["model"] for c in router.rank(cs, cfg, need_tools=True)] == ["m2"]
    assert len(router.rank(cs, cfg)) == 2  # unflagged path unaffected
    router._record("openrouter", "m1", 100, True, "tag:x", tools=True)
    assert [c["model"] for c in router.rank(cs, cfg, need_tools=True)] == ["m1", "m2"]
