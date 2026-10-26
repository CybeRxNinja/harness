"""MCP servers: opencode owns them; harness lists and edits them on request.

opencode v2 already ships the manager (`opencode mcp add|list|auth`), so `add`
delegates instead of re-implementing the config shape. Two gaps it leaves are
handled here: `list` reads the config files directly (opencode's own list is a
live-server view and can miss a just-written project file), and `remove` edits
the `mcp` block in place — opencode has no remove subcommand. The edit replaces
only that value's text span, so comments and every other key survive.

Nothing here prints an env value or header value: `list` reports keys, and
`add` redacts the values it passed from the command output.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from .config import _strip_jsonc
from .paths import opencode_config_dir

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HEADER_KEY_RE = re.compile(r"^[^\s=]+$")


def config_file(root: str | Path = ".", global_: bool = False) -> Path:
    """The opencode config opencode itself reads for this scope."""
    if global_:
        return opencode_config_dir() / "opencode.json"
    return Path(root).resolve() / "opencode.json"


def _load(path: Path) -> dict:
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return {}
    try:
        data = json.loads(_strip_jsonc(text))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _keys(value) -> list[str]:
    return sorted(str(k) for k in value) if isinstance(value, dict) else []


def servers(root: str | Path = ".") -> list[dict]:
    """Every configured MCP server, project overriding global by name.

    Reads the files rather than asking a running server, so a config written a
    second ago is visible immediately. Secret VALUES are never returned — only
    the env/header keys that carry them.
    """
    found: dict[str, dict] = {}
    for source, path in (("global", config_file(root, True)),
                         ("project", config_file(root, False))):
        data = _load(path)
        block = data.get("mcp") if isinstance(data.get("mcp"), dict) else {}
        rows = block.get("servers") if isinstance(block.get("servers"), dict) else {}
        for name, spec in rows.items():
            if not isinstance(spec, dict):
                continue
            command = spec.get("command")
            found[str(name)] = {
                "name": str(name),
                "source": source,
                "type": str(spec.get("type") or ""),
                "command": [str(c) for c in command] if isinstance(command, list) else [],
                "url": str(spec.get("url") or ""),
                "environment": _keys(spec.get("environment")),
                "headers": _keys(spec.get("headers")),
                "enabled": spec.get("enabled") is not False,
                "config": str(path),
            }
    return [found[name] for name in sorted(found)]


def _redact(text: str, secrets: list[str]) -> str:
    out = str(text or "")
    for value in secrets:
        if value and len(value) >= 3:
            out = out.replace(value, "***")
    return out


def _run(argv: list[str], root: str | Path, timeout: int = 60) -> dict:
    """Run opencode in the project dir; never raises, never echoes its argv."""
    try:
        r = subprocess.run(argv, cwd=str(Path(root).resolve()),
                           capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "code": 127, "out": "opencode binary not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "out": f"opencode timed out after {timeout}s"}
    out = "\n".join(part for part in ((r.stdout or "").strip(),
                                      (r.stderr or "").strip()) if part)
    return {"ok": r.returncode == 0, "code": r.returncode, "out": out}


def add(root: str | Path = ".", name: str = "", command: list[str] | None = None,
        url: str = "", env: list[tuple[str, str]] | None = None,
        headers: list[tuple[str, str]] | None = None,
        global_: bool = False) -> dict:
    """Add or update one server through opencode's own `mcp add`."""
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        return {"ok": False, "code": 2,
                "out": f"invalid MCP name {name!r}: use letters, digits, . _ - (max 64)"}
    url = str(url or "").strip()
    command = [str(c) for c in (command or []) if str(c)]
    if bool(command) == bool(url):
        return {"ok": False, "code": 2,
                "out": "give exactly one of --command (local) or --url (remote)"}
    env = [(str(k), str(v)) for k, v in (env or [])]
    headers = [(str(k), str(v)) for k, v in (headers or [])]
    for key, _ in env:
        if not ENV_KEY_RE.match(key):
            return {"ok": False, "code": 2, "out": f"invalid env key {key!r}"}
    for key, _ in headers:
        if not HEADER_KEY_RE.match(key):
            return {"ok": False, "code": 2, "out": f"invalid header name {key!r}"}
    argv = ["opencode", "mcp", "add"]
    if global_:
        argv.append("--global")
    for key, value in env:
        argv += ["--env", f"{key}={value}"]
    for key, value in headers:
        argv += ["--header", f"{key}={value}"]
    if url:
        argv += ["--url", url]
    argv.append(name)
    if command:
        argv += ["--", *command]
    res = _run(argv, root)
    res["out"] = _redact(res.get("out") or "", [v for _, v in env + headers])
    path = config_file(root, global_)
    res["path"] = str(path)
    if res["ok"] and (env or headers) and path.exists():
        # A value passed this way is stored in opencode.json. Keep that file
        # owner-only so adding a token does not widen its mode.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return res


def reload(root: str | Path = ".", timeout: int = 15) -> dict:
    """Best-effort `opencode reload`; the caller decides whether to report it."""
    return _run(["opencode", "reload"], root, timeout=timeout)


# ---------------------------------------------------------------------------
# Removing a server: opencode has no `mcp remove`, so edit only the mcp value.
# ---------------------------------------------------------------------------

def _skip_string(text: str, i: int) -> int:
    """`i` at the opening quote; index just past the closing quote."""
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return i


def _value_end(text: str, start: int) -> int:
    i = start
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text):
        return i
    if text[i] == '"':
        return _skip_string(text, i)
    if text[i] in "{[":
        depth = 0
        while i < len(text):
            c = text[i]
            if c == '"':
                i = _skip_string(text, i)
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return i
    while i < len(text) and text[i] not in ",}]":
        i += 1
    while i > start and text[i - 1] in " \t\r\n":
        i -= 1
    return i


def _key_span(text: str, key: str) -> tuple[int, int, int] | None:
    """(key_start, value_start, value_end) for a TOP-LEVEL object key."""
    depth = 0
    i = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            end = _skip_string(text, i)
            if depth == 1:
                try:
                    name = json.loads(text[i:end])
                except Exception:
                    name = ""
                if name == key:
                    j = end
                    while j < len(text) and text[j] in " \t\r\n":
                        j += 1
                    if j < len(text) and text[j] == ":":
                        start = j + 1
                        while start < len(text) and text[start] in " \t\r\n":
                            start += 1
                        return i, start, _value_end(text, start)
            i = end
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    return None


def _replace_value(text: str, span: tuple[int, int, int], value: dict) -> str:
    key_start, start, end = span
    line_start = text.rfind("\n", 0, key_start) + 1
    head = text[line_start:key_start]
    indent = len(head) - len(head.lstrip())
    body = json.dumps(value, indent=2, ensure_ascii=False)
    body = body.replace("\n", "\n" + " " * indent)
    return text[:start] + body + text[end:]


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(path)


def remove(root: str | Path = ".", name: str = "", global_: bool = False) -> bool:
    """Delete one server from the config; False when it was not there.

    Only the `mcp` value is rewritten, so comments and unrelated keys keep
    their exact bytes. Refuses (rather than corrupts) a block it cannot parse
    as JSON — e.g. one that contains comments.
    """
    path = config_file(root, global_)
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    span = _key_span(text, "mcp")
    if span is None:
        return False
    try:
        block = json.loads(text[span[1]:span[2]])
    except Exception:
        return False
    rows = block.get("servers") if isinstance(block, dict) else None
    if not isinstance(rows, dict) or name not in rows:
        return False
    del rows[name]
    updated = _replace_value(text, span, block)
    try:
        check = json.loads(_strip_jsonc(updated))
    except Exception:
        return False
    left = ((check.get("mcp") or {}).get("servers") or {})
    if not isinstance(left, dict) or name in left:
        return False
    _write_atomic(path, updated)
    return True
