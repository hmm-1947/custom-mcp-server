"""Shared plumbing for tool handlers.

Handlers are deliberately thin. Each one resolves the workspace, delegates to
the harness, records what happened on the session, and returns the payload with
the workflow envelope attached. No handler decides workflow policy itself.
"""

from __future__ import annotations

from pathlib import Path

from config import get_workspace
from harness.guidance import envelope, refusal
from harness.session import SESSIONS, TaskSession

WORKFLOW_ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "goal": {"type": "string"},
        "phase": {"type": "string"},
        "phase_goal": {"type": "string"},
        "last_tool": {"type": "string"},
        "progress": {"type": "object", "additionalProperties": True},
        "outstanding": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    },
}


def tool_schema(properties: dict | None = None) -> dict:
    """An output schema that always carries the workflow envelope."""
    return {
        "type": "object",
        "properties": {**(properties or {}), "workflow": WORKFLOW_ENVELOPE_SCHEMA},
        "additionalProperties": True,
    }


def context(workspace: str) -> tuple[TaskSession, Path]:
    """Resolve a workspace to (active session, absolute root)."""
    root = get_workspace(workspace)
    return SESSIONS.require(workspace, root), root


def respond(session: TaskSession, tool: str, payload: dict, hints: list[str] | None = None) -> dict:
    payload["workflow"] = envelope(session, tool, hints)
    session.persist()
    return payload


def blocked(session: TaskSession, tool: str, reason: str, required: list[str], **extra) -> dict:
    session.persist()
    return refusal(session, tool, reason, required, **extra)


def relative(root: Path, path: Path | str) -> str:
    """Workspace-relative POSIX path; the ledger is keyed on these."""
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def line_window(content: str, start: int, end: int, window: int) -> str:
    lines = content.splitlines()
    if not lines:
        return "[empty file]"
    first = max(1, start - window)
    last = min(len(lines), end + window)
    body = "\n".join(f"{number:>5} | {lines[number - 1]}" for number in range(first, last + 1))
    return f"[showing lines {first}-{last} of {len(lines)} total lines]\n{body}"
