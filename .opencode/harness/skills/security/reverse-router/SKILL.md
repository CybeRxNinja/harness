---
name: reverse-router
description: Triage RE/sec tasks to the right playbook. Disabled by default; lawful-use only.
---
# Reverse Router (subset of zhaoxuya520/reverse-skill, MIT skills only; CTF GPL part excluded)
## When to Use
APK/binary/JS-crypto/PCAP/pentest with explicit authorization only.
## Procedure
1. Require scope file (target + auth proof + network profile). NO target ACT until scope ready.
2. Triage via routing table (apk|js|binary|pentest) -> load ONE scenario reference (L2).
3. Check tool-index (`harness` reports missing; never auto-install).
4. Timeline + Evidence->Finding->Path + report. Audit-log every target-touching command.
## Pitfalls
- Touching target before scope. - Auto-installing tools. - Scanning outside scope.
## Verification
Scope file linked + tool-index cited + report with evidence hashes.
