"""Tool registration.

Five tools, registered in the order they are normally used:

    workspace   saved project roots
    find        search / tree / files / symbols / impact
    read        file contents with line numbers
    edit        localized changes and new files
    run         any shell command, anywhere on this machine
"""

from fastmcp import FastMCP

from tool_handlers.edit import register as register_edit
from tool_handlers.find import register as register_find
from tool_handlers.read import register as register_read
from tool_handlers.run import register as register_run
from tool_handlers.workspace import register as register_workspace


def register_tools(mcp: FastMCP) -> None:
    register_workspace(mcp)
    register_find(mcp)
    register_read(mcp)
    register_edit(mcp)
    register_run(mcp)
