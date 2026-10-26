"""Skill discovery I/O: one read per file, bounded view, folded frontmatter."""
import os
from pathlib import Path

os.environ.setdefault("HARNESS_MOCK", "1")

CFG = {"skills": {"external_dirs": []}}


def _skill(root, name, body="body", desc="Use when testing"):
    path = root / ".agents" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}")
    return path


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path / "proj"


def test_scan_reads_each_skill_md_once(tmp_path, monkeypatch):
    root = _isolate(tmp_path, monkeypatch)
    paths = [_skill(root, "one"), _skill(root, "two")]
    from harness import skills as S
    orig = Path.read_text
    counts = {}

    def counting(self, *a, **k):
        counts[str(self)] = counts.get(str(self), 0) + 1
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting)
    rows = S.scan(root, CFG)
    assert [r["name"] for r in rows] == ["one", "two"]
    assert [counts.get(str(p)) for p in paths] == [1, 1]


def test_view_marks_a_truncated_body(tmp_path, monkeypatch):
    root = _isolate(tmp_path, monkeypatch)
    _skill(root, "big", "x" * 13000)
    from harness import skills as S
    body = S.view(root, CFG, "big")
    assert "[harness: truncated at 12000 chars" in body
    assert body.startswith("---\nname: big")


def test_frontmatter_folds_block_scalars(tmp_path, monkeypatch):
    root = _isolate(tmp_path, monkeypatch)
    path = root / ".agents" / "skills" / "folded" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: folded\ndescription: >-\n  Use when a pack\n"
                    "  writes a folded description.\n---\nbody")
    from harness import skills as S
    rows = {r["name"]: r for r in S.scan(root, CFG)}
    assert rows["folded"]["description"].startswith("Use when a pack writes a folded")
