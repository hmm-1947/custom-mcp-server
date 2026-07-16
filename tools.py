"""Register the MCP tools exposed by this server."""

from fastmcp import FastMCP

from tool_handlers.edit import register as register_edit
from tool_handlers.explore import register as register_explore
from tool_handlers.read_files import register as register_read_files
from tool_handlers.references import register as register_references
from tool_handlers.workspace import register as register_workspace


def register_tools(mcp: FastMCP) -> None:
    """Attach every individual tool module to the MCP server."""
    register_workspace(mcp)
    register_explore(mcp)
    register_read_files(mcp)
    register_edit(mcp)
    register_references(mcp)
