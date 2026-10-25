"""Default-deny policy for the Secret Broker's `secret_exec` (curl only).

The secret VALUE never reaches this module: `check()` sees the argv with the
`[[hb:...]]` reference still in it, validates every option and URL, and matches
the URLs against exact-host grants for one env key. The plugin substitutes the
value only after an allow, then re-parses the URL indices returned here.

Garbage in is a deny out. `check()` never raises.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

MAX_ARGS = 64
MAX_ARG_BYTES = 8192
MAX_TOTAL_BYTES = 32768
MAX_GRANTS_BYTES = 65536

REF_RE = re.compile(r"^\[\[hb:[a-f0-9]{8}-\d+\]\]$")
HOST_RE = re.compile(r"^[a-z0-9._-]+$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")
CONTROL = ("\0", "\r", "\n")

# Exact long names only. An abbreviation (`--conf`) is not in the table, so it
# is denied like any other unknown option — never prefix-matched.
ALLOW_FLAGS = {
    "-s", "--silent", "-S", "--show-error", "-f", "--fail", "--fail-with-body",
    "-i", "--include", "-I", "--head", "-G", "--get", "-g", "--globoff",
    "--compressed", "--http1.1", "--http2",
}
# canonical name -> True. Short options take their value from the rest of the
# cluster (`-Hfoo`) or the next argv element (`-H foo`), exactly curl's rule.
ALLOW_VALUE = {
    "-X": "request", "--request": "request",
    "-H": "header", "--header": "header",
    "-d": "data", "--data": "data",
    "--data-binary": "data-binary",
    "--data-raw": "data-raw",
    "-u": "user", "--user": "user",
    "--oauth2-bearer": "oauth2-bearer",
    "-A": "user-agent", "--user-agent": "user-agent",
    "--url": "url",
}
DENY_HEADER_NAMES = {"host", "x-forwarded-host"}


def _deny(reason: str, rule: str) -> dict:
    return {"allow": False, "reason": str(reason)[:300], "rule": rule}


def _strict_pairs(pairs):
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def grants_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    home = os.environ.get("HARNESS_HOME") or str(Path.home() / ".harness")
    return Path(home) / "secret-grants.json"


def _grant_entry(entry: str):
    """`host` -> (host, None); `host:port` -> (host, port). None when invalid."""
    text = str(entry).strip().lower()
    if not text or "*" in text or "/" in text or "\\" in text or "@" in text:
        return None
    host, port_text = text, ""
    if ":" in text:
        host, _, port_text = text.rpartition(":")
        if not port_text.isdigit():
            return None
        port = int(port_text)
        if not 1 <= port <= 65535:
            return None
    else:
        port = None
    host = host.rstrip(".")
    if not host or not HOST_RE.match(host):
        return None
    return (host, port)


def load_grants(path: str | Path | None = None) -> tuple[dict | None, str]:
    """Strict grants JSON: one bad entry invalidates the whole file (deny)."""
    file = grants_path(path)
    try:
        if not file.exists():
            return None, f"no grants file at {file}"
        info = file.stat()
        if info.st_size > MAX_GRANTS_BYTES:
            return None, "grants file exceeds 64 KiB"
        if os.name == "posix" and (info.st_mode & 0o022):
            return None, "grants file is group/world-writable"
        data = json.loads(file.read_text(), object_pairs_hook=_strict_pairs)
    except Exception as e:
        return None, f"grants unreadable: {e}"
    if not isinstance(data, dict) or not data:
        return None, "grants must be a non-empty object"
    out: dict[str, list[tuple[str, int | None]]] = {}
    for key, entries in data.items():
        if not isinstance(key, str) or not key or key != key.strip():
            return None, "invalid grant key"
        if not isinstance(entries, list) or not entries:
            return None, f"grant {key} must be a non-empty list"
        rows = []
        for entry in entries:
            if not isinstance(entry, str):
                return None, f"grant {key} has a non-string entry"
            parsed = _grant_entry(entry)
            if parsed is None:
                return None, f"invalid grant entry {entry!r}"
            rows.append(parsed)
        out[key] = rows
    return out, ""


def _url(raw: str):
    """(url dict, "") or (None, why). http/https, no userinfo, ASCII host."""
    text = str(raw)
    if any(ch in text for ch in ("%", "\\", "{", "}")):
        return None, "URL contains a forbidden character"
    try:
        parts = urlsplit(text)
    except Exception:
        return None, "unparseable URL"
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None, "only http and https URLs are allowed"
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        return None, "userinfo in a URL is not allowed"
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None, "empty URL host"
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:
            return None, "non-ASCII host cannot be IDNA-encoded"
    if not HOST_RE.match(host):
        return None, f"invalid URL host {host!r}"
    try:
        port = parts.port
    except ValueError:
        return None, "invalid URL port"
    if port is not None and not 1 <= port <= 65535:
        return None, "URL port out of range"
    default = 443 if scheme == "https" else 80
    effective = port if port is not None else default
    origin = f"{scheme}://{host}" if effective == default else f"{scheme}://{host}:{effective}"
    return {"host": host, "port": port, "effective": effective,
            "default": default, "origin": origin}, ""


def _granted(url: dict, grants) -> tuple[bool, str]:
    for host, port in grants:
        if url["host"] != host:
            continue
        if port is None:
            if url["effective"] == url["default"]:
                return True, host
        elif url["effective"] == port:
            return True, f"{host}:{port}"
    return False, ""


def _header_ok(value: str) -> bool:
    name, sep, _ = str(value).partition(":")
    if not sep or not name.strip() or not TOKEN_RE.match(name.strip()):
        return False
    low = name.strip().lower()
    return low not in DENY_HEADER_NAMES and not low.startswith("proxy-")


def _parse_argv(argv: list[str]) -> tuple[list[tuple[int, str]], str]:
    """(url occurrences, why). Each is (argv index, raw URL)."""
    urls: list[tuple[int, str]] = []
    i = 1
    end_opts = False
    while i < len(argv):
        arg = argv[i]
        if end_opts:
            urls.append((i, arg))
            i += 1
            continue
        if arg == "--":
            end_opts = True
            i += 1
            continue
        if arg.startswith("--"):
            name, eq, inline = arg.partition("=")
            if name in ALLOW_FLAGS:
                if eq:
                    return [], f"{name} takes no value"
                i += 1
                continue
            canonical = ALLOW_VALUE.get(name)
            if canonical is None:
                return [], f"option {name!r} is not allowed"
            if eq:
                value = inline
                i += 1
            else:
                if i + 1 >= len(argv):
                    return [], f"option {name} needs a value"
                value = argv[i + 1]
                i += 2
            why = _check_value(canonical, value)
            if why:
                return [], why
            if canonical == "url":
                urls.append((i - 1, value))
            continue
        if arg.startswith("-") and arg != "-":
            cluster = arg[1:]
            j = 0
            while j < len(cluster):
                short = "-" + cluster[j]
                if short in ALLOW_FLAGS:
                    j += 1
                    continue
                canonical = ALLOW_VALUE.get(short)
                if canonical is None:
                    return [], f"option {short!r} is not allowed"
                rest = cluster[j + 1:]
                if rest:
                    value = rest
                    value_index = i
                    i += 1
                else:
                    if i + 1 >= len(argv):
                        return [], f"option {short} needs a value"
                    value = argv[i + 1]
                    value_index = i + 1
                    i += 2
                why = _check_value(canonical, value)
                if why:
                    return [], why
                if canonical == "url":
                    urls.append((value_index, value))
                break
            else:
                i += 1
            continue
        urls.append((i, arg))
        i += 1
    return urls, ""


def _check_value(canonical: str, value: str) -> str:
    if canonical == "header":
        if value.startswith("@"):
            return "header file references are not allowed"
        if not _header_ok(value):
            return f"header {value[:60]!r} is not allowed"
    if canonical in ("data", "data-binary") and value.startswith("@"):
        return "data file references are not allowed"
    if canonical == "user-agent" and value.startswith("@"):
        return "user-agent file references are not allowed"
    return ""


def _check(payload: dict, grants_path_override: str | Path | None) -> dict:
    if not isinstance(payload, dict):
        return _deny("payload must be an object", "input")
    argv = payload.get("argv")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return _deny("argv must be an array of strings", "input")
    if not 2 <= len(argv) <= MAX_ARGS:
        return _deny("argv must have between 2 and 64 elements", "argv")
    if any(len(a.encode("utf-8", "surrogatepass")) > MAX_ARG_BYTES for a in argv):
        return _deny("an argv element exceeds 8192 bytes", "argv")
    if sum(len(a.encode("utf-8", "surrogatepass")) for a in argv) > MAX_TOTAL_BYTES:
        return _deny("argv exceeds 32768 bytes", "argv")
    for arg in argv:
        if any(ch in arg for ch in CONTROL):
            return _deny("control characters are not allowed in argv", "argv")
    if argv[0] != "curl":
        return _deny("only curl is allowed", "argv")
    ref = payload.get("ref")
    if not isinstance(ref, str) or not REF_RE.match(ref):
        return _deny("missing or malformed secret reference", "ref")
    if sum(arg.count(ref) for arg in argv) != 1:
        return _deny("the secret reference must occur exactly once", "ref")
    env_key = payload.get("env_key")
    if not isinstance(env_key, str) or not env_key or env_key != env_key.strip():
        return _deny("missing env key", "input")
    grants, why = load_grants(grants_path_override)
    if grants is None:
        return _deny(why, "grants")
    if env_key not in grants:
        return _deny(f"no grant for env key {env_key}", "grants")
    urls, why = _parse_argv(argv)
    if why:
        return _deny(why, "option")
    if not urls:
        return _deny("at least one URL is required", "url")
    origins, indices, hosts = [], [], []
    for index, raw in urls:
        url, why = _url(raw)
        if url is None:
            return _deny(why, "url")
        ok, matched = _granted(url, grants[env_key])
        if not ok:
            return _deny(f"host {url['host']!r} is not granted for {env_key}", "url")
        origins.append(url["origin"])
        indices.append(index)
        hosts.append(matched)
    return {"allow": True, "reason": "granted", "rule": "grant",
            "origins": origins, "url_indices": indices, "hosts": hosts}


def check(payload, grants_path_override: str | Path | None = None) -> dict:
    """Validate an exec request before substitution. Never raises."""
    try:
        return _check(payload, grants_path_override)
    except Exception as e:  # a policy crash is a deny, not an allow
        return _deny(f"policy error: {e}", "internal")


__all__ = ["check", "load_grants", "grants_path"]
