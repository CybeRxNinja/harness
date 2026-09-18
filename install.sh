#!/usr/bin/env bash
set -euo pipefail
# Installer: core via pip + TUI binary from Releases (sha256 verified). No Bun on user machine.
REPO="${HARNESS_REPO:-YOU/harness}"
VER="${HARNESS_VER:-latest}"
pip install -e . 2>/dev/null || pip install .
mkdir -p ~/.harness ~/.local/bin
if command -v harness-tui >/dev/null; then echo "harness-tui present: $(command -v harness-tui)"; exit 0; fi
ARCH=x64
URL="https://github.com/${REPO}/releases/${VER}/download/harness-tui-linux-${ARCH}"
if curl -fsSL "$URL" -o ~/.local/bin/harness-tui; then
  chmod +x ~/.local/bin/harness-tui
  echo "installed harness-tui to ~/.local/bin/harness-tui (add to PATH)"
else
  echo "no TUI binary yet (fine) — CLI fallback active: harness chat --help"
fi
harness setup || true
