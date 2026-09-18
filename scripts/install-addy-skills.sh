#!/usr/bin/env bash
set -euo pipefail
# Full Addy pack (MIT) into user skills dir (scanned, quarantined on load).
DEST="${1:-$HOME/.harness/skills/_addy}"
mkdir -p "$DEST"
if [ -d "$DEST/.git" ]; then git -C "$DEST" pull --ff-only; else git clone --depth 1 https://github.com/addyosmani/agent-skills "$DEST"; fi
echo "cloned to $DEST — referenced via skills.external_dirs or copy selected SKILL.md + needed references/"
