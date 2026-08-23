"""Reading files with line numbers.

The numbers shown here are the ones `edit(start_line=, end_line=)` expects.
"""

from __future__ import annotations

from fastmcp import FastMCP

from config import resolve_path
from core import search as text_search
from ._common import numbered

DEFAULT_SPAN = 400


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def read(
        paths: list[str],
        workspace: str = "",
        start_line: int | None = None,
        end_line: int | None = None,
        around_line: int | None = None,
        window: int = 60,
        max_chars: int = 20000,
    ) -> dict:
        """Read files, numbered. Prefer a region over a whole file.

        start_line/end_line for a range, or around_line (+ window) for a
        symmetric window around a line of interest. The line numbers returned
        are what `edit` expects.
        """
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            raise ValueError("paths is required")

        if around_line is not None:
            start_line = max(1, around_line - max(1, window))
            end_line = around_line + max(1, window)

        files: dict[str, dict] = {}
        for path in paths:
            target = resolve_path(workspace, path)
            key = path if len(paths) > 1 or workspace else str(target)

            if not target.exists():
                files[key] = {"error": "does not exist"}
                continue
            if not target.is_file():
                files[key] = {"error": "not a file"}
                continue
            if target.suffix.lower() in text_search.BINARY_SUFFIXES:
                files[key] = {"error": "binary file"}
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
            whole = start_line is None and end_line is None

            if whole and len(text) > max_chars:
                last = min(total, text[:max_chars].count("\n") + 1)
                files[key] = {
                    "total_lines": total,
                    "shown": [1, last],
                    "content": numbered(lines, 1, last),
                    "note": f"{total - last} more line(s). Re-read with start_line={last + 1}.",
                }
                continue

            if whole:
                first, last = 1, max(total, 1)
            else:
                first = max(1, start_line or 1)
                last = min(total, end_line if end_line is not None else first + DEFAULT_SPAN)
                if first > total:
                    files[key] = {"error": f"start_line {first} is past end of file",
                                  "total_lines": total}
                    continue

            files[key] = {
                "total_lines": total,
                "shown": [first, last],
                "content": numbered(lines, first, last),
            }

        return {"files": files}
