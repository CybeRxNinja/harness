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
    assert g and g[0]["model"] == "cohere/north-mini-code:free", g
    r = [c for c in candidates(parse_model("free-giant"), {}) if c["provider"] == "openrouter"]
    assert r and r[0]["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free", r
    assert rank(cs, {})
