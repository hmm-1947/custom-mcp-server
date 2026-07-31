"""Terminal access for the connected model.

Reasoning about a project is not the same as knowing it. This tool exists so
every claim can be grounded in an executed command, and so environment faults
can be investigated instead of assumed.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from config import resolve_path
from harness import diagnose, terminal as runner
from ._common import context, respond, tool_schema

RUN_SCHEMA = tool_schema({
    "command": {"type": "string"},
    "cwd": {"type": "string"},
    "exit_code": {"type": ["integer", "null"]},
    "stdout": {"type": "string"},
    "stderr": {"type": "string"},
    "duration_ms": {"type": "integer"},
    "ok": {"type": "boolean"},
    "timed_out": {"type": "boolean"},
    "clipped": {"type": "boolean"},
    "refused": {"type": ["string", "null"]},
    "diagnosis": {"type": "object", "additionalProperties": True},
    "environment": {"type": "object", "additionalProperties": True},
})

SUGGESTIONS = {
    "explore": [
        "rg --files | head -50",
        "find . -maxdepth 2 -type d -not -path '*/.*' | head -40",
        "ls -la",
    ],
    "inspect": [
        "sed -n '1,80p' <file>",
        "rg -n '<symbol>' --stats",
        "wc -l <file>",
    ],
    "environment": [
        "which python3 pip git",
        "python3 -c 'import sys; print(sys.executable, sys.version)'",
        "env | sort | head -40",
    ],
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=RUN_SCHEMA)
    def run(
        workspace: str,
        command: str = "",
        path: str = ".",
        timeout: int = 300,
        purpose: str = "",
        list_tools: bool = False,
        probe_versions: bool = False,
    ) -> dict:
        """Run a shell command inside the workspace and capture everything.

        Returns stdout, stderr, exit code and wall-clock duration, plus an
        automatic diagnosis when the command fails - including whether the cause
        looks like the code or the environment.

        Choose commands from evidence about this specific project rather than a
        fixed routine:

          explore    find, ls, tree, rg, grep, sed -n, awk, head, tail, wc
          inspect    cat, stat, file, diff, which, pwd, env
          vcs        git status / diff / log / branch  (or the git_state tool)
          validate   python3 -m py_compile, pytest, ruff, mypy, dart analyze,
                     flutter analyze/test, npm|yarn|pnpm run <script>,
                     tsc --noEmit, cargo check/test, go build/vet/test,
                     gradle, mvn, cmake, make, pip, uv

        Pipes, redirection and shell operators work. Destructive commands
        (recursive deletes of /, block-device writes, force pushes, hard resets,
        git clean -f, piping remote scripts into a shell) are refused - ask the
        user to run those.

        Set list_tools=True to see which development tools exist on this machine
        before depending on one.
        """
        session, root = context(workspace)
        cwd = resolve_path(workspace, path) if path not in ("", ".") else Path(root)

        if list_tools:
            environment = runner.describe_environment(cwd, probe_versions=probe_versions)
            session.record_command(
                {"command": "<probe available tools>", "exit_code": 0, "duration_ms": 0, "ok": True},
                purpose="environment probe",
            )
            return respond(session, "run", {
                "command": "<probe available tools>", "cwd": str(cwd), "exit_code": 0,
                "stdout": "", "stderr": "", "duration_ms": 0, "ok": True,
                "environment": environment,
            })

        if not command.strip():
            return respond(session, "run", {
                "command": "", "cwd": str(cwd), "exit_code": None, "stdout": "",
                "stderr": "No command given.", "duration_ms": 0, "ok": False,
                "suggestions": SUGGESTIONS,
            })

        print(f"[harness] run({command[:120]!r}) in {cwd}")
        result = runner.execute(command, cwd, timeout=max(1, int(timeout)))
        payload = result.to_dict()
        session.record_command(payload, purpose=purpose or "manual")

        hints: list[str] = []
        if result.refused:
            # A refusal is a policy outcome, not a failure to diagnose.
            hints.append(
                f"This command was blocked ({result.refused}) and did not run. "
                "Ask the user to run it if it is genuinely required."
            )
        elif not result.ok:
            analysis = diagnose.analyze(
                command=result.command, exit_code=result.exit_code, stdout=result.stdout,
                stderr=result.stderr, timed_out=result.timed_out, root=Path(root),
            )
            payload["diagnosis"] = analysis
            hints.append(analysis["next"])
            if analysis["classification"] == "environment":
                hints.extend(f"run(command={item!r})" for item in analysis["investigate"][:3])
        elif purpose:
            session.note(f"{purpose}: {result.command} -> exit 0")

        return respond(session, "run", payload, hints=hints)
