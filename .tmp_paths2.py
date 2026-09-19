from pathlib import Path

subs = [
    ("harness/kernel.py",
     "        from .paths import state_dir as _sd\n        self.store = _sd(self.root)",
     "        self.store = state_dir(self.root)"),
    ("harness/kernel.py",
     '        self.runs = _sd(self.root) / "runs"',
     '        self.runs = state_dir(self.root) / "runs"'),
    ("harness/checkpoints.py",
     "        from .paths import state_dir as _sd\n    dest = _sd(root)",
     "    dest = state_dir(root)"),
    ("harness/checkpoints.py",
     "        from .paths import state_dir as _sd2\n        cands = sorted((_sd2(root)",
     "        cands = sorted((state_dir(root)"),
]
for path, old, new in subs:
    p = Path(path)
    t = p.read_text()
    assert t.count(old) == 1, (path, old[:60])
    p.write_text(t.replace(old, new))
    print("ok", path)

# module-level imports
for path, anchor, imp in [
    ("harness/kernel.py", "from pathlib import Path", "from pathlib import Path\n\nfrom .paths import state_dir"),
    ("harness/checkpoints.py", "from pathlib import Path", "from pathlib import Path\n\nfrom .paths import state_dir"),
]:
    p = Path(path)
    t = p.read_text()
    assert t.count(anchor) == 1
    p.write_text(t.replace(anchor, imp))
    print("import ok", path)
