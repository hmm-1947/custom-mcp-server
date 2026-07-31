"""Entry point.

The server advertises its methodology in `instructions`, which MCP clients
receive during initialization. That is what makes a connected LLM follow the
workflow rather than treating these as loose file-editing tools; the gates in
`harness/` are what make it stick.
"""

import argparse

from fastmcp import FastMCP

from harness.instructions import SERVER_INSTRUCTIONS
from tools import register_tools

mcp = FastMCP("Joshua MCP", instructions=SERVER_INSTRUCTIONS)

register_tools(mcp)


def main() -> None:
    parser = argparse.ArgumentParser(description="Joshua MCP engineering harness")
    parser.add_argument("--transport", default="streamable-http",
                        choices=("streamable-http", "sse", "stdio"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    arguments = parser.parse_args()

    if arguments.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=arguments.transport, host=arguments.host, port=arguments.port)


if __name__ == "__main__":
    main()
