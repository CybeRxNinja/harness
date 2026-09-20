"""Lite RTK + Caveman compression (stdlib only, deterministic, no LLM).

RTK-lite: command-aware filters for machine output (tests, builds, git,
package managers, shell, tracebacks). Errors and failures are NEVER dropped.
Caveman-lite: prose condenser that protects code/URLs/JSON/paths first.
Stacked order is always rtk -> caveman. Every filter carries inline tests;
see tests/test_compress.py (output never larger, errors always preserved).
"""
from __future__ import annotations

import re

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")

# Lines matching any of these are ALWAYS preserved, whatever the filter says.
ERROR_RE = re.compile(
    r"Traceback |Error:|Exception:|FAILED|failed|FAIL |AssertionError|"
    r"error:|ERROR|panic:|fatal:|✗|✖|E  +[A-Za-z]|assert",
)

FILTERS: list[dict] = [
    {"id": "python-traceback", "commands": ("python", "python3", "pytest", "uv"),
     "patterns": (r"Traceback \(most recent call last\)",),
     "include": (r"Traceback \(most recent call last\)", r'^\s*File ".+", line \d+',
                 r"^[A-Za-z_]+Error:", r"^[A-Za-z_]+Exception:"),
     "drop": (r"site-packages/", r"^\s+[a-z_]+\([^)]*\)$"),
     "head": 5, "tail": 3, "max_lines": 25,
     "tests": [{"name": "keeps-location-and-type",
                "input": "Traceback (most recent call last):\n  File \"app.py\", line 42, in main\n    do_thing()\n  File \"lib/utils.py\", line 17, in helper\n    return 1 / 0\nZeroDivisionError: division by zero",
                "expect": ("app.py\", line 42", "ZeroDivisionError")}]} ,
    {"id": "pytest", "commands": ("pytest",),
     "patterns": (r"=+.*(passed|failed|error)|FAILED|PASSED",),
     "include": (r"FAILED", r"ERROR", r"passed|failed", r"short test summary"),
     "drop": (r"^\.|^s$",),
     "collapse": (r"^\.+$", r"^s+$"),
     "head": 8, "tail": 10, "max_lines": 40,
     "tests": [{"name": "keeps-summary",
                "input": "FAILED tests/x.py::test_a - assert False\n===== 1 failed in 0.5s =====",
                "expect": ("FAILED", "1 failed")}]} ,
    {"id": "npm", "commands": ("npm", "npx", "node", "bun", "tsc", "eslint"),
     "patterns": (r"npm (ERR!|WARN)|error TS\d+|ERROR|error:",),
     "include": (r"npm ERR!", r"error TS\d+", r"ERROR", r"FAIL"),
     "drop": (r"npm WARN", r"^\s+at\s",),
     "collapse": (r"^\s*at\s.*$",),
     "head": 6, "tail": 8, "max_lines": 40,
     "tests": [{"name": "keeps-npm-error",
                "input": "npm ERR! code E404\nnpm ERR! 404 Not Found\nnpm WARN deprecated x",
                "expect": ("E404",)}]} ,
    {"id": "git", "commands": ("git",),
     "patterns": (r"^(diff --git|commit |On branch|Changes )",),
     "include": (r"^diff --git", r"^[+-]{3} ", r"^commit ", r"files? changed"),
     "drop": (r"^index [0-9a-f]+\.\.[0-9a-f]+", r"^new file mode", r"^old mode"),
     "head": 10, "tail": 10, "max_lines": 60,
     "tests": [{"name": "keeps-diff-files",
                "input": "diff --git a/x.py b/x.py\nindex 123..456 100644\n--- a/x.py\n+++ b/x.py\n+hello",
                "expect": ("diff --git", "+hello")}]} ,
    {"id": "pip", "commands": ("pip", "uv", "poetry"),
     "patterns": (r"ERROR:|error:|Successfully|WARNING",),
     "include": (r"ERROR", r"Successfully installed", r"Requirement already satisfied"),
     "drop": (r"^\s*$",),
     "collapse": (r"^Requirement already satisfied.*$", r"^Collecting.*$"),
     "head": 4, "tail": 6, "max_lines": 25,
     "tests": [{"name": "keeps-install-result",
                "input": "Collecting x\nCollecting x\nSuccessfully installed x-1.0",
                "expect": ("Successfully installed",)}]} ,
    {"id": "shell-list", "commands": ("ls", "find", "grep", "rg", "cat", "lsblk", "df", "ps"),
     "patterns": (),
     "include": (),
     "drop": (),
     "collapse": (),
     "head": 30, "tail": 20, "max_lines": 60,
     "tests": []},
    {"id": "generic", "commands": (),
     "patterns": (),
     "include": (),
     "drop": (),
     "collapse": (),
     "head": 20, "tail": 20, "max_lines": 80,
     "tests": []},
]

INTENSITY = {
    "minimal": {"truncate": False, "max_lines": 400, "head": 60, "tail": 60},
    "standard": {"truncate": True, "max_lines": 120, "head": 24, "tail": 24},
    "aggressive": {"truncate": True, "max_lines": 60, "head": 12, "tail": 12},
}


def _pick_filter(text: str, command: str) -> dict:
    cmd = (command or "").strip().split()[0].lower() if command else ""
    for f in FILTERS:
        if f["id"] == "generic":
            continue
        if cmd and cmd in f["commands"]:
            return f
    for f in FILTERS:
        if f["id"] == "generic":
            continue
        for pat in f["patterns"]:
            if re.search(pat, text, re.M):
                return f
    return next(f for f in FILTERS if f["id"] == "generic")


def _collapse_runs(lines: list[str], threshold: int = 3) -> list[str]:
    out: list[str] = []
    run: list[str] = []
    def flush() -> None:
        if len(run) >= threshold:
            out.append(run[0])
            out.append(f"... [{len(run) - 1} repeated lines] ...")
        else:
            out.extend(run)
    for ln in lines:
        if run and ln == run[0]:
            run.append(ln)
        else:
            flush()
            run = [ln]
    flush()
    return out


def rtk_lite(text: str, command: str = "", intensity: str = "standard") -> tuple[str, dict]:
    """Command-aware deterministic compression. Returns (text, stats)."""
    cfg = INTENSITY.get(intensity, INTENSITY["standard"])
    original = text or ""
    stripped = ANSI_RE.sub("", original).replace("\r", "")
    lines = stripped.splitlines()
    f = _pick_filter(stripped, command)
    kept: list[str] = []
    dropped = 0
    for ln in lines:
        if ERROR_RE.search(ln):
            kept.append(ln)  # errors are sacred
            continue
        if f["include"] and any(re.search(p, ln) for p in f["include"]):
            kept.append(ln)
            continue
        if any(re.search(p, ln) for p in f["drop"]):
            dropped += 1
            continue
        kept.append(ln)
    # collapse runs, then head/tail truncation (errors already safe above,
    # but re-check after truncation window selection)
    collapsed = _collapse_runs(kept)
    if cfg["truncate"] and len(collapsed) > cfg["max_lines"]:
        head, tail = collapsed[: cfg["head"]], collapsed[-cfg["tail"] :]
        omitted = len(collapsed) - len(head) - len(tail)
        collapsed = head + [f"... [{omitted} lines elided] ..."] + tail
    if not "".join(collapsed).strip():
        collapsed = [f.get("on_empty", f"[no output after {f['id']} filter]")]
    out = "\n".join(collapsed)
    stats = {"engine": "rtk_lite", "filter": f["id"], "in_chars": len(original),
             "out_chars": len(out), "dropped_lines": dropped,
             "saved_pct": round(100 * (1 - len(out) / max(1, len(original))), 1)}
    return out, stats


FILLER_RE = re.compile(
    r"\b(very|really|quite|rather|basically|essentially|actually|just|simply|"
    r"in order to|due to the fact that|it is important to note that|"
    r"as a matter of fact|for all intents and purposes)\b", re.I)

CODE_RE = re.compile(r"```[\s\S]*?```|`[^`\n]+`")
URL_RE = re.compile(r"https?://\S+")
JSON_RE = re.compile(r"\{[^{}]{20,}\}|\[[^\[\]]{20,}\]")
PATH_RE = re.compile(r"(?:~?/[A-Za-z0-9_.\-]+)+/?")


def caveman_lite(text: str) -> tuple[str, dict]:
    """Prose condenser. Code/URLs/JSON/paths are placeholder-protected."""
    original = text or ""
    vault: list[str] = []

    def stash(m: "re.Match[str]") -> str:
        vault.append(m.group(0))
        return f"\x00V{len(vault) - 1}\x00"

    import re as _re
    tmp = CODE_RE.sub(stash, original)
    tmp = URL_RE.sub(stash, tmp)
    tmp = JSON_RE.sub(stash, tmp)
    tmp = PATH_RE.sub(stash, tmp)
    tmp = FILLER_RE.sub("", tmp)
    tmp = _re.sub(r"[ \t]+", " ", tmp)
    tmp = _re.sub(r"\n{3,}", "\n\n", tmp)
    tmp = _re.sub(r" +", " ", tmp)
    lines = [ln.strip() for ln in tmp.splitlines()]
    out_lines: list[str] = []
    prev = None
    for ln in lines:
        if ln == prev and ln:
            continue
        prev = ln
        out_lines.append(ln)
    tmp = "\n".join(out_lines).strip()

    def unstash(m: "re.Match[str]") -> str:
        try:
            return vault[int(m.group(1))]
        except Exception:
            return m.group(0)

    out = _re.sub(r"\x00V(\d+)\x00", unstash, tmp)
    stats = {"engine": "caveman_lite", "in_chars": len(original), "out_chars": len(out),
             "saved_pct": round(100 * (1 - len(out) / max(1, len(original))), 1)}
    return out, stats


def stacked(text: str, command: str = "", intensity: str = "standard") -> tuple[str, dict]:
    """rtk -> caveman. Returns (text, stats with per-engine breakdown)."""
    mid, s1 = rtk_lite(text, command, intensity)
    out, s2 = caveman_lite(mid)
    total_in, total_out = len(text or ""), len(out)
    return out, {"engines": [s1["engine"], s2["engine"]], "filter": s1.get("filter"),
                 "in_chars": total_in, "out_chars": total_out,
                 "saved_pct": round(100 * (1 - total_out / max(1, total_in)), 1)}


def should_compress(nchars: int, threshold: int = 4000) -> bool:
    return nchars >= threshold
