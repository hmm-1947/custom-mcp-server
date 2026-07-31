"""Tool registration.

The tools are registered in the order the workflow runs, which is also the order
a client lists them in:

    task            declare the goal, orient, check status, block, complete
    workspace       register/resolve project roots
    explore         map the repository
    find_references locate every affected symbol
    impact          find dependents and co-changing artefacts
    read_files      read the exact regions to change
    edit            make a localized change (gated on having read it)
    validate        execute the project's own checks and diagnose failures
    run             terminal access for investigation
    git_state       repository state before and after

No tool is meant to be used alone: each one reports into the active task and
returns the next required action.
"""

from fastmcp import FastMCP

from harness.instructions import WORKFLOW_PROMPT
from tool_handlers.edit import register as register_edit
from tool_handlers.explore import register as register_explore
from tool_handlers.git import register as register_git
from tool_handlers.impact import register as register_impact
from tool_handlers.read_files import register as register_read_files
from tool_handlers.references import register as register_references
from tool_handlers.task import register as register_task
from tool_handlers.terminal import register as register_terminal
from tool_handlers.validate import register as register_validate
from tool_handlers.workspace import register as register_workspace


def register_tools(mcp: FastMCP) -> None:
    register_task(mcp)
    register_workspace(mcp)
    register_explore(mcp)
    register_references(mcp)
    register_impact(mcp)
    register_read_files(mcp)
    register_edit(mcp)
    register_validate(mcp)
    register_terminal(mcp)
    register_git(mcp)

    @mcp.prompt(name="engineering_task")
    def engineering_task(goal: str) -> str:
        """Drive a change through the harness: orient, explore, read, edit, validate, repair."""
        return WORKFLOW_PROMPT.format(goal=goal)
