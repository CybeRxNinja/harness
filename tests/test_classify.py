import os
os.environ["HARNESS_MOCK"] = "1"

def test_classify_and_quarantine(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    from harness import router
    assert router.parse_params("nvidia/nemotron-3-ultra-550b-a55b:free") == (550.0, 55.0)
    assert router.parse_params("qwen/qwen3.8-27b:free") == (27.0, None)
    assert router.parse_params("poolside/laguna-s-2.1:free") == (None, None)
    assert router.bench_score("z-ai/glm-5.2:free") == 83
    assert router.bench_score("some-brand-new-xyz-1:free") is None
    card = router.classify("some-brand-new-xyz-1:free", None)
    assert card["verdict"] == "quarantine"
    ok, why = router.is_allowed("openrouter", "some-brand-new-xyz-1:free", {}, None)
    assert not ok and "approve" in why
    router.approve_model("openrouter", "some-brand-new-xyz-1:free")
    ok, why = router.is_allowed("openrouter", "some-brand-new-xyz-1:free", {}, None)
    assert ok and why == "approved"
    ok, _ = router.is_allowed("openrouter", "z-ai/glm-5.2:free", {}, None)
    assert ok
    cfg = {"router": {"new_model_policy": "allow"}}
    ok, _ = router.is_allowed("openrouter", "some-brand-new-xyz-1:free", cfg, None)
    assert ok


def test_infer_tags_quality():
    from harness.router import infer_tags, infer_quality
    assert "coding" in infer_tags("qwen/qwen3-coder:free")
    assert "free" in infer_tags("x:free")
    assert infer_quality("nvidia/nemotron-3-ultra-550b-a55b:free") >= 0.8
