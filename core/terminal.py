"""Command execution.

Everything that runs a shell command goes through `execute`, so timing, output
clipping and the destructive-command guard behave identically everywhere.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Refused outright. Not a sandbox - a guard against catastrophic typos in a
#: loop that is allowed to run commands unattended.
DESTRUCTIVE_PATTERNS = (
    (r"\brm\s+(-[a-zA-Z]*\s+)*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/(\s|$)", "recursive delete of /"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(~|\$HOME)(/\s*)?$", "recursive delete of home directory"),
    (r"\bmkfs(\.|\s)", "filesystem format"),
    (r"\bformat\s+[a-zA-Z]:", "drive format"),
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
MAX_OUTPUT_CHARS = 20000


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
        data.pop("started_at", None)
        data["ok"] = self.ok
        if data["refused"] is None:
            data.pop("refused")
        if not data["clipped"]:
            data.pop("clipped")
        if not data["timed_out"]:
            data.pop("timed_out")
        return data


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
