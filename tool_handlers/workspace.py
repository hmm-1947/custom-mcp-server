"""Saved project roots."""

from __future__ import annotations

from fastmcp import FastMCP

from config import add_workspace, list_workspaces, remove_workspace

ACTIONS = ("list", "add", "remove")


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def workspace(action: str = "list", name: str = "", path: str = "") -> dict:
        """List, add or remove saved project directories.

        An alias saves repeating a long path: pass workspace='<alias>' to find,
        read and edit, or cwd='<alias>' to run. Absolute paths work everywhere
        too, so this is a convenience, not a requirement.
        """
        action = (action or "list").strip().lower()
        if action not in ACTIONS:
            raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")

        if action == "add":
            if not name.strip() or not path.strip():
                raise ValueError("name and path are both required to add a workspace")
            root = add_workspace(name.strip(), path.strip())
            return {"message": f"'{name}' -> {root}", "workspaces": list_workspaces()}

        if action == "remove":
            if not name.strip():
                raise ValueError("name is required to remove a workspace")
            remove_workspace(name.strip())
            return {"message": f"'{name}' removed.", "workspaces": list_workspaces()}

        return {"workspaces": list_workspaces()}
