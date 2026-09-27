---
name: apk-reverse
description: Static APK analysis (manifest, certs, code, secrets). Lawful-use only; no repack, piracy, or malware work.
---
# APK Reverse (static-analysis subset of https://github.com/newliver666/apk-reverse)
## When to Use
Inspecting an APK you own or are authorized to analyze (own app, authorized sample, CTF).
Static questions only: manifest/permissions, signing cert, where code lives, secrets in resources.
## Procedure
1. Confirm authorization (own app, written auth, or CTF scope) — else stop. Work on a copy; hash the original.
2. Recon before decompile: package/version/ABIs/dex count via an index query (`droidasc findrefs`/`listclass`/`getmanifest`). Locate first, decompile second.
3. Read located classes with `ddc` per-class decompile (`jadx` is a viewer of last resort, never the entry point); `baksmali` only when instruction-level detail is needed. Read-only: no patching in this skill.
4. Inspect manifest (permissions, exported components), signing cert (`apksigner`/`keytool`), and resources/`strings` for hardcoded secrets.
5. Label every finding `observed` (command output exists), `inferred` (reasoned step), or `unverified` (assumed). Packed targets or server-side gates are out of scope: say so and stop.
## Pitfalls
- Decompiling everything before one indexed query (hours of grep over a paid-for export).
- Drifting into repack/patch routes — this skill is read-only analysis.
- License-check bypass, paid-app piracy, malware weaponization, repackaging for distribution — refused, not rerouted.
## Verification
Authorization cited + tool outputs quoted + findings labeled observed/inferred/unverified; out-of-scope targets named, not improvised on.
