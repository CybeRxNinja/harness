"""Scratch confinement guards: the suite must never touch the system temp.

Two regressions are pinned here, both of them real:
  * the FEATURES fixture used node's tmpdir() -> 54 unmanaged
    `harness-plugin-*` dirs accumulated in /tmp;
  * pytest's tmp_path defaulted to /tmp/pytest-of-<user>.
Everything now lands in <project>/.opencode/harness/tmp/, reaped by
tests/conftest.py.
"""
import re
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
