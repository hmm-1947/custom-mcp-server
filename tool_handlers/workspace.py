from fastmcp import FastMCP

from config import add_workspace, list_workspaces, remove_workspace
from .schemas import WORKSPACE_OUTPUT_SCHEMA


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=WORKSPACE_OUTPUT_SCHEMA)
    def workspace(action: str, name: str = "", path: str = ""):
        """Manage workspaces: list, add, or remove."""
        print(f"[tool] workspace(action={action!r}, name={name!r})")

        action = action.lower()
        if action == "list":
            return {"workspaces": list_workspaces()}
        if action == "add":
            if not name or not path:
                raise ValueError("name and path are required")
            add_workspace(name, path)
            return {"message": f"Workspace '{name}' added"}
        if action == "remove":
            if not name:
                raise ValueError("name is required")
            remove_workspace(name)
            return {"message": f"Workspace '{name}' removed"}

        raise ValueError("action must be one of: list, add, remove")
