"""Partial reads.

Reading is how an edit earns the right to happen: the harness refuses to edit a
file it has no record of you reading, and refuses again if the file changed on
disk afterwards. Every read is recorded with the line ranges it covered and a
content digest taken at that moment.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from config import resolve_path
from harness import ripgrep
from ._common import context, relative, respond, tool_schema

READ_SCHEMA = tool_schema({
    "files": {"type": "object", "additionalProperties": True},
    "message": {"type": "string"},
})

DEFAULT_SPAN = 400


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=READ_SCHEMA)
    def read_files(
        workspace: str,
        paths: list[str],
        start_line: int | None = None,
        end_line: int | None = None,
        around_line: int | None = None,
        window: int = 40,
        max_chars_per_file: int = 20000,
    ) -> dict:
        """Read files with line numbers - a region, not the whole file where possible.

        Give start_line/end_line for a region, or around_line with window to read
        a symmetric window around a reported failure line (the diagnosis in
        validate() returns exactly those line numbers).

        The line numbers shown here are what edit(start_line=, end_line=) expects,
        and each read is recorded so the harness knows what you have actually seen.
        """
        session, root = context(workspace)
        root = Path(root)
        files: dict[str, dict] = {}
        hints: list[str] = []

        if around_line is not None:
            start_line = max(1, around_line - max(1, window))
            end_line = around_line + max(1, window)

        for path in paths:
            target = resolve_path(workspace, path)
            key = relative(root, target)

            if not target.exists():
                files[key] = {"error": "does not exist"}
                hints.append(f"{key} does not exist. Check the path with run(command='ls -la <dir>').")
                continue
            if not target.is_file():
                files[key] = {"error": "not a file"}
                continue
            if target.suffix.lower() in ripgrep.BINARY_SUFFIXES:
                files[key] = {"error": "binary file; inspect it with run() instead"}
                continue

            try:
                text = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                files[key] = {"error": "not UTF-8 text"}
                continue
            except OSError as error:
                files[key] = {"error": str(error)}
                continue

            lines = text.splitlines()
            total = len(lines)
            full = start_line is None and end_line is None

            if full and len(text) > max_chars_per_file:
                shown = text[:max_chars_per_file]
                last = min(total, shown.count("\n") + 1)
                files[key] = {
                    "total_lines": total,
                    "shown_range": [1, last],
                    "truncated": True,
                    "content": _numbered(lines, 1, last),
                    "note": (
                        f"{total - last} more line(s) not shown. Re-read with "
                        f"start_line={last + 1} for the next section."
                    ),
                }
                session.record_read(key, 1, last, False, target)
                hints.append(
                    f"{key} is {total} lines. Read only the region you need - "
                    "find_references gives you the line numbers."
                )
                continue

            if full:
                first, last = 1, max(total, 1)
                session.record_read(key, first, last, True, target)
            else:
                first = max(1, start_line or 1)
                last = min(total, end_line if end_line is not None else min(total, first + DEFAULT_SPAN))
                if first > total:
                    files[key] = {
                        "error": f"start_line {first} is past end of file ({total} lines)",
                        "total_lines": total,
                    }
                    continue
                session.record_read(key, first, last, False, target)

            files[key] = {
                "total_lines": total,
                "shown_range": [first, last],
                "truncated": not full and (first > 1 or last < total),
                "content": _numbered(lines, first, last),
                "lines_before": first - 1,
                "lines_after": max(0, total - last),
            }

        read_now = [key for key, value in files.items() if "content" in value]
        if read_now:
            hints.append(
                "You may now edit " + ", ".join(read_now[:4])
                + " using the line numbers shown. Keep the changed span as small as possible."
            )

        return respond(session, "read_files", {
            "files": files,
            "message": f"Read {len(read_now)} of {len(paths)} requested path(s).",
        }, hints=hints)


def _numbered(lines: list[str], first: int, last: int) -> str:
    if not lines:
        return "[empty file]"
    body = "\n".join(f"{number:>5} | {lines[number - 1]}" for number in range(first, last + 1))
    return f"[lines {first}-{last} of {len(lines)}]\n{body}"
