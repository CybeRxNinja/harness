from harness.compress import rtk_lite, caveman_lite, stacked, FILTERS, should_compress


def test_filter_fixtures():
    for f in FILTERS:
        for t in f.get("tests", []):
            out, stats = rtk_lite(t["input"], command=t.get("command", ""))
            for needle in t["expect"]:
                assert needle in out, (f["id"], t["name"], needle, out[:200])


def test_never_larger():
    big = "line %d\n" * 1
    blob = "".join(f"line {i} output data {i}\n" for i in range(300))
    for intensity in ("minimal", "standard", "aggressive"):
        out, _ = rtk_lite(blob, command="ls", intensity=intensity)
        assert len(out) <= len(blob)
    out, _ = caveman_lite("word " * 2000)
    assert len(out) <= len("word " * 2000)


def test_errors_preserved():
    tb = "Traceback (most recent call last):\n" + "".join(
        f'  File "x.py", line {i}, in f\n    pass\n' for i in range(100)
    ) + "ValueError: bad value"
    out, _ = stacked(tb, command="pytest", intensity="aggressive")
    assert "ValueError: bad value" in out
    assert "Traceback" in out


def test_caveman_protects_code_and_paths():
    text = "please check this basically for me https://example.com/x?q=1 and `rm -rf /tmp/work` ok?"
    out, stats = caveman_lite(text)
    assert "https://example.com/x?q=1" in out
    assert "`rm -rf /tmp/work`" in out
    assert "basically" not in out
    assert stats["saved_pct"] >= 0


def test_stacked_stats_shape():
    out, stats = stacked("hello world\n" * 50, command="echo hi")
    assert set(stats) >= {"engines", "in_chars", "out_chars", "saved_pct"}
    assert stats["engines"] == ["rtk_lite", "caveman_lite"]


def test_threshold_gate():
    assert not should_compress(100)
    assert should_compress(4000)
    assert should_compress(5000, threshold=5000)
