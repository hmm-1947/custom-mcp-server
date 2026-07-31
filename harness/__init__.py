"""The engineering harness that drives this MCP server.

Layers, lowest to highest:

    terminal    - the only place a command is executed
    ripgrep     - the only place a search is executed
    structure   - cheap in-process syntax guard for edits
    project     - detects the project and derives its validation plan
    gitstate    - repository state before/after changes
    diagnose    - classifies failures as code vs environment
    impact      - finds what else a change affects
    session     - per-task ledger and phase state machine
    guidance    - turns ledger state into the next required action
    phases      - the workflow definition itself

`tool_handlers` are thin adapters: they validate input, call into these modules,
record the outcome on the session, and attach the guidance envelope.
"""

from . import (  # noqa: F401
    diagnose,
    gitstate,
    guidance,
    impact,
    instructions,
    phases,
    project,
    ripgrep,
    session,
    structure,
    terminal,
)
from .guidance import envelope, refusal  # noqa: F401
from .phases import Phase  # noqa: F401
from .session import SESSIONS, TaskSession  # noqa: F401

__all__ = [
    "SESSIONS", "Phase", "TaskSession", "diagnose", "envelope", "gitstate",
    "guidance", "impact", "instructions", "phases", "project", "refusal",
    "ripgrep", "session", "structure", "terminal",
]
