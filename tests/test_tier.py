import os
os.environ["HARNESS_MOCK"] = "1"

def test_free_tier_filters_paid(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    # tier lookup hits network (stubbed here): opt out of MOCK for this call
    monkeypatch.setenv("HARNESS_MOCK", "0")
    from harness import router
    import json, urllib.request
    # stub the tier endpoint: free-tier key
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"data": {"is_free_tier": True}}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: R())
    assert router._openrouter_tier() == "free"
    cs = router.candidates(router.parse_model("tag:coding"), {})
    ors = [c for c in cs if c["provider"] == "openrouter"]
    assert ors and all(c["model"].endswith(":free") for c in ors), ors[:3]
    # pins bypass the filter by design
    p = router.candidates(router.parse_model("openrouter/anything"), {})
    assert p and p[0]["model"] == "anything"
