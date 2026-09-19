import os
os.environ["HARNESS_MOCK"] = "1"

def test_keyless_never_empty_errs(tmp_path, monkeypatch):
    """With zero keys, ranked candidates must all be attemptable (anon free
    or local) so failures always carry an error trail, never a bare message."""
    for k in ("OPENROUTER_API_KEY", "KILO_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
              "NVIDIA_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    from harness import router
    from harness.router import PROVIDERS
    cfg = {}
    cs = router.candidates(router.parse_model("auto-fastest"), cfg)
    o = router.rank(cs, cfg, need_tools=True)
    assert o, "nothing rankable without keys"
    for c in o[:5]:
        meta = PROVIDERS[c["provider"]]
        keyless_ok = (not meta.get("env")) or bool(meta.get("optional_key") and c["model"].endswith(":free"))
        assert keyless_ok, f"unattemptable candidate would silently skip: {c}"
