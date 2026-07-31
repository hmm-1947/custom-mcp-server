"""Terminal access.

Validation and reasoning must come from executed commands, not from what a
model believes about a project. This module is the single execution point:
everything else in the harness (validation plans, git state, environment
probes, diagnostics) runs through `execute`, so every command is uniformly
timed, clipped, recorded and confined to the workspace.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Commands the harness expects to be able to reach for exploration,
#: inspection, validation and environment diagnosis. Presence is probed, never
#: assumed - a missing tool is a fact to report, not a reason to guess.
KNOWN_TOOLS = (
    # exploration / inspection
    "find", "grep", "rg", "sed", "awk", "head", "tail", "cat", "ls", "tree",
    "pwd", "which", "file", "wc", "diff", "stat", "env",
    # vcs
    "git",
    # language toolchains
    "python", "python3", "pip", "pip3", "uv", "pytest", "ruff", "mypy",
    "flutter", "dart", "node", "npm", "yarn", "pnpm", "npx", "tsc",
    "cargo", "rustc", "go", "java", "javac", "gradle", "mvn",
    "cmake", "make", "ninja", "gcc", "clang", "docker",
)

#: Refused outright. Not a sandbox - a guard against catastrophic typos in a
#: loop that is allowed to run commands unattended.
DESTRUCTIVE_PATTERNS = (
    (r"\brm\s+(-[a-zA-Z]*\s+)*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/(\s|$)", "recursive delete of /"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(~|\$HOME)(/\s*)?$", "recursive delete of home directory"),
    (r"\bmkfs(\.|\s)", "filesystem format"),
    (r"\bdd\b[^\n]*\bof=/dev/(sd|nvme|hd|disk)", "raw write to a block device"),
    (r">\s*/dev/(sd|nvme|hd|disk)", "raw write to a block device"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "host power state change"),
    (r":\(\)\s*\{\s*:\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bchmod\s+-R\s+777\s+/(\s|$)", "recursive permission wipe of /"),
    (r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b", "piping a remote script into a shell"),
    (r"\bgit\s+push\b[^\n]*--force", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "hard reset (discards uncommitted work)"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*f", "git clean (discards untracked work)"),
)

DEFAULT_TIMEOUT = 300
MAX_OUTPUT_CHARS = 24000


@dataclass
class CommandResult:
    command: str
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    refused: str | None = None
    clipped: bool = False
    started_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.refused is None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        return data

    def tail(self, limit: int = 2000) -> str:
        """Most diagnostic value per character: the end of stderr, then stdout."""
        blob = (self.stderr or "") + ("\n" if self.stderr and self.stdout else "") + (self.stdout or "")
        return blob[-limit:]


def refusal_reason(command: str) -> str | None:
    for pattern, label in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command):
            return label
    return None


def clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    """Keep the head and (larger) tail - failures surface at the end."""
    if len(text) <= limit:
        return text, False
    head = int(limit * 0.35)
    tail = limit - head
    omitted = len(text) - limit
    return (
        f"{text[:head]}\n...[{omitted} chars omitted from the middle]...\n{text[-tail:]}",
        True,
    )


def execute(
    command: str,
    cwd: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    env_overrides: dict[str, str] | None = None,
    allow_destructive: bool = False,
) -> CommandResult:
    """Run a shell command in `cwd` and capture everything about it."""
    cwd = Path(cwd)
    started = time.perf_counter()

    reason = None if allow_destructive else refusal_reason(command)
    if reason is not None:
        return CommandResult(
            command=command, cwd=str(cwd), exit_code=None, stdout="",
            stderr=(
                f"Refused: {reason}. This command was not run. If it is genuinely "
                "required, ask the user to run it themselves."
            ),
            duration_ms=0, refused=reason,
        )

    if not cwd.is_dir():
        return CommandResult(
            command=command, cwd=str(cwd), exit_code=None, stdout="",
            stderr=f"Working directory does not exist: {cwd}", duration_ms=0,
            refused="missing cwd",
        )

    environment = os.environ.copy()
    environment.setdefault("PYTHONUNBUFFERED", "1")
    environment.setdefault("CI", "1")          # keeps npm/flutter non-interactive
    environment.setdefault("NO_COLOR", "1")
    environment.setdefault("TERM", "dumb")
    if env_overrides:
        environment.update(env_overrides)

    try:
        process = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
            stdin=subprocess.DEVNULL,
        )
        stdout, stdout_clipped = clip(process.stdout or "")
        stderr, stderr_clipped = clip(process.stderr or "")
        return CommandResult(
            command=command, cwd=str(cwd), exit_code=process.returncode,
            stdout=stdout, stderr=stderr,
            duration_ms=int((time.perf_counter() - started) * 1000),
            clipped=stdout_clipped or stderr_clipped,
        )
    except subprocess.TimeoutExpired as expired:
        stdout, _ = clip(_decode(expired.stdout))
        stderr, _ = clip(_decode(expired.stderr))
        return CommandResult(
            command=command, cwd=str(cwd), exit_code=None, stdout=stdout,
            stderr=(stderr + f"\n[timed out after {timeout}s]").strip(),
            duration_ms=int((time.perf_counter() - started) * 1000), timed_out=True,
        )
    except OSError as error:
        return CommandResult(
            command=command, cwd=str(cwd), exit_code=None, stdout="",
            stderr=f"Could not start command: {error}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def _decode(blob) -> str:
    if blob is None:
        return ""
    if isinstance(blob, bytes):
        return blob.decode("utf-8", errors="replace")
    return str(blob)


def tool_path(name: str) -> str | None:
    return shutil.which(name)


_VERSION_FLAGS = {
    "java": "-version", "javac": "-version", "go": "version", "docker": "--version",
}
_VERSIONLESS = frozenset({
    "find", "grep", "sed", "awk", "head", "tail", "cat", "ls", "pwd", "which",
    "file", "wc", "stat", "env", "tree", "diff",
})


def describe_environment(cwd: Path, probe_versions: bool = False) -> dict:
    """Report which known development tools actually exist here."""
    available: dict[str, str] = {}
    missing: list[str] = []
    for name in KNOWN_TOOLS:
        path = tool_path(name)
        if path is None:
            missing.append(name)
            continue
        version = ""
        if probe_versions and name not in _VERSIONLESS:
            flag = _VERSION_FLAGS.get(name, "--version")
            result = execute(f"{name} {flag}", cwd, timeout=20)
            version = ((result.stdout or result.stderr).strip().splitlines() or [""])[0][:120]
        available[name] = version or path
    return {
        "cwd": str(cwd),
        "available": available,
        "missing": missing,
        "note": (
            "Absent tools are facts about this machine. If a validation step needs a "
            "missing tool, that is an environment limitation - record it with "
            "task(action='block'), do not rewrite working code around it."
        ),
    }
