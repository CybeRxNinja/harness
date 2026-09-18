#!/usr/bin/env bash
set -euo pipefail
# Reverse-router MIT skills only (excludes GPL CTF orchestrator by design).
DEST="${1:-$HOME/.harness/skills/_reverse}"
mkdir -p "$DEST"
if [ -d "$DEST/.git" ]; then git -C "$DEST" pull --ff-only; else git clone --depth 1 https://github.com/zhaoxuya520/reverse-skill "$DEST"; fi
echo "cloned to $DEST. Enable selectively; built-in reverse-router stays disabled until trusted."
echo "Review SECURITY.md + RULES.md before any target work. Lawful-use only."
