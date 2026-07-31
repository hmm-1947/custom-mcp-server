"""Failure analysis.

When a validation command fails there are two very different situations, and
conflating them is the single most damaging mistake an automated editor makes:

  * the code is wrong        -> repair the code
  * the environment is wrong -> investigate with terminal commands and report

This module classifies a failure from its actual output, extracts concrete
file:line locations to re-read, and proposes the shell commands that would
confirm or refute an environment hypothesis. It never guesses silently: an
unclassified failure is reported as "unknown" with instructions to gather more
evidence rather than being assumed to be a code fault.
"""

from __future__ import annotations

import re
from pathlib import Path

#: (pattern, label, why it is environmental, commands that confirm it)
ENVIRONMENT_SIGNATURES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # dash/sh says "<name>: not found", bash says "command not found",
    # cmd says "not recognized as an internal or external command".
    (r"command not found|not recognized as an internal|^[^\n]*: not found", "missing executable",
     "the shell could not find the program at all",
     ("which {tool}", "ls -la $(dirname $(which {tool} 2>/dev/null) 2>/dev/null) 2>/dev/null", "echo $PATH")),
    (r"No such file or directory: ['\"]?(?P<detail>[^'\"\n]+)", "missing file or directory",
     "a path the tool expected does not exist",
     ("ls -la", "pwd", "git status --short")),
    # Covers both the traceback form (ModuleNotFoundError: No module named 'x')
    # and the bare form emitted by `python -m x` (No module named x).
    (r"No module named ['\"]?(?P<detail>[\w.]+)", "missing python dependency",
     "the module is absent from the interpreter being used, which may or may not be the project venv",
     ("python3 -c 'import sys; print(sys.executable)'",
      "python3 -m pip show {detail}",
      "ls -d venv .venv 2>/dev/null",
      "grep -rin '{detail}' requirements.txt pyproject.toml setup.py 2>/dev/null")),
    (r"error: unrecognized arguments|is not a recognized command|"
     r"No such command|Unknown command", "wrong tool invocation",
     "the tool exists but the command form is wrong for its installed version",
     ("{tool} --help | head -30", "{tool} --version")),
    (r"ImportError: cannot open shared object|libssl|GLIBC", "native library mismatch",
     "a shared library required by a compiled dependency is missing or the wrong version",
     ("ldd --version", "uname -a")),
    (r"Permission denied|EACCES|Operation not permitted", "permissions",
     "the process is not allowed to read/write/execute the target",
     ("ls -la", "id", "stat .")),
    (r"externally-managed-environment", "pep 668 interpreter",
     "the system interpreter refuses installs; a virtualenv is required",
     ("ls -d venv .venv 2>/dev/null", "python3 -m venv --help | head -3")),
    (r"Could not resolve host|Temporary failure in name resolution|Network is unreachable|"
     r"ETIMEDOUT|ECONNREFUSED|Connection refused|SSL: CERTIFICATE_VERIFY_FAILED|"
     r"proxyconnect|EAI_AGAIN", "network unavailable",
     "the command needed the network and could not reach it",
     ("git remote -v", "env | grep -i proxy")),
    (r"No space left on device|ENOSPC", "disk full",
     "the filesystem is out of space",
     ("df -h .", "du -sh . 2>/dev/null | tail -1")),
    (r"Unable to find git in your PATH|Flutter SDK not found|ANDROID_HOME|"
     r"No Android SDK found|Xcode installation is incomplete", "incomplete sdk install",
     "the language SDK itself is not fully configured on this machine",
     ("flutter doctor -v", "which flutter dart", "echo $ANDROID_HOME")),
    (r"Cannot connect to the Docker daemon|docker: error during connect", "docker daemon down",
     "the docker service is not running or not reachable",
     ("docker info", "systemctl is-active docker 2>/dev/null")),
    (r"could not connect to server|password authentication failed|"
     r"Access denied for user|connection to server .* failed", "database unreachable",
     "the command needed a database that is not running or not credentialed here",
     ("env | grep -iE 'database|db_|postgres|mysql' | sed 's/=.*/=***/'",)),
    (r"error: pathspec|not a git repository", "git state",
     "the git working copy is not in the state the command assumed",
     ("git status --short", "git branch --show-current", "git log --oneline -3")),
    (r"npm ERR! code E(NOENT|NOTFOUND|AI_AGAIN)|Cannot find module ['\"](?P<detail>[^'\"]+)",
     "missing node dependency",
     "the package is not installed in node_modules",
     ("ls node_modules 2>/dev/null | head -5", "cat package.json")),
)

#: Definitely-the-code signatures. These override an ambiguous env guess.
CODE_SIGNATURES: tuple[tuple[str, str], ...] = (
    (r"SyntaxError|IndentationError|TabError", "python syntax error"),
    (r"NameError: name ['\"]?(?P<detail>\w+)", "undefined name"),
    (r"AttributeError:", "attribute error"),
    (r"TypeError:|ValueError:|KeyError:", "runtime type/value error"),
    (r"^\s*(FAILED|assert)\b|AssertionError", "failing assertion"),
    (r"error: expected|expected ';'|unexpected token|Unexpected token", "parse error"),
    (r"is not defined\b", "undefined reference"),
    (r"error\[E\d+\]", "rust compile error"),
    (r"cannot find symbol|incompatible types", "jvm compile error"),
    (r"error TS\d+", "typescript error"),
    (r"undefined: |declared and not used|missing return", "go compile error"),
    (r"\berror\s+•", "dart analyzer error"),
    (r"Unbalanced|unterminated string", "unterminated literal"),
)

#: file:line[:col] shapes emitted by py/dart/ts/go/rust/eslint/gcc toolchains.
LOCATION_PATTERNS = (
    re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)'),
    re.compile(r"(?P<file>[\w./\\@+-]+\.(?:py|dart|ts|tsx|js|jsx|go|rs|java|kt|c|cc|cpp|h|hpp))"
               r":(?P<line>\d+)(?::(?P<col>\d+))?"),
    re.compile(r"•\s*(?P<file>[\w./\\-]+\.dart):(?P<line>\d+):(?P<col>\d+)"),
)


def _blob(stdout: str, stderr: str) -> str:
    return f"{stderr or ''}\n{stdout or ''}"


def extract_locations(stdout: str, stderr: str, root: Path | None = None, limit: int = 12) -> list[dict]:
    """Pull concrete file:line references out of tool output, newest first.

    These are the regions to re-read before repairing - the harness pushes the
    LLM back to READ rather than letting it edit from imagination.
    """
    text = _blob(stdout, stderr)
    seen: set[tuple[str, int]] = set()
    found: list[dict] = []
    for pattern in LOCATION_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group("file")
            try:
                line = int(match.group("line"))
            except (TypeError, ValueError):
                continue
            if "site-packages" in raw or "/lib/python" in raw or "node_modules" in raw:
                continue
            path = raw
            if root is not None:
                try:
                    candidate = Path(raw)
                    if candidate.is_absolute():
                        path = str(candidate.relative_to(root))
                except ValueError:
                    path = raw
            key = (path, line)
            if key in seen:
                continue
            seen.add(key)
            found.append({"file": path, "line": line, "excerpt": _excerpt(text, match.start())})
    # Last frame of a traceback is where the error actually happened.
    found.reverse()
    return found[:limit]


def _excerpt(text: str, index: int, width: int = 160) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    end = len(text) if end < 0 else end
    return text[start:end].strip()[:width]


def _format(commands: tuple[str, ...], detail: str | None, tool: str | None) -> list[str]:
    rendered: list[str] = []
    for command in commands:
        try:
            rendered.append(command.format(detail=detail or "", tool=tool or ""))
        except (KeyError, IndexError):
            rendered.append(command)
    return [command for command in rendered if "{" not in command]


def _guess_tool(command: str) -> str:
    parts = command.replace("'", " ").split()
    return parts[0].rsplit("/", 1)[-1] if parts else ""


def analyze(
    *,
    command: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    timed_out: bool = False,
    root: Path | None = None,
) -> dict:
    """Classify a command failure and say what to do about it."""
    if exit_code == 0 and not timed_out:
        return {"classification": "pass", "signals": [], "locations": [], "investigate": [], "summary": "passed"}

    text = _blob(stdout, stderr)
    tool = _guess_tool(command)

    code_signals: list[dict] = []
    for pattern, label in CODE_SIGNATURES:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            detail = match.groupdict().get("detail") if match.groupdict() else None
            code_signals.append({"label": label, "evidence": _excerpt(text, match.start()), "detail": detail})

    environment_signals: list[dict] = []
    investigate: list[str] = []
    for pattern, label, why, commands in ENVIRONMENT_SIGNATURES:
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            continue
        detail = match.groupdict().get("detail") if match.groupdict() else None
        environment_signals.append({
            "label": label, "why": why, "detail": detail,
            "evidence": _excerpt(text, match.start()),
        })
        investigate.extend(_format(commands, detail, tool))

    locations = extract_locations(stdout, stderr, root)

    # Exit 127 is the shell's own "I could not run this", regardless of wording.
    if exit_code == 127 and not any(signal["label"] == "missing executable" for signal in environment_signals):
        environment_signals.append({
            "label": "missing executable",
            "why": "exit status 127 means the shell could not execute the command at all",
            "detail": tool, "evidence": f"exit 127 running {tool!r}",
        })
        investigate.extend([f"which {tool}", "echo $PATH"])

    if timed_out:
        classification = "environment"
        environment_signals.append({
            "label": "timeout", "why": "the command did not finish in the allotted time",
            "detail": None, "evidence": "timed out",
        })
        investigate.extend(["ps -o pid,etime,cmd -u $(id -u) | head -20"])
    elif code_signals and not environment_signals:
        classification = "code"
    elif environment_signals and not code_signals:
        classification = "environment"
    elif code_signals and environment_signals:
        # A real compiler/analyzer error outranks an incidental env-looking line.
        classification = "code"
    elif locations:
        classification = "code"
    else:
        classification = "unknown"

    summary = _summarize(classification, code_signals, environment_signals, exit_code, timed_out)
    return {
        "classification": classification,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "signals": code_signals + environment_signals,
        "code_signals": code_signals,
        "environment_signals": environment_signals,
        "locations": locations,
        "investigate": _dedupe(investigate),
        "summary": summary,
        "next": _next_step(classification, locations),
    }


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered[:8]


def _summarize(classification, code_signals, environment_signals, exit_code, timed_out) -> str:
    if timed_out:
        return "Command timed out; treat as an environment/perf limitation until proven otherwise."
    labels = [signal["label"] for signal in (code_signals if classification == "code" else environment_signals)]
    if classification == "code":
        return f"Code fault (exit {exit_code}): {', '.join(labels) or 'toolchain reported errors'}."
    if classification == "environment":
        return f"Environment fault (exit {exit_code}): {', '.join(labels)}. Do not edit code to work around this yet."
    return (
        f"Unclassified failure (exit {exit_code}). Gather evidence with the terminal before "
        "assuming the implementation is wrong."
    )


def _next_step(classification: str, locations: list[dict]) -> str:
    if classification == "code":
        if locations:
            targets = ", ".join(f"{item['file']}:{item['line']}" for item in locations[:3])
            return f"Re-read the reported region(s) ({targets}) with read_files, then make a localized repair edit."
        return "Re-read the region you last edited with read_files, then make a localized repair edit."
    if classification == "environment":
        return (
            "Run the suggested investigate commands with run(). If they confirm an external "
            "limitation, record it with task(action='block'). Do not modify working code."
        )
    return (
        "Run the failing command again with more verbosity, plus `pwd`, `ls -la` and `git status "
        "--short`, to decide whether this is the code or the environment."
    )
