from fastmcp import FastMCP

from config import resolve_path
from .schemas import READ_FILES_OUTPUT_SCHEMA


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=READ_FILES_OUTPUT_SCHEMA)
    def read_files(
        workspace: str,
        paths: list[str],
        start_line: int | None = None,
        end_line: int | None = None,
        max_chars_per_file: int = 20000,
    ) -> dict[str, str]:
        """Read one or more text files, optionally limited to a line range."""
        result = {}
        for path in paths:
            target = resolve_path(workspace, path)
            if not target.exists():
                result[path] = "ERROR: File does not exist"
                continue
            if not target.is_file():
                result[path] = "ERROR: Not a file"
                continue

            try:
                text = target.read_text(encoding="utf-8")
                if start_line is not None or end_line is not None:
                    lines = text.splitlines()
                    total = len(lines)
                    start = 1 if start_line is None else max(1, start_line)
                    end = total if end_line is None else min(total, end_line)
                    numbered = "\n".join(
                        f"{index:>5} | {lines[index - 1]}"
                        for index in range(start, end + 1)
                    )
                    before = (
                        f"... [{start - 1} line(s) before this range, not shown]\n"
                        if start > 1 else "[start of file]\n"
                    )
                    after = (
                        f"\n... [{total - end} line(s) after this range, not shown]"
                        if end < total else "\n[end of file]"
                    )
                    text = f"[showing lines {start}-{end} of {total} total lines]\n{before}{numbered}{after}"
                elif len(text) > max_chars_per_file:
                    text = (
                        text[:max_chars_per_file]
                        + f"\n... [truncated, {len(text) - max_chars_per_file} more chars. "
                        + "Use start_line/end_line to read further.]"
                    )

                result[path] = text
            except UnicodeDecodeError:
                result[path] = "ERROR: File is not UTF-8 text"
            except Exception as exc:
                result[path] = f"ERROR: {exc}"

        return result
