"""The validation loop.

This is the step that converts "I think the edit is right" into evidence. It
derives the project's own checks, runs them in cheapest-first order, stops at the
first failure, diagnoses that failure, and hands back the concrete next move -
repair the code, or investigate the environment.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from harness import diagnose, project, terminal as runner
from harness.phases import Phase
from ._common import context, respond, tool_schema

VALIDATE_SCHEMA = tool_schema({
    "passed": {"type": "boolean"},
    "attempt": {"type": "integer"},
    "depth": {"type": "string"},
    "plan": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "steps": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "failed_step": {"type": ["string", "null"]},
    "diagnosis": {"type": "object", "additionalProperties": True},
    "skipped": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "edited_files": {"type": "array", "items": {"type": "string"}},
    "message": {"type": "string"},
})


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=VALIDATE_SCHEMA)
    def validate(
        workspace: str,
        depth: str = "standard",
        commands: list[str] | None = None,
        paths: list[str] | None = None,
        dry_run: bool = False,
        stop_on_first_failure: bool = True,
        timeout: int = 600,
    ) -> dict:
        """Prove the change by executing the project's own checks.

        Derives the plan from this repository (manifests, lockfiles, script
        definitions, test layout) and from what is installed on this machine,
        then runs it cheapest-first, capturing stdout, stderr, exit codes and
        durations.

        depth: "quick"    syntax/compile only
               "standard" syntax + analyzer/lint/type check  (default)
               "full"     everything including the test suite

        Pass commands=[...] to override the derived plan when you know better.
        Pass paths=[...] to scope validation to specific files; by default the
        files edited in this task are used.
        Pass dry_run=True to see the plan without executing it.

        On failure the result includes a diagnosis: whether the cause is the code
        or the environment, the file:line locations to re-read, and the commands
        that would confirm an environment hypothesis. Repair, then call validate()
        again - the loop closes only on a pass or a recorded blocker.
        """
        session, root = context(workspace)
        root = Path(root)
        depth = depth if depth in ("quick", "standard", "full") else "standard"

        targets = paths if paths else session.dirty_files() or session.edited_files()
        if session.profile is None:
            session.profile = project.detect(root)

        if commands:
            plan = [
                project.ValidationStep(
                    name=f"custom {index + 1}", command=command, tier="static",
                    why="explicitly requested by the caller",
                )
                for index, command in enumerate(commands)
            ]
        else:
            plan = project.derive_plan(root, targets, depth=depth, profile=session.profile)

        available = [step for step in plan if step.available]
        skipped = [step.to_dict() for step in plan if not step.available]

        if not available:
            message = (
                "No runnable validation step could be derived. Inspect the project with run() "
                "(check manifests, scripts, installed tools) and pass commands=[...] explicitly."
            )
            session.record_validation({
                "passed": False, "steps": [], "skipped": skipped, "edited_files": targets,
                "failed_step": None, "message": message, "depth": depth,
            })
            return respond(session, "validate", {
                "passed": False, "attempt": len(session.validations), "depth": depth,
                "plan": project.plan_to_dicts(plan), "steps": [], "skipped": skipped,
                "failed_step": None, "edited_files": targets, "message": message,
            }, hints=[
                "run(command='ls -la') and run(command='cat <manifest>') to see what this project offers.",
                "validate(commands=['<the real check for this project>'])",
            ])

        if dry_run:
            return respond(session, "validate", {
                "passed": False, "attempt": len(session.validations), "depth": depth,
                "plan": project.plan_to_dicts(plan), "steps": [], "skipped": skipped,
                "failed_step": None, "edited_files": targets,
                "message": f"Dry run: {len(available)} step(s) would execute, cheapest first.",
                "dry_run": True,
            }, hints=["validate() to actually run this plan."])

        print(f"[harness] validate(depth={depth}, steps={len(available)}, targets={targets})")

        steps: list[dict] = []
        diagnosis: dict | None = None
        failed_step: str | None = None
        passed = True

        for step in available:
            result = runner.execute(step.command, root, timeout=max(1, int(timeout)))
            record = {
                "name": step.name, "tier": step.tier, "why": step.why,
                "command": result.command, "exit_code": result.exit_code,
                "duration_ms": result.duration_ms, "ok": result.ok,
                "timed_out": result.timed_out,
                "stdout": result.stdout, "stderr": result.stderr,
                "clipped": result.clipped,
            }
            steps.append(record)
            session.record_command(result.to_dict(), purpose=f"validate:{step.name}")

            if result.ok:
                continue

            passed = False
            failed_step = step.name
            diagnosis = diagnose.analyze(
                command=result.command, exit_code=result.exit_code, stdout=result.stdout,
                stderr=result.stderr, timed_out=result.timed_out, root=root,
            )
            record["diagnosis"] = diagnosis
            if stop_on_first_failure:
                break

        run_record = {
            "passed": passed, "steps": steps, "skipped": skipped, "edited_files": targets,
            "failed_step": failed_step, "depth": depth,
        }
        session.record_validation(run_record)
        if diagnosis is not None:
            session.record_diagnosis(diagnosis)

        hints: list[str] = []
        tiers_run = sorted({step["tier"] for step in steps})
        if passed:
            message = (
                f"Validation passed: {len(steps)} step(s) green"
                f" [{', '.join(tiers_run)}]"
                + (f", {len(skipped)} skipped for missing tools" if skipped else "")
                + "."
            )
            if skipped:
                hints.append(
                    "Skipped steps mean parts of the project were not actually checked: "
                    + ", ".join(f"{item['name']} ({item['unavailable_reason']})" for item in skipped[:3])
                    + ". Say so when you report - do not imply those passed."
                )
            if "test" not in tiers_run:
                hints.append(
                    f"No test ran ({', '.join(tiers_run) or 'nothing'} only). "
                    "validate(depth='full') if this project has a suite that should back the change."
                )
            hints.append("task(action='complete') to close the task.")
        else:
            message = f"Validation failed at '{failed_step}'. {(diagnosis or {}).get('summary', '')}".strip()
            if diagnosis:
                hints.append(diagnosis["next"])
                if diagnosis["classification"] == "environment":
                    hints.extend(f"run(command={item!r})" for item in diagnosis["investigate"][:3])
                for location in (diagnosis.get("locations") or [])[:2]:
                    hints.append(
                        f"read_files(paths=['{location['file']}'], "
                        f"start_line={max(1, location['line'] - 15)}, end_line={location['line'] + 15})"
                    )
            if session.repair_attempts >= 4:
                hints.append(
                    f"{session.repair_attempts} failed attempts on this task. Stop repeating the same repair: "
                    "gather fresh evidence with run(), or record a blocker with task(action='block')."
                )
            if session.phase is Phase.DIAGNOSE and diagnosis and diagnosis["classification"] == "code":
                session.phase = Phase.REPAIR

        return respond(session, "validate", {
            "passed": passed, "attempt": len(session.validations), "depth": depth,
            "plan": project.plan_to_dicts(plan), "steps": steps, "skipped": skipped,
            "failed_step": failed_step, "diagnosis": diagnosis,
            "edited_files": targets, "message": message,
        }, hints=hints)
