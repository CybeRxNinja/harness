"""Scratch confinement for the suite.

Everything the tests create lives in the PROJECT under .opencode/harness/tmp/
— never the system temp, where crashed runs used to leave unmanaged junk
(the 54 leftover `harness-plugin-*` dirs in /tmp that prompted this file).
Three mechanisms, all project-local:

* TMPDIR is pointed at the scratch dir at import time, so `tempfile`,
  pytest's own helpers and every child process inherit it;
* basetemp is pinned absolute (cwd-independent — a relative one would follow
  pytest's cwd), so `tmp_path` resolves there too;
* the session fixture reaps fixture dirs on the way in (crash leftovers from a
  previous run) and on the way out (this run), so the directory never piles up.

pytest wipes its basetemp at session start itself; the sweep covers what
pytest does not own (`harness-plugin-*` fixture dirs, stray tempfile files).
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / ".opencode" / "harness" / "tmp"


def pytest_configure(config):
    # Absolute on purpose: a relative basetemp would land wherever pytest was
    # invoked from. Only when the caller did not pin one themselves.
    if not getattr(config.option, "basetemp", None):
        config.option.basetemp = str(SCRATCH / "pytest")


# Import time: tempfile.gettempdir() caches on first use, so this must be set
# before any test — or any library a test loads — touches it. Unconditional on
# purpose: during the suite the project's scratch dir is the only place a temp
# file is allowed to land.
os.environ["TMPDIR"] = str(SCRATCH)
# ...and pin the module global directly: gettempdir() caches its answer, and
# pytest's own startup may have already asked before this file imported.
tempfile.tempdir = str(SCRATCH)


def _reap() -> None:
    """Remove everything a test run may have left directly in SCRATCH."""
    if not SCRATCH.is_dir():
        return
    for path in SCRATCH.iterdir():
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort: a stuck file must not fail the session


@pytest.fixture(scope="session", autouse=True)
def sweep_scratch():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    _reap()  # a crashed previous run does not get to accumulate
    yield
    _reap()  # this run leaves nothing behind either
