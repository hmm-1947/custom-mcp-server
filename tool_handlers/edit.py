from pathlib import Path

from fastmcp import FastMCP

from code_engine.replace import build_function_replacement, fuzzy_find_text
from config import resolve_path
from .schemas import EDIT_OUTPUT_SCHEMA
from .validation import STRUCTURAL_EXTENSIONS, validate_structure as check_structure


LAST_LINE_EDITS: dict[str, tuple[int, int]] = {}


def _line_context(content: str, start_line: int, end_line: int, window: int) -> str:
    lines = content.splitlines()
    if not lines:
        return "[empty file]"
    first = max(1, start_line - window)
    last = min(len(lines), end_line + window)
    body = "\n".join(f"{number:>5} | {lines[number - 1]}" for number in range(first, last + 1))
    return f"[showing lines {first}-{last} of {len(lines)} total lines]\n{body}"


def _should_validate(path: Path, validate: bool | None) -> bool:
    return validate if validate is not None else path.suffix.lower() in STRUCTURAL_EXTENSIONS


def _match_previews(text: str, old_text: str) -> list[dict]:
    """Return every non-overlapping exact match with three surrounding lines."""
    matches = []
    start_index = 0
    while True:
        match_index = text.find(old_text, start_index)
        if match_index < 0:
            return matches
        start_line = text.count("\n", 0, match_index) + 1
        end_line = start_line + old_text.count("\n")
        matches.append({
            "index": len(matches) + 1,
            "start_line": start_line,
            "end_line": end_line,
            "context": _line_context(text, start_line, end_line, window=3),
        })
        start_index = match_index + len(old_text)


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=EDIT_OUTPUT_SCHEMA)
    def edit(
        workspace: str,
        path: str,
        function_name: str | None = None,
        new_function: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        replace_all: bool = True,
        create_if_missing: bool = False,
        content: str = "",
        description: str | None = None,
        context_lines: int = 0,
        include_context: bool | None = None,
        validate_structure: bool | None = None,
        dry_run: bool = False,
        expected_start_text: str | None = None,
        preview_matches: bool = False,
    ) -> dict:
        """Create or edit a file with optional context and structural validation.

        Line-range edits validate known brace-based languages and return 15
        surrounding lines by default. Supply expected_start_text to reject a
        stale line number before making that edit.
        """
        kind = (
            "function_replace" if function_name else "line_replace"
            if start_line is not None else "text_replace" if old_text else "create/no-op"
        )
        print(f"[tool] edit(workspace={workspace!r}, path={path!r}, kind={kind})")

        if context_lines < 0:
            raise ValueError("context_lines must be zero or greater")

        target = resolve_path(workspace, path)
        previous_line_edit = LAST_LINE_EDITS.get(str(target))

        if not target.exists():
            if not create_if_missing:
                raise FileNotFoundError(path)
            proposed = content
            affected_start = 1
            affected_end = max(1, content.count("\n") + 1)
            message = f"Created {path}"
        else:
            text = target.read_text(encoding="utf-8")

            if function_name:
                if new_function is None:
                    raise ValueError("new_function is required")
                proposed_bytes, affected_start, affected_end = build_function_replacement(
                    str(target), function_name, new_function,
                )
                proposed = proposed_bytes.decode("utf-8")
                message = f"Replaced function '{function_name}'"
            elif start_line is not None:
                if end_line is None or new_text is None:
                    raise ValueError("end_line and new_text are required with start_line")

                lines = text.splitlines()
                if start_line < 1 or end_line < start_line or end_line > len(lines):
                    raise ValueError(f"Invalid line range {start_line}-{end_line} for file with {len(lines)} lines")
                actual_start = lines[start_line - 1]
                if expected_start_text is not None and expected_start_text not in actual_start:
                    return {
                        "message": "Line-range edit aborted: start line no longer matches expected text",
                        "line": start_line,
                        "expected": expected_start_text,
                        "actual": actual_start,
                    }

                had_trailing_newline = text.endswith("\n")
                source_lines = text.split("\n")
                if had_trailing_newline:
                    source_lines.pop()
                proposed = "\n".join(
                    source_lines[:start_line - 1] + new_text.split("\n") + source_lines[end_line:]
                ) + ("\n" if had_trailing_newline else "")
                affected_start = start_line
                affected_end = start_line + new_text.count("\n")
                message = f"Replaced lines {start_line}-{end_line}"
            elif old_text:
                count = text.count(old_text)
                if preview_matches:
                    if count:
                        previews = _match_previews(text, old_text)
                        return {
                            "message": f"Preview: found {count} exact match(es); no edit applied",
                            "matches": previews,
                            "match_count": count,
                            "preview_matches": True,
                        }

                    found = fuzzy_find_text(text, old_text)
                    if found is None:
                        return {"message": "Preview: text not found", "matches": [], "match_count": 0}
                    replacement_start, replacement_end = found
                    start_line = text.count("\n", 0, replacement_start) + 1
                    end_line = text.count("\n", 0, replacement_end) + 1
                    return {
                        "message": "Preview: found 1 fuzzy whitespace match; no edit applied",
                        "matches": [{
                            "index": 1,
                            "start_line": start_line,
                            "end_line": end_line,
                            "context": _line_context(text, start_line, end_line, window=3),
                        }],
                        "match_count": 1,
                        "preview_matches": True,
                    }

                if new_text is None:
                    raise ValueError("new_text is required")
                if count == 0:
                    found = fuzzy_find_text(text, old_text)
                    if found is None:
                        return {"message": "Text not found"}
                    replacement_start, replacement_end = found
                    proposed = text[:replacement_start] + new_text + text[replacement_end:]
                    message = "Replaced 1 occurrence (fuzzy whitespace match)"
                else:
                    replacement_start = text.index(old_text)
                    proposed = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
                    message = f"Replaced {count if replace_all else 1} occurrence(s)"
                affected_start = text.count("\n", 0, replacement_start) + 1
                affected_end = affected_start + new_text.count("\n")
            else:
                return {"message": "Nothing to edit. Provide a replacement or create_if_missing=True."}

        if _should_validate(target, validate_structure):
            validation_error = check_structure(target, proposed)
            if validation_error is not None:
                return {"message": "Edit not applied: structural validation failed", "validation": validation_error}

        line_edit = start_line is not None
        should_include_context = (
            include_context if include_context is not None else context_lines > 0 or line_edit
        )
        context_window = context_lines or 15
        response = {"message": f"Dry run: {message}" if dry_run else message}
        if old_text and 'count' in locals() and count > 1:
            response["warning"] = (
                f"old_text occurs {count} times in this file; "
                + ("all matches were replaced" if replace_all else "only the first match was replaced")
                + ". Use preview_matches=True to inspect every match before editing."
            )
        if should_include_context:
            response["context"] = _line_context(proposed, affected_start, affected_end, context_window)
        if previous_line_edit is not None:
            response["previous_line_edit"] = {
                "start_line": previous_line_edit[0],
                "end_line": previous_line_edit[1],
            }
        response["affected_lines"] = {"start": affected_start, "end": affected_end}

        if dry_run:
            response["dry_run"] = True
            return response

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(proposed, encoding="utf-8")
        if line_edit:
            LAST_LINE_EDITS[str(target)] = (affected_start, affected_end)
        return response
