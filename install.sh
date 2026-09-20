#!/usr/bin/env bash
set -euo pipefail
# Installer: harness CLI via pip + opencode server plugin. Stock opencode only —
# no forked TUI binary and no AppImage. The plugin provides seed-skill
# registration, native skills/memory tools, and session-compaction memory
# recall inside stock opencode. Models always come from YOUR opencode config.
REPO="${HARNESS_REPO:-CybeRxNinja/harness}"
python3 -m pip install --break-system-packages "git+https://github.com/${REPO}.git" 2>/dev/null \
  || python3 -m pip install "git+https://github.com/${REPO}.git"
mkdir -p ~/.harness
# Seed global skills, verify your model, and install the server plugin
# (harness.ts -> ~/.config/opencode/plugins/, auto-loaded by opencode v2).
python3 -m harness setup || true
python3 -m harness plugin install || true
echo "Installed. Set your model in opencode.json, then launch stock opencode:"
echo "  opencode        # or: harness tui   (opencode config + plugin, then launches opencode)"
