from pathlib import Path

from fastmcp import FastMCP

from config import add_workspace, list_workspaces, remove_workspace
from .schemas import WORKSPACE_OUTPUT_SCHEMA


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=WORKSPACE_OUTPUT_SCHEMA)
    def workspace(action: str, name: str = "", path: str = ""):
        import traceback

        print("=" * 80)
        print("workspace called")
        print(f"action={action!r}")
        print(f"name={name!r}")
        print(f"path={path!r}")

        try:
            action = action.lower()

            if action == "list":
                result = {"workspaces": list_workspaces()}
                print("RETURN:", result)
                return result

            if action == "add":
                print("Path exists:", Path(path).exists())
                print("Is dir:", Path(path).is_dir())

                add_workspace(name, path)

                result = {"message": f"Workspace '{name}' added"}
                print("RETURN:", result)
                return result

            if action == "remove":
                remove_workspace(name)
                result = {"message": f"Workspace '{name}' removed"}
                print("RETURN:", result)
                return result

            raise ValueError("invalid action")

        except Exception:
            traceback.print_exc()
            raise
