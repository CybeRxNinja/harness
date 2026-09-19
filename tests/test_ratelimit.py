import os
os.environ["HARNESS_MOCK"] = "1"

def test_retry_after_parse():
    from harness.router import _retry_after_s
    class E(Exception):
        def __init__(self, h): self.headers = h
    assert _retry_after_s(E({"Retry-After": "120"})) == 120
    assert _retry_after_s(E({"retry-after": "9999"})) == 300  # capped
    assert _retry_after_s(E({})) == 0
    assert _retry_after_s(Exception("429 boom")) == 0


def test_rate_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    from harness import router
    import time
    router._record("openrouter", "m9", 100, False, "tag:x", rate_limited=120)
    cs = [{"provider": "openrouter", "model": "m9", "quality": 0.99, "ctx": 1},
          {"provider": "openrouter", "model": "m8", "quality": 0.01, "ctx": 1}]
    assert [c["model"] for c in router.rank(cs, {})] == ["m8"]
    assert router._load_stats()["openrouter|m9"]["cooldown_until"] > time.time() + 100
