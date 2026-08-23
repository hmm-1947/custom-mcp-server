"""Support modules for the tools.

    terminal    the only place a shell command is executed
    search      the only place a text search is executed
    structure   cheap bracket-balance guard for edits
    impact      what else a change to a file may affect
    instructions the short contract sent to every connected client
"""

from . import impact, instructions, search, structure, terminal  # noqa: F401

__all__ = ["impact", "instructions", "search", "structure", "terminal"]
