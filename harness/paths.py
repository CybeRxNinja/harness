"""Single source of truth for state locations.

Everything project-local lives in <project>/.opencode/harness/ so one folder
holds sessions, memory, kernel, workers, checkpoints, plans and skills.
Global state stays in ~/.harness/ (token, router stats, global skills).

opencode's own config locations live here too: the plugins dir used to be
recomputed (with its own XDG handling) in the CLI and again in `doctor`, and
the two copies disagreed about the installed layout — that is how `harness
doctor` ended up reporting "plugin missing" for a correctly installed plugin.
"""
from __future__ import annotations

import os
from pathlib import Path

STATE_PARTS = (".opencode", "harness")
LEGACY = ".harness"


def state_dir(root: str | Path) -> Path:
    """<root>/.opencode/harness (created on demand)."""
    p = Path(root).resolve()
    for part in STATE_PARTS:
        p = p / part
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_file(root: str | Path, name: str) -> Path:
    """Path to a project state file WITHOUT creating anything.

    `state_dir()` mkdirs as a side effect, which is wrong for anything that
    merely reports (doctor, status output).
    """
    p = Path(root).resolve()
    for part in STATE_PARTS:
        p = p / part
    return p / name


def project_config_file(project_dir: str | Path) -> Path:
    """The project config layer load_config() reads: <root>/.opencode/harness.jsonc.

    `set_value(..., scope="project")` wrote to state_dir(root)/harness.jsonc
    (.opencode/harness/harness.jsonc) — a path nothing ever read, so a project
    override was accepted and then silently ignored.
    """
    return Path(project_dir).resolve() / ".opencode" / "harness.jsonc"


def opencode_config_dir() -> Path:
    """opencode's user config dir, honouring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "opencode"


def opencode_plugins_dir() -> Path:
    """Where opencode auto-loads plugins from (~/.config/opencode/plugins)."""
    return opencode_config_dir() / "plugins"


def plugin_install_dir() -> Path:
    """The installed plugin layout: <plugins>/harness/{server.ts,tui.tsx}.

    The loader probes a plugin DIRECTORY for `server.*` and `tui.*`. A bare
    harness.ts is a server-only plugin and never appears in the TUI's plugin
    list, which filters on the `tui` feature.
    """
    return opencode_plugins_dir() / "harness"


def migrate_legacy(root: str | Path) -> str:
    """One-time move of <root>/.harness to the new location.

    Moves only when the target does not exist yet (never merges, never
    deletes user data). Returns what happened: moved|already|absent|both.
    """
    r = Path(root).resolve()
    legacy = r / LEGACY
    if not legacy.exists():
        return "absent"
    target = r.joinpath(*STATE_PARTS)
    if target.exists():
        return "both"
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(target)
    return "moved"
