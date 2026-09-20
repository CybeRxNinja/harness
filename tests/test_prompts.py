def test_fresh_history_drops_stale_system():
    from harness.loop import _fresh_history, SYSTEM
    hist = [
        {"role": "system", "content": "OLD MODE A"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "OLD COMPACT NOTE"},
        {"role": "assistant", "content": "ok"},
        {"role": "system", "content": "LATEST STATE"},
        {"role": "user", "content": "go"},
    ]
    out = _fresh_history(hist)
    systems = [m["content"] for m in out if m["role"] == "system"]
    assert systems == ["LATEST STATE"], systems
    assert [m["role"] for m in out] == ["user", "assistant", "system", "user"]
    assert _fresh_history([]) == []


def test_spawn_loads_skills(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    from harness.skills import ensure_seed_skills
    from harness.store import connect
    from harness.config import load_config
    from harness import rlm
    assert ensure_seed_skills()
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    cfg, _ = load_config(root)
    con = connect(root)
    h = rlm.spawn(con, cfg, root, "test skill load", name="sk",
                  category="quick", load_skills=["using-agent-skills"])
    time.sleep(4)
    text = (root / ".opencode" / "harness" / "workers" / h["rlm_child_id"] / "result.md").read_text()
    assert text, "worker produced no result"
    con.close()
