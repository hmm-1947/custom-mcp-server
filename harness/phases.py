"""The engineering workflow this server enforces.

The harness is a state machine, not a bag of tools. Every tool call moves a
task through these phases, and the phase decides what the connected LLM is
allowed (and told) to do next.
"""

from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    IDLE = "idle"
    ORIENT = "orient"
    EXPLORE = "explore"
    READ = "read"
    EDIT = "edit"
    VALIDATE = "validate"
    DIAGNOSE = "diagnose"
    REPAIR = "repair"
    BLOCKED = "blocked"
    DONE = "done"


#: Linear happy path. DIAGNOSE/REPAIR are a loop back into VALIDATE.
ORDER = (
    Phase.ORIENT,
    Phase.EXPLORE,
    Phase.READ,
    Phase.EDIT,
    Phase.VALIDATE,
    Phase.DONE,
)

GOALS: dict[Phase, str] = {
    Phase.IDLE: "No active task. Call task(action='start', goal=...) before touching code.",
    Phase.ORIENT: (
        "Understand the ground truth of the repository before forming a plan: "
        "project type, toolchain, git state, and what is already uncommitted."
    ),
    Phase.EXPLORE: (
        "Locate every place the task touches. Search for the symbols, functions, "
        "classes, routes, models, schemas, migrations, APIs, config keys and call "
        "sites involved. Breadth first; do not read whole files yet."
    ),
    Phase.READ: (
        "Read the exact regions you are about to change, plus their surrounding "
        "context, so the edit is based on the real current text and not memory."
    ),
    Phase.EDIT: (
        "Make the smallest correct, localized change. Preserve surrounding "
        "formatting. One concern per edit."
    ),
    Phase.VALIDATE: (
        "Prove the change with executed commands, not reasoning. Run the derived "
        "validation plan and capture exit codes and output."
    ),
    Phase.DIAGNOSE: (
        "A validation command failed. Determine whether the cause is the code you "
        "wrote or the environment, using terminal commands to gather evidence."
    ),
    Phase.REPAIR: (
        "Fix the diagnosed cause with another localized edit, then validate again. "
        "Never declare success on an unvalidated repair."
    ),
    Phase.BLOCKED: (
        "A genuine external limitation was recorded. Report it to the user with the "
        "evidence gathered; do not silently rewrite working code to dodge it."
    ),
    Phase.DONE: "Validation passed with no edits after it. Task closed.",
}

#: Phases in which making an edit is the expected action.
EDIT_PHASES = frozenset({Phase.EDIT, Phase.REPAIR, Phase.READ})


def goal_of(phase: Phase) -> str:
    return GOALS.get(phase, "")


def advance(phase: Phase) -> Phase:
    """Return the next phase on the happy path."""
    if phase in (Phase.DIAGNOSE, Phase.REPAIR):
        return Phase.VALIDATE
    try:
        index = ORDER.index(phase)
    except ValueError:
        return Phase.ORIENT
    return ORDER[min(index + 1, len(ORDER) - 1)]
