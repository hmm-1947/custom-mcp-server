"""Repository state.

Knowing the git state before editing is what keeps a change honest: it separates
pre-existing modifications from your own, exposes an in-progress merge, and lets
the final diff be reviewed as evidence rather than described from memory.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from harness import gitstate
from ._common import context, respond, tool_schema

GIT_SCHEMA = tool_schema({
    "action": {"type": "string"},
    "is_repo": {"type": "boolean"},
    "branch": {"type": ["string", "null"]},
    "entries": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "diff": {"type": "string"},
    "log": {"type": "array", "items": {"type": "string"}},
    "branches": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "conflicted_files": {"type": "array", "items": {"type": "string"}},
})

ACTIONS = ("status", "diff", "staged_diff", "log", "branch", "conflicts", "overview")


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=GIT_SCHEMA)
    def git_state(
        workspace: str,
        action: str = "status",
        paths: list[str] | None = None,
        count: int = 10,
        stat_only: bool = False,
        context_lines: int = 3,
    ) -> dict:
        """Read git state. Do this before editing and again before reporting.

        Actions:
          status      branch, upstream, ahead/behind, staged/unstaged/untracked,
                      conflicts, merge or rebase in progress
          diff        unstaged changes (use paths=[...] to narrow, stat_only=True
                      for a summary)
          staged_diff staged changes
          log         recent commits (count=N, paths=[...] to follow one file)
          branch      local branches and the current one
          conflicts   conflicted files with their marker line numbers
          overview    status + log + diff stat in one call

        This is read-only. Committing, resetting and pushing are left to the user.
        """
        session, root = context(workspace)
        root = Path(root)
        action = (action or "status").strip().lower()
        if action not in ACTIONS:
            raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")

        print(f"[harness] git_state(action={action!r})")
        session.record_command(
            {"command": f"git_state:{action}", "exit_code": 0, "duration_ms": 0, "ok": True},
            purpose="git",
        )

        if action == "status":
            payload = {"action": action, **gitstate.status(root)}
            hints = _status_hints(payload)
            return respond(session, "git_state", payload, hints=hints)

        if action in ("diff", "staged_diff"):
            payload = {
                "action": action,
                **gitstate.diff(root, paths, staged=action == "staged_diff",
                                stat_only=stat_only, context=context_lines),
            }
            hints = []
            if payload.get("empty"):
                hints.append(
                    "Empty diff. If you believe you edited something, the write did not land - "
                    "re-read the file and check task(action='status')."
                )
            return respond(session, "git_state", payload, hints=hints)

        if action == "log":
            return respond(session, "git_state", {
                "action": action,
                **gitstate.log(root, count=count, path=(paths or [None])[0]),
            })

        if action == "branch":
            return respond(session, "git_state", {"action": action, **gitstate.branches(root)})

        if action == "conflicts":
            payload = {"action": action, **gitstate.conflict_details(root)}
            hints = []
            if payload["conflicted_files"]:
                hints.append(
                    "Read each conflicted hunk with read_files and resolve it with a localized edit; "
                    "do not overwrite the whole file."
                )
            return respond(session, "git_state", payload, hints=hints)

        status = gitstate.status(root)
        payload = {
            "action": "overview",
            **status,
            "recent_commits": gitstate.log(root, count=count)["log"],
            "diff_stat": gitstate.diff(root, paths, stat_only=True)["diff"],
        }
        return respond(session, "git_state", payload, hints=_status_hints(status))


def _status_hints(status: dict) -> list[str]:
    hints: list[str] = []
    if not status.get("is_repo"):
        return ["No git safety net here: there is no way to undo an edit. Keep changes minimal."]
    if status.get("conflicts"):
        hints.append("Unresolved merge conflicts: git_state(action='conflicts') before any other edit.")
    if status.get("merge_in_progress") or status.get("rebase_in_progress"):
        hints.append("A merge or rebase is in progress. Finish or abort it before starting new work.")
    if status.get("dirty_count"):
        hints.append(
            f"{status['dirty_count']} pre-existing modification(s). Do not attribute them to your own change."
        )
    return hints
