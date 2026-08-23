"""Shell access.

No workspace is required: a command runs wherever the caller points it, or in
the server's current directory when no location is given.
"""

from __future__ import annotations

from fastmcp import FastMCP

from config import resolve_dir
from core import terminal


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def run(command: str, cwd: str = "", timeout: int = 300) -> dict:
        """Run a shell command on this machine and capture the result.

        cwd: a workspace alias or a directory path. Omit it to run from the
        server's current directory. Pipes, redirection and shell operators work.

        Returns exit_code, stdout, stderr and duration. Use it for anything the
        other tools do not cover, and to check a change when the user asks for
        it (for example `flutter analyze`, `pytest`, `npm run build`).

        Destructive commands (recursive deletes of /, disk formats, force
        pushes, hard resets, piping remote scripts into a shell) are refused.
        """
        if not command.strip():
            raise ValueError("command is required")

        working_dir = resolve_dir(cwd)
        print(f"[run] {command[:160]!r} in {working_dir}")
        result = terminal.execute(command, working_dir, timeout=max(1, int(timeout)))
        return result.to_dict()
