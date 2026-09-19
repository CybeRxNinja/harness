import os
os.environ["HARNESS_MOCK"] = "1"

def test_free_routing():
    from harness.router import candidates, parse_model, rank
    cs = candidates(parse_model("tag:free"), {})
    assert cs, "no free candidates"
    ors = [c for c in cs if c["provider"] == "openrouter"]
    assert ors and ors[0]["model"].endswith(":free"), ors[:2]
    # per-provider ids honored for group routing too
    g = [c for c in candidates(parse_model("free-coder"), {}) if c["provider"] == "openrouter"]
    assert g and g[0]["model"] == "qwen/qwen3.8-27b:free", g
    assert rank(cs, {})
