"""Risk assessment: what may run unattended, and what must ask first.

The problem this solves: a blanket `permission.bash = "ask"` trains the owner to
approve reflexively — reads, greps and test runs prompt exactly as loudly as
`git push --force`, so the prompt stops carrying information. Approval should
mean "this one is destructive or irreversible", nothing else.

One source of truth, used by both halves of the story:

  * `assess(action)` — the classifier. The `risk_check` tool exposes it to the
    model, and the loop consults it before auto-approving.
  * `opencode_permission()` — the `permission` block `ensure_opencode_config`
    merges into opencode.json, so the rule the user sees in a prompt is
    literally the rule this module classified.

opencode evaluates granular rules by pattern with the LAST match winning, so
the maps below put a permissive catch-all first and the destructive patterns
after it.
"""
from __future__ import annotations

import re
from pathlib import Path

SAFE = "safe"
ELEVATED = "elevated"
DESTRUCTIVE = "destructive"

# (regex, why it is irreversible / destructive). Keep the reason phrased for a
# human deciding right now, not for a log line.
DESTRUCTIVE_RULES: tuple[tuple[str, str], ...] = (
    # --- data loss on disk -------------------------------------------------
    (r"\brm\b", "deletes files outright; no trash, not recoverable by git"),
    (r"\brmdir\b", "removes directories from disk"),
    (r"\btruncate\b", "truncates file contents in place"),
    (r"\bshred\b", "overwrites file contents irrecoverably"),
    (r"\bmkfs(\.\w+)?\b", "formats a filesystem"),
    (r"\bdd\b.*\bof=", "writes raw bytes to a device/file"),
    (r">\s*/dev/(sd|nvme|disk)", "writes raw bytes to a block device"),
    (r"\bmv\b.*\s/(\s|$)", "moves data to the filesystem root"),
    # --- history / remote rewrite -----------------------------------------
    # the specific force rule must precede the generic push rule (first match wins)
    (r"\bgit\s+push\b[^&|]*\s(--force|-f)\b", "force-pushes: the remote's commits are overwritten"),
    (r"\bgit\s+push\b", "publishes commits to a remote; other people may already have them"),
    (r"\bgit\s+reset\s+--hard\b", "discards uncommitted work with no recovery"),
    (r"\bgit\s+clean\b", "deletes untracked files"),
    (r"\bgit\s+(checkout|restore)\s+--\b", "overwrites working-tree changes"),
    (r"\bgit\s+branch\s+-D\b", "force-deletes a branch"),
    (r"\bgit\s+filter-branch\b", "rewrites repository history"),
    # --- privilege / host --------------------------------------------------
    (r"\bsudo\b", "runs as root, where a typo is unbounded"),
    (r"^\s*su\b", "switches to another user's full privileges"),
    (r"\bchmod\s+(-R\s+)?[0-7]*777\b", "makes files world-writable"),
    (r"\bchown\b", "changes ownership; can lock you out of your own files"),
    (r"\b(kill|pkill|killall)\b", "kills processes; unsaved work dies with them"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "powers off the host"),
    (r":\(\)\s*\{", "fork bomb"),
    # --- publish / deploy / spend -----------------------------------------
    (r"\b(npm|pnpm|yarn|bun)\s+publish\b", "publishes a package to a public registry"),
    (r"\btwine\s+upload\b", "publishes a package to PyPI"),
    (r"\b(docker|podman)\s+push\b", "pushes an image to a registry"),
    (r"\b(docker|podman)\s+(rm|rmi|prune)\b", "removes containers/images"),
    (r"\bgh\s+release\s+(create|delete)\b", "creates/deletes a public release"),
    (r"\b(vercel|netlify|fly|flyctl|railway|heroku)\b.*\b(deploy|destroy|delete)\b", "deploys/destroys live infrastructure"),
    (r"\bterraform\s+(apply|destroy)\b", "changes real infrastructure; destroy is not undoable"),
    (r"\bkubectl\s+(delete|apply|drain|cordon)\b", "changes a live cluster"),
    (r"\b(aws|gcloud|az)\b", "changes real cloud resources, often billable"),
    (r"\b(pip|pip3|npm|pnpm|yarn)\s+(install|i)\b.*(-g|--global)\b", "installs software system-wide"),
    # Installing software lands OUTSIDE the project (interpreter env, ~/.cache,
    # the OS) where checkpoints cannot undo it — and an unattended worker doing
    # it silently is exactly how a simple task starts mutating the system.
    # (Project-local `npm install` in node_modules stays unattended.)
    (r"\b(pip|pip3|pipx)\s+install\b", "installs into the interpreter's environment, outside the project — prefer the project venv"),
    (r"\bpython3?\s+(-m\s+)?pip\s+install\b", "installs into the interpreter's environment, outside the project — prefer the project venv"),
    (r"\buv\s+(tool|pip)\s+install\b", "installs into a shared tool environment, outside the project"),
    (r"\bplaywright\s+install\b", "downloads browser binaries into ~/.cache — hundreds of MB outside the project"),
    (r"\b(apt|apt-get|dnf|yum|brew|snap)\s+(install|upgrade)\b", "installs system packages via the OS package manager"),
    (r"\bpacman\s+(-S|--sync)\b", "installs system packages via the OS package manager"),
    (r"\bcurl\b.+\|\s*(ba)?sh\b", "pipes a remote script straight into a shell"),
    (r"\bwget\b.+\|\s*(ba)?sh\b", "pipes a remote script straight into a shell"),
    (r"\bpip\s+uninstall\b", "removes installed packages"),
    # --- irreversible database statements ---------------------------------
    (r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX)\b", "drops database objects and their data"),
    (r"\bTRUNCATE\s+TABLE\b", "empties a table with no undo"),
    (r"\bDELETE\s+FROM\b(?!.*\bWHERE\b)", "deletes every row (no WHERE clause)"),
    # --- harness's own irreversible operations -----------------------------
    (r"\bcheckpoint\s+restore\b", "resets files to a snapshot, discarding later edits"),
    (r"\bplugin\s+uninstall\b", "removes the installed plugin"),
    (r"\bconfig\s+set\b", "changes persisted config"),
    (r"\bforget\b", "deletes remembered facts"),
)

# Paths whose contents are secrets or credentials: writing them is effectively
# irreversible (the old value is gone and the new one leaks).
SENSITIVE_PATH_RULES: tuple[tuple[str, str], ...] = (
    (r"(^|/)\.env(\.|$)", "holds secrets; a write leaks or destroys credentials"),
    (r"(^|/)\.ssh/", "SSH keys and config"),
    (r"(^|/)\.aws/", "cloud credentials"),
    (r"(^|/)\.netrc$", "plaintext credentials"),
    (r"(^|/)id_(rsa|ed25519|ecdsa)", "private keys"),
    (r"(^|/)\.git/config$", "repository remotes and hooks"),
    (r"(^|/)\.github/workflows/", "CI config: a push will run it with secrets"),
)

# Scratch that escapes the project: the file never lived in the repo, so git
# and checkpoints cannot bring it back — same posture as `external_directory`
# (elevated + ask, NOT irreversible). The interpreter form is anchored to a
# command segment start so `grep python3 /tmp` (a read) never matches.
SCRATCH_RULES: tuple[tuple[str, str], ...] = (
    (r">\s*/tmp/", "writes scratch outside the project; checkpoints cannot undo it — keep it in .opencode/harness/tmp/"),
    (r"\btee\s+(-a\s+)?/tmp/", "writes scratch outside the project; checkpoints cannot undo it — keep it in .opencode/harness/tmp/"),
    (r"(^|[;&|]+\s*)(python3?|node|bun|deno|sh|bash)\s+[^;&|]*/tmp/", "runs a scratch file outside the project — keep it in .opencode/harness/tmp/"),
)

# Action keywords that never need approval: they observe, they do not change.
SAFE_KINDS = ("read", "glob", "grep", "list", "ls", "status", "doctor", "search",
              "fetch", "webfetch", "websearch", "recall", "memory_recall",
              "skill_view", "skills_list", "inspect", "diff", "log", "show",
              "cat", "head", "tail", "wc", "find", "query", "config_get",
              "risk_check", "subagents", "inbox", "result")

WRITE_KINDS = ("write", "edit", "patch", "create", "append", "config_set")

# Bash commands that are pure observation, matched after the destructive rules
# so `git log` is safe while `git push` still asks. Order matters: these run as
# an overlay that turns a matched command back to safe.
SAFE_BASH_PREFIXES = (
    "ls", "pwd", "cat", "head", "tail", "wc", "echo", "which", "type",
    "grep", "rg", "find", "fd", "tree", "stat", "file", "du", "df",
    "git status", "git diff", "git log", "git show", "git branch", "git remote",
    "git blame", "git stash list", "git rev-parse",
    "pytest", "python -m pytest", "python -m unittest", "cargo test",
    "npm test", "npm run test", "pnpm test", "bun test", "go test", "make test",
    "ruff", "flake8", "mypy", "tsc", "eslint", "pyright", "black --check",
    "python -m harness doctor", "harness doctor", "harness plan next",
    "opencode --version", "git fetch", "git ls-remote", "env", "date", "whoami",
)


def _matches(text: str, rules: tuple[tuple[str, str], ...]) -> tuple[str, str] | None:
    for pattern, why in rules:
        try:
            if re.search(pattern, text, re.I):
                return pattern, why
        except re.error:
            continue
    return None


def is_safe_bash(cmd: str) -> bool:
    """True when a shell command only observes. Destructive rules win outright.

    Prefix matching is on a word boundary, so `ls` never matches `lsof`.
    """
    text = normalize_bash(cmd)
    if not text:
        return True
    if _matches(text, DESTRUCTIVE_RULES):
        return False
    head = text.split("&&")[0].split("|")[0].split(";")[0].strip()
    return any(head == p or head.startswith(p + " ") for p in SAFE_BASH_PREFIXES)


def normalize_bash(cmd: str) -> str:
    """Collapse whitespace so `git  push   --force` matches `git push`."""
    return re.sub(r"\s+", " ", (cmd or "").strip())


def assess(action: str, kind: str = "auto", path: str = "", root: str | Path = "") -> dict:
    """Classify one action. Returns a verdict the caller can act on verbatim.

        {"action", "kind", "risk", "irreversible", "ask", "reason", "rule"}

    risk:        safe | elevated | destructive
    ask:         True only when a human decision is genuinely required
    irreversible: True when the change cannot be undone by git or a checkpoint
    """
    text = (action or "").strip()
    k = (kind or "auto").strip().lower()
    if k == "auto":
        k = _infer_kind(text)
    # A sensitive path is checked before anything else: reading a credential is
    # not destructive, but it is never plain "safe" either.
    sensitive = _matches(path.replace("\\", "/"), SENSITIVE_PATH_RULES) if path else None

    if k in SAFE_KINDS:
        if sensitive:
            return {"action": f"{text} -> {path}", "kind": k, "risk": ELEVATED,
                    "irreversible": False, "ask": False, "reason": sensitive[1],
                    "rule": "sensitive-path:" + sensitive[0]}
        return {"action": text, "kind": k, "risk": SAFE, "irreversible": False,
                "ask": False, "reason": "read-only: it observes state and changes nothing",
                "rule": None}

    if path:
        if sensitive:
            rule = "sensitive-path:" + sensitive[0]
            if k in WRITE_KINDS:
                return {"action": f"{text} -> {path}", "kind": k, "risk": DESTRUCTIVE,
                        "irreversible": True, "ask": True, "reason": sensitive[1],
                        "rule": rule}
            return {"action": f"{text} -> {path}", "kind": k, "risk": ELEVATED,
                    "irreversible": False, "ask": False, "reason": sensitive[1],
                    "rule": rule}
        outside = _outside(path, root)
        if outside and k in WRITE_KINDS:
            return {"action": f"{text} -> {path}", "kind": k, "risk": ELEVATED,
                    "irreversible": False, "ask": True,
                    "reason": f"writes outside the project root ({outside}); "
                              "checkpoints cannot undo it",
                    "rule": "external_directory"}

    if k == "shell":
        norm = normalize_bash(text)
        hit = _matches(norm, DESTRUCTIVE_RULES)
        if hit:
            return {"action": text, "kind": k, "risk": DESTRUCTIVE, "irreversible": True,
                    "ask": True, "reason": hit[1], "rule": hit[0]}
        hit = _matches(norm, SCRATCH_RULES)
        if hit:
            return {"action": text, "kind": k, "risk": ELEVATED, "irreversible": False,
                    "ask": True, "reason": hit[1], "rule": "scratch:" + hit[0]}
        if is_safe_bash(text):
            return {"action": text, "kind": k, "risk": SAFE, "irreversible": False,
                    "ask": False, "reason": "read-only command (inspect/test/lint)",
                    "rule": None}
        return {"action": text, "kind": k, "risk": ELEVATED, "irreversible": False,
                "ask": False, "reason": "not on the destructive list; recoverable with git",
                "rule": None}

    if k in WRITE_KINDS:
        return {"action": text, "kind": k, "risk": SAFE, "irreversible": False,
                "ask": False,
                "reason": "project edit: reviewable and recoverable via git/checkpoints",
                "rule": None}

    return {"action": text, "kind": k, "risk": ELEVATED, "irreversible": False,
            "ask": False, "reason": "unknown action shape; not known to be destructive",
            "rule": None}


def _infer_kind(text: str) -> str:
    """Guess the kind from the shape of the action when the caller gave none.

    A harness tool name as the first word (`read src/app.py`) is classified as
    that tool, so a read never gets treated as an opaque shell command.
    """
    t = (text or "").strip().lower()
    if not t:
        return "read"
    first = t.split()[0]
    if first in SAFE_KINDS:
        return first
    if first in WRITE_KINDS:
        return first
    if t.startswith(("http://", "https://", "fetch ", "webfetch ")) and "|" not in t:
        return "fetch"
    return "shell"


def _outside(path: str, root: str | Path) -> str:
    """Return the resolved path when it escapes root, else ""."""
    if not root or not path:
        return ""
    try:
        base = Path(root).resolve()
        p = Path(path)
        p = (base / p).resolve() if not p.is_absolute() else p.resolve()
    except Exception:
        return ""
    if p == base or base in p.parents:
        return ""
    return str(p)


def assess_many(actions: list, root: str | Path = "") -> dict:
    """Batch verdict for a plan: what may run, what needs the owner.

    The orchestrator calls this before a wave so it asks ONCE about the
    irreversible subset instead of once per action.
    """
    verdicts = []
    for item in actions or []:
        if isinstance(item, dict):
            verdicts.append(assess(str(item.get("action", "")), str(item.get("kind", "auto")),
                                   str(item.get("path", "")), root))
        else:
            verdicts.append(assess(str(item), root=root))
    needs = [v for v in verdicts if v["ask"]]
    return {
        "total": len(verdicts),
        "needs_approval": needs,
        "safe": [v for v in verdicts if not v["ask"]],
        "summary": ("nothing needs approval" if not needs else
                    f"{len(needs)} of {len(verdicts)} need approval: "
                    + "; ".join(f"{v['action'][:60]} — {v['reason']}" for v in needs)),
    }


# ---------------------------------------------------------------------------
# opencode config generation
# ---------------------------------------------------------------------------
# opencode matches the PARSED command (`git status --porcelain`), and "grep *"
# is needed for a command with arguments, so every destructive rule is emitted
# in a bare and an argument form. Last match wins.

_SAFE_BASH_LIST = (
    "ls", "pwd", "cat", "head", "tail", "wc", "grep", "rg", "find", "fd",
    "git status", "git diff", "git log", "git show", "git branch", "git fetch",
    "git blame", "git remote", "git rev-parse", "git stash list",
    "python", "python3", "pytest", "ruff", "mypy", "npm test", "npm run",
    "pnpm", "bun", "node", "go test", "cargo test", "make",
    "harness doctor", "harness plan", "harness memory search", "harness skills list",
    "opencode --version", "which", "echo", "du", "df", "stat", "env", "date",
)

_DESTRUCTIVE_BASH = (
    "rm", "rmdir", "shred", "truncate", "mkfs", "dd", "sudo", "su", "chown",
    "kill", "pkill", "killall", "shutdown", "reboot",
    "git push", "git reset", "git clean", "git checkout", "git restore",
    "git branch -D", "git filter-branch",
    "npm publish", "pnpm publish", "yarn publish", "bun publish",
    "pip uninstall", "twine upload", "docker push", "docker rm", "docker rmi",
    "docker prune", "podman push", "gh release",
    "terraform apply", "terraform destroy", "kubectl delete", "kubectl apply",
    "vercel", "netlify", "fly deploy", "heroku", "aws", "gcloud", "az",
    "harness checkpoint restore", "harness plugin uninstall", "harness config set",
    # installs that land outside the project (see DESTRUCTIVE_RULES above);
    # plain project-local `npm install` is deliberately absent.
    "pip install", "pip3 install", "pipx install",
    "python -m pip install", "python3 -m pip install",
    "uv tool install", "uv pip install",
    "playwright install", "npx playwright install", "bunx playwright install",
    "apt install", "apt-get install", "dnf install", "yum install",
    "brew install", "snap install", "pacman -S",
)

# Scratch outside the project (SCRATCH_RULES): emitted as prefix globs, since
# opencode matches the parsed command — redirects cannot be expressed as a
# prefix, so `> /tmp/...` is covered by assess() (risk_check) alone.
SCRATCH_BASH_PATTERNS = (
    "python /tmp*", "python3 /tmp*", "node /tmp*", "bun /tmp*",
    "deno /tmp*", "sh /tmp*", "bash /tmp*", "tee /tmp*", "tee -a /tmp*",
)


def bash_permission_map(allow_safe: bool = True) -> dict:
    """opencode `permission.bash` object: permissive by default, ask on damage.

    With `allow_safe` the only prompting rules are the destructive ones — this
    is the posture the owner chose: approval means "destructive or
    irreversible", not "you typed a shell command".
    """
    rules: dict[str, str] = {"*": "allow"}
    if not allow_safe:
        rules["*"] = "ask"
        for cmd in _SAFE_BASH_LIST:
            rules[cmd] = "allow"
            rules[f"{cmd} *"] = "allow"
    for cmd in _DESTRUCTIVE_BASH:
        rules[cmd] = "ask"
        rules[f"{cmd} *"] = "ask"
    for pat in SCRATCH_BASH_PATTERNS:
        rules[pat] = "ask"
    return rules


def opencode_permission(read_only: bool = False, allow_safe: bool = True) -> dict:
    """The `permission` block for a harness agent.

    Read-only agents deny `edit` outright (never a prompt: a prompt would imply
    a yes could unlock it) and still allow bash so opencode advertises its
    `shell` tool — a request with no `shell` trips zen's free-tier gate.
    """
    perm: dict = {
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "webfetch": "allow",
        "websearch": "allow",
        "task": "allow",
        "external_directory": "ask",
        "bash": bash_permission_map(allow_safe=allow_safe),
    }
    if read_only:
        perm["edit"] = "deny"
    return perm
