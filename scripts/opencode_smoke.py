#!/usr/bin/env python3
"""Live smoke test: the plugin must activate on a real opencode server.

Boots `opencode serve` (no model calls — free-tier models and API keys are not
needed), forces plugin activation by creating a session, then asserts through
the server's own API that:

  1. the `harness` plugin is listed with state.status == "active" and
     source.path pointing at the file this script installed
  2. the bundled harness-* skills were seeded into the skill store

Direct-tool visibility (codemode:false) is locked by the bun feature tests in
tests/test_plugin.py; the server's per-session tool registry is not a stable
public endpoint, so this script does not assert on it (it would break on
unrelated opencode upgrades).

Exit code 0 = plugin behaves on this opencode version. Any assertion failure
prints the diff between expected and actual so a payload/API drift is obvious.

Usage: python3 scripts/opencode_smoke.py [--port 4199] [--keep]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "harness" / "plugin"


def install_plugin() -> Path:
    """Install the plugin dir (server.ts + tui.tsx) into opencode's auto-loaded
    plugins dir (idempotent); mirrors `harness plugin install`."""
    dest = (Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
            / "opencode" / "plugins" / "harness")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "server.ts").write_text((PLUGIN_DIR / "harness.ts").read_text())
    tui = PLUGIN_DIR / "tui.tsx"
    if tui.exists():
        (dest / "tui.tsx").write_text(tui.read_text())
    legacy = dest.parent / "harness.ts"
    if legacy.exists():
        legacy.unlink()
    return dest


class Server:
    def __init__(self, port: int, boot_timeout: float = 45.0):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.auth: str = ""
        self.boot_timeout = boot_timeout
        self.lines: list[str] = []

    def banner(self) -> str | None:
        """Read the startup banner once: which auth mode is this server in?

        A machine that has run opencode before reports `server password <x>` and
        wants HTTP basic auth. A CLEAN one (CI) warns
        "OPENCODE_SERVER_PASSWORD is not set; server is unsecured" and wants no
        auth at all — treating that as "no banner" is what made this script file
        a healthy server as a 45s timeout.
        """
        for line in list(self.lines):
            m = re.search(r"server password (\S+)", line)
            if m:
                token = base64.b64encode(f"opencode:{m.group(1)}".encode()).decode()
                self.auth = f"Basic {token}"
                return "password"
            if "is unsecured" in line or "OPENCODE_SERVER_PASSWORD is not set" in line:
                return "unsecured"
        return None

    def tail(self, n: int = 25) -> str:
        """The server's last output — what a failure actually looked like."""
        return "\n".join(self.lines[-n:]) or "(no output)"

    def _drain(self) -> None:
        """Read the server's output on its own thread.

        A blocking readline() on a pipe is how this script hung a CI job for 32
        minutes: `opencode serve` buffers its banner when stdout is not a TTY, so
        the read never returned and no deadline could fire. Reading on a thread
        keeps the deadline in charge.
        """
        try:
            for line in self.proc.stdout or []:  # type: ignore[union-attr]
                self.lines.append(line.rstrip())
                del self.lines[:-80]
        except Exception:
            pass

    def __enter__(self) -> "Server":
        try:
            self.proc = subprocess.Popen(
                ["opencode", "serve", "--hostname", "127.0.0.1", "--port", str(self.port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"opencode is not on PATH: {e}") from None
        threading.Thread(target=self._drain, daemon=True).start()

        deadline = time.time() + self.boot_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"opencode serve exited early (code {self.proc.returncode}):\n{self.tail()}"
                )
            # the banner can arrive in any order relative to the API coming up,
            # and the API itself is the health check either way
            if self.banner() and self.get("/api/plugin") is not None:
                return self
            time.sleep(0.25)
        raise RuntimeError(
            f"opencode serve did not report a password + healthy API in {self.boot_timeout:.0f}s:\n{self.tail()}"
        )

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def headers(self) -> dict:
        """No Authorization header at all when the server has no password — an
        empty header value is not the same request."""
        return {"Authorization": self.auth} if self.auth else {}

    def get(self, path: str):
        req = urllib.request.Request(self.base + path, headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
            return None


def wait_for_activation(srv: Server, timeout: float = 45.0) -> dict:
    """Create a session (forces lazy plugin activation) then poll /api/plugin."""
    req = urllib.request.Request(
        srv.base + "/api/session",
        method="POST",
        headers={**srv.headers(), "Content-Type": "application/json"},
        data=b"{}",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        json.loads(r.read())
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        plugins = (srv.get("/api/plugin") or {}).get("data", [])
        last = next((p for p in plugins if p.get("id") == "harness"), {})
        if last.get("state", {}).get("status") == "active":
            return last
        time.sleep(1)
    return last


def list_skills(srv: Server) -> list[dict]:
    """Unwrap the skill listing (shape varies across versions)."""
    raw = srv.get("/api/skill")
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(data, dict):
        data = data.get("skills", data.get("data", []))
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4199)
    ap.add_argument("--boot-timeout", type=float, default=45.0,
                    help="seconds to wait for the server banner + API (bounded on purpose)")
    args = ap.parse_args()

    if not PLUGIN_DIR.exists():
        print(f"FAIL: {PLUGIN_DIR} missing")
        return 2
    dest = install_plugin()

    failures: list[str] = []
    with Server(args.port, args.boot_timeout) as srv:
        harness = wait_for_activation(srv)
        status = harness.get("state", {}).get("status")
        features = harness.get("features", {})
        src_path = harness.get("source", {}).get("path")
        print(f"server: auth={srv.auth[:7] + '…' if srv.auth else 'none'}")
        print(f"plugin: id={harness.get('id')} status={status} features={features} path={src_path}")
        if status != "active":
            failures.append(f"plugin not active: {json.dumps(harness)}")
        elif src_path and Path(src_path).resolve().parent != dest.resolve():
            failures.append(f"active plugin is not the installed dir: {src_path}")
        if not features.get("tui"):
            failures.append("features.tui not set — TUI plugin list would omit harness "
                            f"(features={json.dumps(features)})")

        # 2. skills seeded
        skills = list_skills(srv)
        ids = {str(s.get("id")) for s in skills}
        seeded = sorted(i for i in ids if i.startswith("harness-"))
        print(f"skills: total={len(ids)} harness-seeded={len(seeded)}")
        if not seeded:
            failures.append(f"no harness-* skills seeded (ids: {sorted(ids)[:10]}…)")

    if failures:
        print("\nSMOKE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nSMOKE OK — plugin active with features.tui, {len(seeded)} skills seeded (installed at {dest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
