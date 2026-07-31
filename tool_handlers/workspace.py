"""Workspace registration.

Workspaces are short aliases for project roots. Every other tool takes one, so
resolving them is the first thing a session does - a raw path guessed from
memory is the most common way a task starts off in the wrong repository.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from config import add_workspace, list_workspaces, remove_workspace
from harness import project

WORKSPACE_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "workspaces": {"type": "object", "additionalProperties": {"type": "string"}},
        "detected": {"type": "object", "additionalProperties": True},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}

ACTIONS = ("list", "add", "remove")


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=WORKSPACE_SCHEMA)
    def workspace(action: str = "list", name: str = "", path: str = "") -> dict:
        """List, add or remove project roots.

        Call this first in a session if you do not already know the aliases.
        An absolute path also works anywhere a workspace is expected, but a
        registered alias is preferred - it is what keeps a task pinned to one
        repository.
        """
        action = (action or "list").strip().lower()
        if action not in ACTIONS:
            raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")
        print(f"[harness] workspace(action={action!r}, name={name!r})")

        if action == "list":
            registered = list_workspaces()
            return {
                "workspaces": registered,
                "message": f"{len(registered)} workspace(s) registered.",
                "next_actions": [
                    "task(workspace=<alias>, action='start', goal='...') to begin work.",
                    "workspace(action='add', name=..., path=...) if the project you need is missing.",
                ],
            }

        if action == "add":
            if not name.strip() or not path.strip():
                raise ValueError("both name and path are required to add a workspace")
            add_workspace(name.strip(), path.strip())
            root = Path(path).resolve()
            return {
                "message": f"Workspace '{name}' -> {root}",
                "workspaces": list_workspaces(),
                "detected": project.detect(root),
                "next_actions": [
                    f"task(workspace='{name}', action='start', goal='...')",
                    f"task(workspace='{name}', action='orient') to confirm the toolchain and git state.",
                ],
            }

        if not name.strip():
            raise ValueError("name is required to remove a workspace")
        remove_workspace(name.strip())
        return {"message": f"Workspace '{name}' removed.", "workspaces": list_workspaces()}
