---
name: reverse-router
description: Triage RE/sec tasks to the right playbook. Disabled by default; lawful-use only.
---
# Reverse Router (subset of zhaoxuya520/reverse-skill, MIT skills only; CTF GPL part excluded)
## When to Use
Authorized APK/binary/JS-crypto/PCAP/pentest triage. Disabled by default; lawful-use only.
## Procedure
1. Require scope file (target + auth proof + network profile). NO target ACT until scope ready.
2. Triage via routing table (apk|js|binary|pentest) -> load ONE scenario reference (L2).
3. Engine preference: rea (https://github.com/morluto/rea) if installed, else the scenario's own tools. Detect with `rea doctor --json`; headless via `rea analyze <app>` / `rea decompile <app>`, or MCP mode `npx -y rea-agents@3.2.1 mcp` (needs Node >=22.19). If rea is absent, name the missing tool — never auto-install.
4. Choose the provider before believing output: `hopper` (full native analysis, annotations, GUI) or `ghidra` (22 read-only operations, no GUI, no annotations).
5. Timeline + Evidence->Finding->Path + report. Audit-log every target-touching command. Label every finding `observed` (command output exists), `inferred` (reasoned step), or `unverified` (assumed).
## Pitfalls
- Touching target before scope. - Auto-installing rea or anything else. - Scanning outside scope.
- rea's presence lowers no bar: the scope gate still runs first.
- Reporting a Hopper-only capability as fact from a Ghidra run.
- rea gaps to state, not paper over: no Windows Ghidra (P0); managed runtime-correlation executor NOT implemented; browser/Electron scenario actions limited to click+wait.
- Piracy, repack/patching, malware weaponization, license bypass — refused, not rerouted.
## Verification
Scope file linked + `rea doctor --json` / `rea providers --json` output quoted (or the missing tool named) + provider named + report with evidence hashes.
