#!/usr/bin/env bash
set -euo pipefail
# Installer: harness CLI via pip + opencod server plugin. Stock opencod only —
# no forked TUI binary and no AppImage. The plugin provides relay-gateway
# bootstrap, seed-skill registration, and session-compaction memory recall
# inside stock opencod.
REPO="${HARNESS_REPO:-CybeRxNinja/harness}"
python3 -m pip install -e . --break-system-packages 2>/dev/null || python3 -m pip install -e . 2>/dev/null || python3 -m pip install .
mkdir -p ~/.harness
# Seed global skills, mint the gateway token, and install the server plugin
# (harness.ts -> ~/.config/opencode/plugins/, auto-loaded by opencod v2).
python3 -m harness setup || true
python3 -m harness plugin install || true
echo "Installed. Launch stock opencod from your project dir:"
echo "  opencode        # or: harness tui   (gateway + opencod config + plugin, then launches opencode)"
