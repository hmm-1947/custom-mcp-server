from fastmcp import FastMCP

from tools import register_tools

mcp = FastMCP("Joshua MCP")

register_tools(mcp)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")