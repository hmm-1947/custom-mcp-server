"""Next-step guidance.

Every tool response carries a `workflow` envelope produced here. This is the
mechanism that makes a connected model follow the methodology without being
told to in a prompt: the answer to "what do I do now" is computed from the
recorded state of the task, not left to the model's discretion.
"""

from __future__ import annotations

from .phases import Phase, goal_of
from .session import TaskSession


def envelope(session: TaskSession, tool: str, hints: list[str] | None = None) -> dict:
    ledger = session.ledger()
    actions = _unique((hints or []) + next_actions(session))
    return {
        "task_id": session.task_id,
        "goal": session.goal,
        "phase": session.phase.value,
        "phase_goal": goal_of(session.phase),
        "last_tool": tool,
        "progress": {
            "searched": len(ledger["files_searched"]),
            "read": len(ledger["files_read"]),
            "edited": len(ledger["files_edited"]),
            "commands_run": ledger["commands_run"],
            "validation_attempts": ledger["validation_attempts"],
        },
        "outstanding": outstanding(session),
        "next_actions": actions[:6],
    }


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def outstanding(session: TaskSession) -> list[str]:
    """Everything the methodology still requires for this task."""
    items: list[str] = []
    if session.goal.startswith("(implicit"):
        items.append("No goal declared. Call task(action='start', goal='...') so validation can be scoped.")
    if session.profile is None:
        items.append("Project type not yet detected. Call task(action='orient').")
    if not session.explored():
        items.append("No search performed yet. Locate the affected code before editing.")
    stale = session.stale_reads()
    if stale:
        items.append(
            "Stale reads (file changed on disk since you read it): "
            + ", ".join(item["path"] for item in stale[:5])
            + ". Re-read before editing."
        )
    unvisited = [
        path for path in session.impact_candidates
        if path not in session.read_files()
    ]
    if unvisited:
        items.append(
            "Impact analysis flagged files you have not opened: "
            + ", ".join(unvisited[:5])
            + ". Confirm whether they also need changes."
        )
    dirty = session.dirty_files()
    if dirty:
        items.append("Edited but not yet validated: " + ", ".join(dirty[:8]))
    if session.blockers:
        items.append("Recorded blocker(s) must be reported to the user: "
                     + "; ".join(blocker["reason"] for blocker in session.blockers[:3]))
    return items


def next_actions(session: TaskSession) -> list[str]:
    phase = session.phase

    if phase is Phase.ORIENT:
        return [
            "task(action='orient') - detect project type, toolchain and git state.",
            "git_state(action='status') - know what is already uncommitted before you change anything.",
            "find_references(symbol_name=<the main symbol in the goal>, context_lines=2) - start locating the work.",
        ]

    if phase is Phase.EXPLORE:
        return [
            "find_references(...) for each remaining symbol, route, model, schema or config key in the goal.",
            "impact(paths=[...]) - find dependents, related schemas/migrations/config that must change too.",
            "read_files(paths=[...], start_line=, end_line=) once you know exactly where to look.",
        ]

    if phase is Phase.READ:
        unread = [path for path in session.searched_files() if path not in session.read_files()]
        actions = ["edit(...) using a localized line-range or old_text/new_text change."]
        if unread:
            actions.insert(0, f"read_files on the remaining candidate(s): {', '.join(unread[:4])}")
        actions.append("Keep the edit minimal; do not reformat or rewrite untouched code.")
        return actions

    if phase is Phase.EDIT:
        return [
            "edit(...) - one concern per call, smallest possible span.",
            "validate() immediately after the edit; do not batch many unvalidated edits.",
        ]

    if phase is Phase.VALIDATE:
        dirty = session.dirty_files()
        last = session.last_validation()
        if dirty or last is None or not last.get("passed"):
            return [
                f"validate() - derives and runs the project's own checks for {', '.join(dirty[:4]) or 'the change'}.",
                "If you want to see the plan first: validate(dry_run=True).",
            ]
        actions = []
        if (last.get("depth") or "standard") != "full":
            actions.append(
                f"validate(depth='full') - the last run was depth='{last.get('depth')}', "
                "so the test suite has not been executed yet."
            )
        actions.append("task(action='complete') to close the task.")
        actions.append("git_state(action='diff') to review exactly what changed before reporting.")
        return actions

    if phase is Phase.DIAGNOSE:
        diagnosis = session.last_diagnosis or {}
        actions: list[str] = []
        if diagnosis.get("classification") == "environment":
            actions.append("run(command=...) each of the investigate commands to confirm the environment cause.")
            actions.extend(f"run(command={command!r})" for command in (diagnosis.get("investigate") or [])[:3])
            actions.append("task(action='block', reason=...) if it is a genuine external limitation.")
        else:
            locations = diagnosis.get("locations") or []
            if locations:
                actions.append(
                    "read_files around the reported failure: "
                    + ", ".join(f"{item['file']}:{item['line']}" for item in locations[:3])
                )
            actions.append("run(command=<the failing command with more verbosity>) if the cause is still unclear.")
            actions.append("Then make a localized repair edit and validate() again.")
        return actions

    if phase is Phase.REPAIR:
        return [
            "read_files the exact failing region (line numbers from the diagnosis) before editing.",
            "edit(...) - repair only the diagnosed cause; do not opportunistically rewrite other code.",
            "validate() again. The loop ends only on a pass or a recorded blocker.",
        ]

    if phase is Phase.BLOCKED:
        return [
            "Report the blocker and the evidence to the user.",
            "task(action='status') to review what was gathered.",
            "task(action='unblock') only if new evidence shows it was not an external limitation.",
        ]

    if phase is Phase.DONE:
        return [
            "task(action='status') for the final ledger.",
            "git_state(action='diff') to review exactly what changed before reporting to the user.",
            "task(action='start', goal=...) to begin the next task.",
        ]

    return ["task(action='start', goal='...') to begin."]


def refusal(session: TaskSession, tool: str, reason: str, required: list[str], **extra) -> dict:
    """A structured 'not yet' that teaches instead of just failing."""
    payload = {
        "blocked": True,
        "tool": tool,
        "reason": reason,
        "required_actions": required,
        "workflow": envelope(session, tool),
    }
    payload.update(extra)
    return payload
