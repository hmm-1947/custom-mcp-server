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
        expected_end_text: str | None = None,
        preview_matches: bool = False,
    ) -> dict:
        """Create or edit a file with optional context and structural validation.

        PREFER LINE-RANGE EDITS to save tokens: after reading a file with
        read_files (which shows line numbers), call this with start_line,
        end_line, and new_text set to just the replacement code. You do NOT
        need to reproduce the old code anywhere — only the new code. This is
        far cheaper than old_text/new_text matching, which requires writing
        out the entire block being replaced verbatim just to identify it.

        Use old_text/new_text only when you don't have reliable line numbers
        (e.g. editing based on a search result without a full file read) or
        when the same snippet might appear in multiple places and you want
        preview_matches=True to disambiguate first.

        For a brand-new file, set create_if_missing=True and either content
        (the whole file) or start_line=1/end_line=1/new_text=<whole file>.

        Line-range edits validate known brace-based languages and return 15
        surrounding lines by default. Supply expected_start_text and/or
        expected_end_text to reject a stale line range before making that edit.
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

        is_empty_existing = target.exists() and target.stat().st_size == 0
        # A "whole file write" can arrive via `content`, or via the new-file
        # fallback pattern of start_line/end_line/new_text with no old_text or
        # function_name. This must be recognized whenever the target doesn't
        # exist yet (any start_line value), or new_text is silently dropped and
        # an empty file gets created while the tool reports success.
        creating_new_file = not target.exists()
        fallback_new_text = (
            new_text if (creating_new_file and start_line is not None and not old_text and not function_name)
            else None
        )
        effective_content = content or fallback_new_text or ""
        whole_file_write = bool(effective_content) and not old_text and not function_name

        if creating_new_file or (whole_file_write and (is_empty_existing or create_if_missing)):
            if creating_new_file and not create_if_missing:
                raise FileNotFoundError(path)
            if creating_new_file and not effective_content and (old_text or function_name):
                raise ValueError(
                    f"{path} does not exist and create_if_missing=True, but old_text/function_name "
                    "were given instead of new_text/content. A brand-new file has nothing to match "
                    "against — pass new_text (with start_line/end_line) or content for new files."
                )
            proposed = effective_content
            affected_start = 1
            affected_end = max(1, effective_content.count("\n") + 1)
            message = f"Created {path}" if creating_new_file else f"Overwrote {path}"


        elif is_empty_existing and start_line is not None:
            proposed = new_text if new_text is not None else ""
            affected_start = 1
            affected_end = max(1, proposed.count("\n") + 1)
            message = f"Wrote content to empty file {path}"
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

                actual_end = lines[end_line - 1]
                if expected_end_text is not None and expected_end_text not in actual_end:
                    return {
                        "message": "Line-range edit aborted: end line no longer matches expected text",
                        "line": end_line,
                        "expected": expected_end_text,
                        "actual": actual_end,
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
