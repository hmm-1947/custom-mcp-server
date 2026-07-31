"""Impact analysis tool.

A change is not finished when the edited file is correct - it is finished when
everything that depends on it agrees. This surfaces those dependents and records
them on the task, so the harness can keep reminding the model about candidates it
has not yet examined.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from harness import impact as analysis
from ._common import context, relative, respond, tool_schema

IMPACT_SCHEMA = tool_schema({
    "summary": {"type": "string"},
    "method": {"type": "string"},
    "targets": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "ranked_candidates": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
})


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=IMPACT_SCHEMA)
    def impact(
        workspace: str,
        paths: list[str] | None = None,
        include_symbols: bool = True,
        max_files: int = 12,
    ) -> dict:
        """Find what else a change to these files affects.

        Combines three signals:
          * reverse imports  - modules that import the target
          * symbol sites     - references to the functions/classes it defines
          * co-changing files - migrations, schemas, serializers, routes, models,
            API contracts, config/manifests and the tests that pin the old shape

        Candidates are ranked; the highest-scoring ones are the files to open
        first. Anything flagged here that you never read is reported as
        outstanding until you either open it or explicitly rule it out.

        With no paths, the files edited so far in this task are analyzed.
        """
        session, root = context(workspace)
        root = Path(root)

        targets = paths or session.edited_files() or session.read_files()
        if not targets:
            return respond(session, "impact", {
                "summary": "Nothing to analyze yet.",
                "method": "", "targets": [], "ranked_candidates": [],
            }, hints=[
                "find_references(symbol_name=...) first, then impact(paths=[<the files you found>]).",
            ])

        normalized = [relative(root, root / path) for path in targets]
        print(f"[harness] impact(paths={normalized[:max_files]})")
        report = analysis.analyze(root, normalized, include_symbols=include_symbols, max_files=max_files)

        flagged = [item["file"] for item in report["ranked_candidates"][:15]]
        session.impact_candidates = sorted(set(session.impact_candidates) | set(flagged))
        for path in flagged:
            session.record(path).flagged_by_impact = True
        session.record_search("impact", ",".join(normalized[:5]), len(report["ranked_candidates"]), flagged)

        hints: list[str] = []
        if flagged:
            hints.append(
                "Open the top candidates with read_files before editing: " + ", ".join(flagged[:5])
            )
        migrations = [
            item["file"] for item in report["ranked_candidates"]
            if any("migration" in reason for reason in item["reasons"])
        ]
        if migrations:
            hints.append(
                "Migration-related files were flagged: " + ", ".join(migrations[:3])
                + ". A new non-nullable column needs a server_default or a backfill; "
                "a response-shape-only change needs no migration at all."
            )
        tests = [
            item["file"] for item in report["ranked_candidates"]
            if any("test" in reason for reason in item["reasons"])
        ]
        if tests:
            hints.append("Tests pin the old behaviour: " + ", ".join(tests[:3]) + ". Expect to update them.")

        return respond(session, "impact", report, hints=hints)
