"""The workflow driver.

`task` is the tool that makes this server a harness. It owns the task lifecycle
and is the only place the phase machine is manipulated deliberately rather than
as a side effect of doing work.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastmcp import FastMCP

from config import get_workspace
from harness import gitstate, project, terminal
from harness.guidance import envelope, next_actions, outstanding
from harness.phases import GOALS, ORDER, Phase
from harness.session import SESSIONS
from ._common import blocked, respond, tool_schema

ACTIONS = ("start", "status", "orient", "next", "note", "block", "unblock", "complete", "abandon")

TASK_SCHEMA = tool_schema({
    "action": {"type": "string"},
    "message": {"type": "string"},
    "ledger": {"type": "object", "additionalProperties": True},
    "environment": {"type": "object", "additionalProperties": True},
    "project": {"type": "object", "additionalProperties": True},
    "git": {"type": "object", "additionalProperties": True},
    "validation_plan": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
})


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=TASK_SCHEMA)
    def task(
        workspace: str,
        action: str = "status",
        goal: str = "",
        reason: str = "",
        evidence: list[str] | None = None,
        note: str = "",
        probe_versions: bool = False,
        acknowledge_skipped: bool = False,
    ) -> dict:
        """Drive the engineering workflow. Call this FIRST, before any code tool.

        Actions:
          start    - open a task with a goal. Scopes exploration and validation.
          orient   - detect project type, toolchain, available commands and git
                     state, and derive the validation plan. Do this before
                     planning any change.
          status   - the full ledger: what was searched, read, edited, validated,
                     what is still outstanding.
          next     - just the next required actions.
          note     - record a finding on the task.
          block    - record a genuine external limitation with evidence. Use this
                     instead of rewriting working code around an environment fault.
          unblock  - clear blockers when new evidence contradicts them.
          complete - close the task. Refused unless the latest validation passed
                     and nothing was edited after it.
          abandon  - discard the task state.

        Every other tool reports back into this task, so the harness can tell you
        what is still missing at each step.
        """
        action = (action or "status").strip().lower()
        if action not in ACTIONS:
            raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")

        root = get_workspace(workspace)
        print(f"[harness] task(workspace={workspace!r}, action={action!r})")

        if action == "start":
            if not goal.strip():
                raise ValueError("A goal is required: what should be true when this task is done?")
            session = SESSIONS.start(workspace, root, goal.strip())
            return respond(session, "task", {
                "action": "start",
                "message": f"Task {session.task_id} opened.",
                "ledger": session.ledger(),
            }, hints=["task(action='orient') next - establish ground truth before planning."])

        session = SESSIONS.require(workspace, root)

        if action == "orient":
            session.profile = project.detect(root)
            environment = terminal.describe_environment(root, probe_versions=probe_versions)
            git = gitstate.status(root)
            session.git_checked_at = time.time()
            plan = project.derive_plan(root, [], depth="standard", profile=session.profile)
            if session.phase is Phase.ORIENT:
                session.phase = Phase.EXPLORE
            hints = []
            if git.get("conflicts"):
                hints.append("Merge conflicts are present. Resolve them (git_state(action='conflicts')) before editing.")
            if git.get("dirty_count"):
                hints.append(
                    f"{git['dirty_count']} file(s) were already modified before this task. "
                    "Use git_state(action='diff') so you do not mistake pre-existing changes for your own."
                )
            if not plan:
                hints.append(
                    "No validation command could be derived from this project's markers. "
                    "Inspect the repo with run() and set one explicitly via validate(commands=[...])."
                )
            return respond(session, "task", {
                "action": "orient",
                "message": f"Oriented in {root}.",
                "project": session.profile,
                "environment": environment,
                "git": git,
                "validation_plan": project.plan_to_dicts(plan),
                "ledger": session.ledger(),
            }, hints=hints)

        if action == "note":
            if not note.strip():
                raise ValueError("note text is required")
            session.note(note.strip())
            return respond(session, "task", {"action": "note", "message": "Recorded.",
                                             "ledger": session.ledger()})

        if action == "block":
            if not reason.strip():
                raise ValueError("reason is required: what external limitation was hit?")
            if not evidence:
                return blocked(
                    session, "task",
                    "A blocker needs executed evidence, not an assumption.",
                    [
                        "Run the investigate commands from the last diagnosis with run().",
                        "Call task(action='block', reason=..., evidence=['<command> -> <result>', ...]) again.",
                    ],
                )
            session.block(reason.strip(), evidence)
            return respond(session, "task", {
                "action": "block",
                "message": "Blocker recorded. Report it to the user with this evidence.",
                "ledger": session.ledger(),
            })

        if action == "unblock":
            session.blockers.clear()
            session.phase = Phase.VALIDATE if session.dirty_files() else Phase.EXPLORE
            return respond(session, "task", {"action": "unblock", "message": "Blockers cleared.",
                                             "ledger": session.ledger()})

        if action == "complete":
            problems = _completion_problems(session, acknowledge_skipped)
            if problems:
                return blocked(
                    session, "task",
                    "Task cannot be completed yet.",
                    problems,
                    ledger=session.ledger(),
                )
            session.phase = Phase.DONE
            session.completed_at = time.time()
            last = session.last_validation() or {}
            skipped = last.get("skipped") or []
            message = "Task complete: validation passed and nothing was edited after it."
            hints = []
            if skipped:
                message += (
                    f" {len(skipped)} check(s) could not run here, so the change is proven only "
                    "as far as the available tooling allows."
                )
                hints.append(
                    "Tell the user which checks did not run and why: "
                    + "; ".join(f"{item['name']} ({item.get('unavailable_reason')})" for item in skipped[:3])
                )
            return respond(session, "task", {
                "action": "complete",
                "message": message,
                "ledger": session.ledger(),
                "evidence": {
                    "validation_attempts": len(session.validations),
                    "commands": [step.get("command") for step in (last.get("steps") or [])],
                    "unverified_checks": [item.get("name") for item in skipped],
                    "files_edited": session.edited_files(),
                },
            }, hints=hints)

        if action == "abandon":
            SESSIONS.drop(workspace)
            fresh = SESSIONS.require(workspace, root)
            return respond(fresh, "task", {"action": "abandon", "message": "Task state discarded."})

        if action == "next":
            return {
                "action": "next",
                "phase": session.phase.value,
                "phase_goal": GOALS.get(session.phase, ""),
                "outstanding": outstanding(session),
                "next_actions": next_actions(session),
                "workflow": envelope(session, "task"),
            }

        return respond(session, "task", {
            "action": "status",
            "message": f"Task {session.task_id} in phase '{session.phase.value}'.",
            "ledger": session.ledger(),
            "project": session.profile,
            "phases": [phase.value for phase in ORDER],
            "recent_commands": session.commands[-10:],
            "recent_validations": [
                {
                    "attempt": run.get("attempt"), "passed": run.get("passed"),
                    "failed_step": run.get("failed_step"),
                    "steps": [
                        {"name": step.get("name"), "exit_code": step.get("exit_code"),
                         "duration_ms": step.get("duration_ms")}
                        for step in (run.get("steps") or [])
                    ],
                }
                for run in session.validations[-3:]
            ],
            "last_diagnosis": session.last_diagnosis,
            "notes": session.notes,
        })


def _completion_problems(session, acknowledge_skipped: bool = False) -> list[str]:
    problems: list[str] = []
    last = session.last_validation()
    if not session.edited_files():
        problems.append("No file was edited in this task. If nothing needed changing, say so instead of completing.")
    if last is None:
        problems.append("Never validated. Call validate() and let it run the project's checks.")
    elif not last.get("passed"):
        failed = last.get("failed_step") or "a step"
        problems.append(
            f"The last validation failed at {failed}. Diagnose it, repair, and validate again "
            "- or record a blocker with task(action='block')."
        )
    else:
        # A skipped check is not a passed check. Behaviour that was never
        # executed must be surfaced deliberately, not absorbed into "green".
        skipped = last.get("skipped") or []
        tiers_run = {step.get("tier") for step in (last.get("steps") or [])}
        behavioural = [item for item in skipped if item.get("tier") == "test"]
        if behavioural and not acknowledge_skipped and "test" not in tiers_run:
            problems.append(
                "The behavioural checks never ran ("
                + "; ".join(f"{item['name']}: {item.get('unavailable_reason')}" for item in behavioural[:3])
                + "). Either make them runnable, investigate with run(), record the limitation with "
                "task(action='block', reason=..., evidence=[...]), or - if you are prepared to tell the "
                "user the change is only statically verified - call "
                "task(action='complete', acknowledge_skipped=True)."
            )
        elif "test" not in tiers_run and not acknowledge_skipped and last.get("depth") != "full":
            problems.append(
                "Only static checks ran. Call validate(depth='full') to execute the test suite, "
                "or task(action='complete', acknowledge_skipped=True) to close without it."
            )
    dirty = session.dirty_files()
    if dirty:
        problems.append(
            "These files were edited after the last passing validation: "
            + ", ".join(dirty[:8]) + ". Run validate() again."
        )
    stale = session.stale_reads()
    if stale:
        problems.append(
            "These files changed on disk outside the harness: "
            + ", ".join(item["path"] for item in stale[:5])
            + ". Re-read and re-validate."
        )
    if session.blockers:
        problems.append(
            "Blockers are recorded. Report them to the user rather than completing silently, "
            "or clear them with task(action='unblock') if they were resolved."
        )
    return problems
