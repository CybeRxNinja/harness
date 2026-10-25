"""Scratch confinement guards: the suite must never touch the system temp.

Two regressions are pinned here, both of them real:
  * the FEATURES fixture used node's tmpdir() -> 54 unmanaged
    `harness-plugin-*` dirs accumulated in /tmp;
  * pytest's tmp_path defaulted to /tmp/pytest-of-<user>.
Everything now lands in <project>/.opencode/harness/tmp/, reaped by
tests/conftest.py.
"""
import conftest
import re
import shutil
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / ".opencode" / "harness" / "tmp"


def test_pytest_temp_is_confined_to_the_project(tmp_path):
    """conftest pins tempfile AND basetemp; both must resolve in-project."""
    assert tempfile.gettempdir() == str(SCRATCH), tempfile.gettempdir()
    assert str(tmp_path).startswith(str(SCRATCH) + "/"), tmp_path


def test_no_test_script_creates_scratch_in_the_system_temp():
    """No bun fixture may ask node for the system temp dir again.

    The FEATURES fixture used to build its mkdtemp paths on node's tmpdir,
    creating dirs nobody reaped — the runner had no idea they existed.
    Fixture dirs now go through the tracked `fixture()` helper (project-local,
    removed by the script's reaper).
    """
    # One pattern, spelled so this file's own source cannot match it: either
    # the tmpdir() call itself or the node import that supplies it.
    escape = re.compile(
        r"mkdtempSync\(\s*join\(\s*tmpdir\("
        r"|import\s*\{[^}]*\btmpdir\b[^}]*\}\s*from\s*\"node:os\""
    )
    offenders = [p.name for p in sorted((REPO_ROOT / "tests").glob("*.py"))
                 if escape.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"scratch escapes the project: {offenders}"


def test_conftest_reaps_and_pins_basetemp():
    """The confinement hooks themselves must stay wired (they are load-bearing:
    removing either silently moves the suite's scratch back to /tmp)."""
    conf = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert 'os.environ["TMPDIR"]' in conf, "TMPDIR must be pointed at the project"
    assert "tempfile.tempdir" in conf, "gettempdir()'s cache must be pinned too"
    assert 'config.option.basetemp' in conf, "tmp_path must be pinned to the project"
    assert "_reap()" in conf, "leftovers must be swept, not accumulated"
    assert 'SCRATCH / "artifacts"' in conf, \
        "the reserved artifacts/ subtree must stay wired into the sweep"


def test_reap_spares_the_reserved_artifacts_subtree(tmp_path, monkeypatch):
    """Third real regression, pinned: the sweep used to destroy durable agent
    artifacts (the panel spec/log lived in SCRATCH and every pytest run ate
    them). The sweep owns temp junk; artifacts/ must survive every run.

    Semantics on a private root: _reap() resolves SCRATCH at call time, so
    retargeting proves the function without firing the real sweep mid-session
    (that eats other files' live fixture dirs). The real wiring stays pinned
    by test_conftest_reaps_and_pins_basetemp above.
    """
    keep = tmp_path / "artifacts" / "keep.txt"
    stray = tmp_path / "reap-me.txt"
    keep.parent.mkdir()
    keep.write_text("durable probe — must survive the sweep", encoding="utf-8")
    stray.write_text("temp junk — the sweep must take this", encoding="utf-8")
    monkeypatch.setattr(conftest, "SCRATCH", tmp_path)
    conftest._reap()
    assert keep.exists(), "durable artifact was reaped"
    assert not stray.exists(), "stray scratch survived the sweep"
    # the reserved dir is live in the REAL tree too; this test never touches
    # it (the session sweep itself proves the spec file survives every run)
    assert (SCRATCH / "artifacts").is_dir()
    shutil.rmtree(tmp_path, ignore_errors=True)
